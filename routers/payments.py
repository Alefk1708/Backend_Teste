"""
Router de Pagamentos — integrado com Mercado Pago.

Fluxo PIX:
  1. POST /payments/pix          → cria pagamento no MP, salva no banco, retorna QR code
  2. POST /payments/webhook      → MP notifica que o pix foi pago → confirma appointment
  3. GET  /payments/{id}/status  → frontend consulta status (polling de segurança)

Fluxo Cartão:
  1. Frontend tokeniza o cartão com o SDK JS do Mercado Pago
  2. POST /payments/card         → envia token + dados → MP processa
  3. POST /payments/webhook      → MP notifica status final

Reembolso:
  POST /payments/{id}/refund     → chama MP para estornar + cancela appointment
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Header, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from database import get_db
from core.security import get_current_user
from models.models import Payment, Appointment, User, Clinic, Notification, PaymentIdempotency
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta
import uuid
import json
import logging

from routers.websocket import manager
from services.mercadopago_service import (
    create_pix_payment,
    create_card_payment,
    refund_payment as mp_refund,
    get_payment_status,
    validate_webhook_signature,
)

router = APIRouter(prefix="/payments", tags=["payments"])


# ==========================================
# SCHEMAS
# ==========================================

class PixPaymentRequest(BaseModel):
    appointment_id: str

class CardPaymentRequest(BaseModel):
    appointment_id: str
    token: str              # Token gerado pelo SDK JS do Mercado Pago no frontend
    installments: int = 1
    payment_method_id: str  # "visa", "master", "elo", etc. — deve vir do SDK JS do MP
    issuer_id: str          # ID do banco emissor — OBRIGATÓRIO, deve vir do SDK JS do MP
    idempotency_key: Optional[str] = None  # UUID v4 gerado pelo frontend antes do envio


# ==========================================
# HELPERS INTERNOS
# ==========================================

def _confirm_appointment_paid(db: Session, appointment: Appointment, payment: Payment):
    """Confirma um agendamento após pagamento aprovado."""
    appointment.status = "confirmed"
    payment.status = "completed"
    payment.paid_at = datetime.utcnow()

    notification = Notification(
        id=str(uuid.uuid4()),
        user_id=str(appointment.patient_id),
        user_type="paciente",
        title="Pagamento confirmado!",
        message=f"Seu pagamento de R${payment.amount:.2f} foi confirmado. Consulta agendada!",
        type="payment_confirmed",
        data=json.dumps({
            "appointment_id": str(appointment.id),
            "payment_id": str(payment.id),
            "amount": float(payment.amount),
        }),
    )
    db.add(notification)
    db.commit()


async def _confirm_slot_after_payment(appointment_id: str, db: Session):
    """Confirma o slot vinculado ao agendamento após pagamento aprovado."""
    try:
        from routers.slots import confirm_slot_payment
        await confirm_slot_payment(appointment_id, db)
    except Exception as exc:
        logging.warning("[payments] Falha ao confirmar slot: %s", exc)


async def _notify_patient_ws(patient_id: str, appointment_id: str, payment_id: str, amount: float):
    """Notifica paciente via WebSocket."""
    await manager.send_to_user(str(patient_id), {
        "type": "payment_confirmed",
        "title": "Pagamento confirmado!",
        "body": f"Seu pagamento de R${amount:.2f} foi confirmado.",
        "data": {
            "appointment_id": str(appointment_id),
            "payment_id": str(payment_id),
            "amount": float(amount),
        },
        "timestamp": datetime.utcnow().isoformat(),
    })


# ==========================================
# PIX
# ==========================================

@router.post("/pix")
async def create_pix(
    data: PixPaymentRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Cria um pagamento PIX via Mercado Pago.
    Retorna o código copia-e-cola e o QR code em base64.
    O agendamento só é confirmado quando o webhook do MP chegar.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "paciente":
        raise HTTPException(status_code=403, detail="Apenas pacientes podem pagar")

    appointment = db.query(Appointment).filter(Appointment.id == data.appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")

    if str(appointment.patient_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Agendamento não pertence a este usuário")

    if appointment.status not in ["awaiting_payment", "pending"]:
        raise HTTPException(
            status_code=400,
            detail=f"Agendamento não está aguardando pagamento (status: {appointment.status})"
        )

    if appointment.total_amount is None:
        raise HTTPException(
            status_code=400,
            detail="Valor do agendamento não definido. Entre em contato com o suporte."
        )

    # ── Lock no agendamento para evitar pagamentos duplicados ────────────────
    # with_for_update() usa SELECT ... FOR UPDATE no PostgreSQL (produção)
    # e é ignorado silenciosamente no SQLite (desenvolvimento) — seguro porque
    # o SQLite usa locking em nível de arquivo (WAL mode) e a UniqueConstraint
    # do PaymentIdempotency serve como proteção extra em dev.
    db.query(Appointment).filter(
        Appointment.id == data.appointment_id
    ).with_for_update().first()

    # Re-buscar o agendamento dentro do lock para garantir estado atualizado
    appointment = db.query(Appointment).filter(
        Appointment.id == data.appointment_id
    ).first()

    if appointment.status not in ["awaiting_payment", "pending"]:
        raise HTTPException(
            status_code=400,
            detail=f"Agendamento não está aguardando pagamento (status: {appointment.status})"
        )

    # Verificar payments existentes dentro do lock
    existing = db.query(Payment).filter(
        Payment.appointment_id == data.appointment_id,
        Payment.status.in_(["pending", "completed"]),
    ).first()

    if existing and existing.status == "completed":
        raise HTTPException(status_code=400, detail="Este agendamento já foi pago")

    if existing and existing.status == "pending" and existing.pix_code:
        # Idempotência: PIX já foi gerado, retornar o mesmo
        return {
            "payment_id": existing.id,
            "status": "pending",
            "pix_code": existing.pix_code,
            "pix_qr_code": existing.pix_qr_code,
            "amount": existing.amount,
            "message": "PIX já gerado, aguardando pagamento",
        }

    # Criar PIX no Mercado Pago
    try:
        mp_result = create_pix_payment(
            appointment_id=str(appointment.id),
            amount=appointment.total_amount,
            patient_email=user.email,
            patient_name=user.name,
            patient_cpf=user.cpf,
            description=f"Dentista Facil - Consulta #{str(appointment.id)[:8]}",
        )
    except ValueError as e:
        import logging
        logging.error(f"[/payments/pix] MP recusou: {e} | user={user.id} | appointment={appointment.id}")
        raise HTTPException(status_code=400, detail=str(e))

    payment = Payment(
        id=str(uuid.uuid4()),
        appointment_id=data.appointment_id,
        amount=appointment.total_amount,
        platform_fee=appointment.platform_fee,
        clinic_amount=appointment.clinic_amount,
        payment_method="pix",
        status="pending",
        external_id=mp_result["external_id"],
        pix_code=mp_result["pix_code"],
        pix_qr_code=mp_result["pix_qr_code"],
    )
    db.add(payment)
    db.commit()

    return {
        "payment_id": payment.id,
        "status": "pending",
        "pix_code": payment.pix_code,
        "pix_qr_code": payment.pix_qr_code,
        "amount": payment.amount,
        "expires_at": mp_result["expires_at"],
        "message": "PIX gerado. Pague dentro de 30 minutos.",
    }


# ==========================================
# CARTÃO DE CRÉDITO / DÉBITO
# ==========================================

@router.post("/card")
async def create_card(
    data: CardPaymentRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    print(data)
    """
    Processa pagamento com cartão via Mercado Pago.
    O frontend gera o token com o SDK JS do MP antes de chamar este endpoint.
    Nunca envie dados brutos do cartão para o backend!
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "paciente":
        raise HTTPException(status_code=403, detail="Apenas pacientes podem pagar")

    appointment = db.query(Appointment).filter(Appointment.id == data.appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")

    if str(appointment.patient_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Agendamento não pertence a este usuário")

    if appointment.total_amount is None:
        raise HTTPException(
            status_code=400,
            detail="Valor do agendamento não definido. Entre em contato com o suporte."
        )

    if not (1 <= data.installments <= 12):
        raise HTTPException(status_code=400, detail="Número de parcelas inválido (deve ser entre 1 e 12)")

    # ── Idempotência: mesma key = mesmo resultado ─────────────────────────────
    # O frontend gera um UUID antes de enviar. Se a key já existe e o pagamento
    # foi concluído, retorna o resultado salvo sem chamar o MP novamente.
    if data.idempotency_key:
        now = datetime.utcnow()
        existing_idem = db.query(PaymentIdempotency).filter(
            PaymentIdempotency.key == data.idempotency_key,
            PaymentIdempotency.expires_at > now,
        ).first()

        if existing_idem:
            if existing_idem.status == "processing":
                # Outra requisição simultânea com a mesma key está em andamento
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "PAYMENT_IN_PROGRESS",
                        "message": "Pagamento já está sendo processado. Aguarde.",
                    },
                )
            if existing_idem.status == "done" and existing_idem.response:
                # Retornar resposta idempotente já armazenada (duplo clique, retry)
                logging.info(
                    "[card] Idempotência: retornando resposta salva | key=%s",
                    data.idempotency_key,
                )
                return json.loads(existing_idem.response)

            # status == "failed" (com ou sem response): a tentativa anterior
            # falhou — reutilizar o mesmo registro atualizando para "processing"
            # em vez de tentar inserir um novo (o que causaria conflito de PK)
            existing_idem.status     = "processing"
            existing_idem.response   = None
            existing_idem.payment_id = None
            existing_idem.expires_at = now + timedelta(minutes=30)
            db.flush()
            idem_record = existing_idem

        else:
            # Primeira vez que esta key aparece — criar o registro
            idem_record = PaymentIdempotency(
                key=data.idempotency_key,
                status="processing",
                created_at=now,
                expires_at=now + timedelta(minutes=30),
            )
            db.add(idem_record)
            try:
                db.flush()
            except Exception:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "PAYMENT_IN_PROGRESS",
                        "message": "Pagamento já está sendo processado. Aguarde.",
                    },
                )
    else:
        idem_record = None

    # ── Lock no agendamento para evitar pagamentos duplicados ────────────────
    # with_for_update() usa SELECT ... FOR UPDATE no PostgreSQL (produção)
    # e é ignorado silenciosamente no SQLite (desenvolvimento).
    db.query(Appointment).filter(
        Appointment.id == data.appointment_id
    ).with_for_update().first()

    # Re-buscar dentro do lock para garantir estado atualizado
    appointment = db.query(Appointment).filter(
        Appointment.id == data.appointment_id
    ).first()

    if appointment.status not in ["awaiting_payment", "pending"]:
        if idem_record:
            idem_record.status = "failed"
            db.commit()
        raise HTTPException(status_code=400, detail="Agendamento não está aguardando pagamento")

    # Verificar pagamento completo dentro do lock
    existing_paid = db.query(Payment).filter(
        Payment.appointment_id == data.appointment_id,
        Payment.status == "completed",
    ).first()
    if existing_paid:
        if idem_record:
            idem_record.status = "failed"
            db.commit()
        raise HTTPException(status_code=400, detail="Agendamento já foi pago")

    # Remover payments "failed" anteriores para liberar a UniqueConstraint
    # (failed = recusado pelo MP — não gera cobrança, pode tentar novamente)
    db.query(Payment).filter(
        Payment.appointment_id == data.appointment_id,
        Payment.status == "failed",
    ).delete(synchronize_session=False)

    try:
        mp_result = create_card_payment(
            appointment_id=str(appointment.id),
            amount=appointment.total_amount,
            token=data.token,
            installments=data.installments,
            patient_email=user.email,
            patient_name=user.name,
            patient_cpf=user.cpf,
            payment_method_id=data.payment_method_id,
            issuer_id=data.issuer_id,
            description=f"Dentista Facil - Consulta #{str(appointment.id)[:8]}",
        )
    except ValueError as e:
        # MP recusou antes mesmo de criar o pagamento — liberar a idem key
        if idem_record:
            idem_record.status = "failed"
            db.commit()
        error_str = str(e)
        if "INTERNATIONAL_NO_INSTALLMENTS" in error_str:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INTERNATIONAL_NO_INSTALLMENTS",
                    "message": (
                        "Este cartão internacional não permite parcelamento. "
                        "Por favor, selecione 1x ou utilize outro método de pagamento."
                    ),
                    "can_retry_with_1x": data.installments > 1,
                },
            )
        if "INSTALLMENTS_NOT_SUPPORTED" in error_str:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INSTALLMENTS_NOT_SUPPORTED",
                    "message": (
                        "Parcelamento não disponível para este cartão. "
                        "Por favor, pague em 1x."
                    ),
                    "can_retry_with_1x": data.installments > 1,
                },
            )
        raise HTTPException(status_code=502, detail=error_str)
    except Exception as e:
        # Erro inesperado — liberar a idem key para não bloquear retentativas
        if idem_record:
            idem_record.status = "failed"
            db.commit()
        logging.exception("[card] Erro inesperado ao chamar MP | appointment=%s", data.appointment_id)
        raise HTTPException(status_code=500, detail="Erro interno ao processar pagamento.")

    # ── Mapeamento completo status_detail → (categoria, mensagem amigável) ────
    # Cobre todos os status_detail documentados pelo Mercado Pago.
    # Ref: https://www.mercadopago.com.br/developers/pt/docs/checkout-api/response-handling/collection-results
    STATUS_DETAIL_MAP = {
        # ── Aprovado ──────────────────────────────────────────────────────────
        "accredited": (
            "approved",
            "Pagamento aprovado! Consulta confirmada.",
        ),
        # ── Pendente / em análise ─────────────────────────────────────────────
        "pending_contingency": (
            "in_process",
            "Pagamento em análise. Você será notificado em até 2 dias úteis.",
        ),
        "pending_review_manual": (
            "in_process",
            "Pagamento em revisão manual. Retorno em até 2 dias úteis.",
        ),
        "pending_waiting_payment": (
            "pending",
            "Aguardando confirmação do pagamento.",
        ),
        "pending_waiting_transfer": (
            "pending",
            "Aguardando transferência bancária.",
        ),
        # ── Recusados — dados incorretos (usuário pode corrigir) ──────────────
        "cc_rejected_bad_filled_security_code": (
            "rejected_fixable",
            "CVV inválido. Confira o código de segurança no verso do cartão.",
        ),
        "cc_rejected_bad_filled_date": (
            "rejected_fixable",
            "Data de validade inválida. Verifique o mês e o ano.",
        ),
        "cc_rejected_bad_filled_card_number": (
            "rejected_fixable",
            "Número do cartão inválido. Confira os 16 dígitos.",
        ),
        "cc_rejected_bad_filled_other": (
            "rejected_fixable",
            "Dado inválido no formulário. Confira as informações e tente novamente.",
        ),
        # ── Recusados — banco bloqueia / limite ───────────────────────────────
        "cc_rejected_call_for_authorize": (
            "rejected_call_bank",
            "Transação bloqueada pelo banco. Ligue para o número no verso do cartão e autorize a compra.",
        ),
        "cc_rejected_insufficient_amount": (
            "rejected_no_funds",
            "Saldo insuficiente. Verifique o limite disponível ou use outro cartão.",
        ),
        "cc_rejected_max_attempts": (
            "rejected_blocked",
            "Tentativas excedidas. Por segurança aguarde 24h ou use outro cartão.",
        ),
        # ── Recusados — cartão bloqueado / problema permanente ────────────────
        "cc_rejected_blacklist": (
            "rejected_blocked",
            "Cartão não autorizado pelo emissor. Use outro cartão ou tente o PIX.",
        ),
        "cc_rejected_card_disabled": (
            "rejected_blocked",
            "Cartão desabilitado. Entre em contato com seu banco.",
        ),
        "cc_rejected_disabled_payment_method": (
            "rejected_blocked",
            "Este tipo de cartão não está habilitado. Tente outro cartão.",
        ),
        "cc_rejected_high_risk": (
            "rejected_blocked",
            "Transação recusada por segurança. Tente outro método de pagamento.",
        ),
        "cc_rejected_card_type_not_allowed": (
            "rejected_blocked",
            "Tipo de cartão não aceito (débito/crédito). Tente com outro cartão.",
        ),
        # ── Recusados — parcelas / configuração ───────────────────────────────
        "cc_rejected_invalid_installments": (
            "rejected_installments",
            "Número de parcelas inválido para este cartão. Tente pagar em 1x.",
        ),
        "cc_rejected_empty_installments": (
            "rejected_installments",
            "Parcelamento não disponível para este cartão. Tente pagar em 1x.",
        ),
        # ── Recusados — duplicidade ───────────────────────────────────────────
        "cc_rejected_duplicated_payment": (
            "rejected_duplicate",
            "Pagamento duplicado detectado. Verifique seus pagamentos antes de tentar novamente.",
        ),
        # ── Genérico ──────────────────────────────────────────────────────────
        "cc_rejected_other_reason": (
            "rejected",
            "Cartão recusado pelo banco. Use outro cartão ou tente o PIX.",
        ),
    }

    mp_status     = mp_result["status"]
    status_detail = mp_result.get("status_detail", "")

    internal_status = {
        "approved":   "completed",
        "in_process": "pending",
        "rejected":   "failed",
        "pending":    "pending",
    }.get(mp_status, "pending")

    payment = Payment(
        id=str(uuid.uuid4()),
        appointment_id=data.appointment_id,
        amount=appointment.total_amount,
        platform_fee=appointment.platform_fee,
        clinic_amount=appointment.clinic_amount,
        payment_method="credit_card",
        status=internal_status,
        external_id=mp_result["external_id"],
        paid_at=datetime.utcnow() if mp_status == "approved" else None,
    )
    db.add(payment)

    # Flush imediato para disparar a UniqueConstraint antes do commit.
    # Se duas requisições simultâneas chegaram aqui (o FOR UPDATE deveria ter
    # impedido, mas este é o fallback), apenas uma vai conseguir o flush.
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        if idem_record:
            idem_record.status = "failed"
            try:
                db.commit()
            except Exception:
                pass
        logging.warning(
            "[card] UniqueConstraint disparada (pagamento duplicado bloqueado pelo banco) "
            "| appointment=%s", data.appointment_id
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code":    "DUPLICATE_PAYMENT_BLOCKED",
                "message": "Este agendamento já possui um pagamento em andamento. Verifique seus agendamentos.",
            },
        )

    # ── Aprovado ──────────────────────────────────────────────────────────────
    if mp_status == "approved":
        _confirm_appointment_paid(db, appointment, payment)
        response_body = {
            "payment_id":    payment.id,
            "status":        "completed",
            "status_detail": status_detail,
            "is_approved":   True,
            "message":       "Pagamento aprovado! Consulta confirmada.",
            "amount":        payment.amount,
        }
        if idem_record:
            idem_record.payment_id = payment.id
            idem_record.status     = "done"
            idem_record.response   = json.dumps(response_body)
        background_tasks.add_task(
            _notify_patient_ws,
            str(user.id),
            str(appointment.id),
            str(payment.id),
            float(payment.amount),
        )
        background_tasks.add_task(
            _confirm_slot_after_payment,
            str(appointment.id),
            db,
        )
        return response_body

    # ── Em análise / pendente ─────────────────────────────────────────────────
    elif mp_status in ["in_process", "pending"]:
        _, friendly_msg = STATUS_DETAIL_MAP.get(
            status_detail,
            ("in_process", "Pagamento em análise. Você será notificado em breve."),
        )
        response_body = {
            "payment_id":    payment.id,
            "status":        mp_status,
            "status_detail": status_detail,
            "is_approved":   False,
            "message":       friendly_msg,
            "amount":        payment.amount,
        }
        if idem_record:
            idem_record.payment_id = payment.id
            idem_record.status     = "done"
            idem_record.response   = json.dumps(response_body)
        db.commit()
        return response_body

    # ── Recusado ──────────────────────────────────────────────────────────────
    else:
        category, friendly_msg = STATUS_DETAIL_MAP.get(
            status_detail,
            ("rejected", "Cartão recusado. Use outro cartão ou tente o PIX."),
        )
        user_fixable = category == "rejected_fixable"
        suggest_1x   = category == "rejected_installments" and data.installments > 1

        error_detail = {
            "code":          status_detail or "cc_rejected_other_reason",
            "category":      category,
            "message":       friendly_msg,
            "user_fixable":  user_fixable,
            "suggest_1x":    suggest_1x,
        }
        # Pagamento recusado: NÃO salvar na idempotência — usuário pode
        # corrigir os dados e tentar novamente com a mesma chave ou uma nova.
        # Apenas marcar como failed para não bloquear retentativas legítimas.
        if idem_record:
            idem_record.status = "failed"
        db.commit()

        raise HTTPException(status_code=402, detail=error_detail)


# ==========================================
# WEBHOOK DO MERCADO PAGO
# ==========================================

@router.post("/webhook")
async def mercadopago_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    x_signature: Optional[str] = Header(None),
    x_request_id: Optional[str] = Header(None),
):
    """
    Recebe notificações do Mercado Pago.

    CONFIGURAÇÃO EM PRODUÇÃO:
      1. https://www.mercadopago.com.br/developers/panel/app → Webhooks
      2. Adicionar URL: https://SUA-URL/payments/webhook  (evento: Pagamentos)
      3. Copiar o Secret gerado → definir como MERCADOPAGO_WEBHOOK_SECRET
      4. Definir APP_BASE_URL com a URL pública da API

    O MP reenvia o webhook em caso de timeout/5xx, por isso o endpoint é
    idempotente: pagamentos já confirmados retornam 200 sem reprocessar.
    """
    import logging
    import os

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body inválido")

    # ── Validar assinatura HMAC ───────────────────────────────────────────────
    is_production = bool(os.getenv("MERCADOPAGO_WEBHOOK_SECRET", ""))

    if x_signature and x_request_id:
        data_id = str(body.get("data", {}).get("id", ""))
        if not validate_webhook_signature(x_signature, x_request_id, data_id):
            logging.error(
                "[webhook] Assinatura rejeitada | x_request_id=%s | data_id=%s",
                x_request_id, data_id,
            )
            raise HTTPException(status_code=401, detail="Assinatura do webhook inválida")
    elif is_production:
        # Em produção, rejeitar qualquer webhook sem headers de assinatura
        logging.error("[webhook] Headers de assinatura ausentes em produção")
        raise HTTPException(status_code=401, detail="Headers de assinatura obrigatórios em produção")
    else:
        logging.warning("[webhook] Recebido sem assinatura (modo desenvolvimento)")

    # ── Filtrar eventos ───────────────────────────────────────────────────────
    event_type = body.get("type")
    if event_type != "payment":
        return {"status": "ignored", "event": event_type}

    mp_payment_id = str(body.get("data", {}).get("id", ""))
    if not mp_payment_id:
        return {"status": "ignored", "reason": "data.id ausente"}

    logging.info("[webhook] Processando evento payment | mp_id=%s", mp_payment_id)

    # ── Consultar status real no MP (não confiar só no payload) ───────────────
    try:
        mp_data = get_payment_status(mp_payment_id)
    except ValueError as e:
        logging.error("[webhook] Erro ao consultar MP | mp_id=%s | erro=%s", mp_payment_id, e)
        return {"status": "error", "reason": "Pagamento não encontrado no MP"}

    mp_status = mp_data["status"]
    logging.info("[webhook] Status MP | mp_id=%s | status=%s", mp_payment_id, mp_status)

    # ── Localizar Payment no banco ────────────────────────────────────────────
    payment = db.query(Payment).filter(Payment.external_id == mp_payment_id).first()

    if not payment:
        external_reference = str(body.get("data", {}).get("external_reference", ""))
        if external_reference:
            payment = db.query(Payment).filter(
                Payment.appointment_id == external_reference,
                Payment.status == "pending",
            ).first()

    if not payment:
        logging.warning("[webhook] Payment não encontrado no banco | mp_id=%s", mp_payment_id)
        return {"status": "ignored", "reason": "Payment não encontrado no banco"}

    # ── Idempotência ──────────────────────────────────────────────────────────
    if payment.status == "completed":
        logging.info("[webhook] Já confirmado, ignorando | payment_id=%s", payment.id)
        return {"status": "ok", "reason": "Já confirmado"}

    appointment = db.query(Appointment).filter(
        Appointment.id == payment.appointment_id
    ).first()
    if not appointment:
        logging.error("[webhook] Appointment não encontrado | payment_id=%s", payment.id)
        return {"status": "error", "reason": "Agendamento não encontrado"}

    # ── Processar conforme status ─────────────────────────────────────────────
    if mp_status == "approved":
        payment.external_id = mp_payment_id
        _confirm_appointment_paid(db, appointment, payment)

        background_tasks.add_task(
            _notify_patient_ws,
            str(appointment.patient_id),
            str(appointment.id),
            str(payment.id),
            float(payment.amount),
        )
        background_tasks.add_task(
            manager.send_to_user,
            str(appointment.clinic_id),
            {
                "type": "appointment_confirmed",
                "title": "Nova consulta confirmada!",
                "body": "Um paciente confirmou uma consulta.",
                "data": {"appointment_id": str(appointment.id)},
                "timestamp": datetime.utcnow().isoformat(),
            },
        )
        background_tasks.add_task(
            _confirm_slot_after_payment,
            str(appointment.id),
            db,
        )
        logging.info("[webhook] Pagamento aprovado e consulta confirmada | payment_id=%s", payment.id)

    elif mp_status in ["rejected", "cancelled"]:
        payment.status = "failed"
        db.commit()
        logging.info("[webhook] Pagamento %s | payment_id=%s", mp_status, payment.id)

    elif mp_status in ["refunded", "charged_back"]:
        payment.status = "refunded"
        payment.refunded_at = datetime.utcnow()
        appointment.status = "cancelled"
        appointment.cancellation_reason = "Pagamento estornado"
        db.commit()
        logging.info("[webhook] Pagamento estornado | payment_id=%s", payment.id)

    else:
        logging.info("[webhook] Status intermediário '%s' | payment_id=%s", mp_status, payment.id)

    return {"status": "ok"}


# ==========================================
# CONSULTAR STATUS
# ==========================================

@router.get("/{payment_id}/status")
def check_payment_status(
    payment_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Consulta status de um pagamento. Use para polling enquanto aguarda PIX."""
    user = current_user["user"]

    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Pagamento nao encontrado")

    appointment = db.query(Appointment).filter(
        Appointment.id == payment.appointment_id
    ).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Agendamento nao encontrado")

    user_type = current_user["payload"]["type"]
    if user_type == "paciente" and str(appointment.patient_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Sem permissao")
    if user_type == "clinica" and str(appointment.clinic_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Sem permissao")

    return {
        "payment_id": payment.id,
        "status": payment.status,
        "amount": payment.amount,
        "paid_at": payment.paid_at,
        "method": payment.payment_method,
        "pix_code": payment.pix_code if payment.payment_method == "pix" else None,
        "pix_qr_code": payment.pix_qr_code if payment.payment_method == "pix" else None,
        "appointment_status": appointment.status,
    }


# ==========================================
# REEMBOLSO
# ==========================================

@router.post("/{payment_id}/refund")
def refund_payment(
    payment_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Solicita reembolso de um pagamento aprovado via Mercado Pago."""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]

    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Pagamento nao encontrado")

    appointment = db.query(Appointment).filter(
        Appointment.id == payment.appointment_id
    ).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Agendamento nao encontrado")

    if user_type == "paciente" and str(appointment.patient_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Sem permissao")

    if payment.status != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Pagamento com status '{payment.status}' nao pode ser reembolsado"
        )

    if payment.external_id:
        try:
            mp_refund(payment.external_id)
        except ValueError as e:
            raise HTTPException(status_code=502, detail=f"Erro no reembolso: {str(e)}")

    payment.status = "refunded"
    payment.refunded_at = datetime.utcnow()
    appointment.status = "cancelled"
    appointment.cancellation_reason = "Reembolso solicitado"
    db.commit()

    return {"message": "Reembolso solicitado. O valor retorna em ate 10 dias uteis."}


# ==========================================
# HISTORICO DE TRANSACOES
# ==========================================

@router.get("/transactions")
def get_transactions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    clinic_id: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = current_user["user"]
    user_type = current_user["payload"]["type"]

    query = db.query(Payment, Appointment).join(Appointment)

    if user_type == "clinica":
        query = query.filter(Appointment.clinic_id == user.id)
    elif clinic_id and user_type == "admin":
        query = query.filter(Appointment.clinic_id == clinic_id)
    elif user_type == "paciente":
        query = query.filter(Appointment.patient_id == user.id)

    if start_date:
        query = query.filter(Payment.created_at >= start_date)
    if end_date:
        query = query.filter(Payment.created_at <= end_date)

    payments = query.order_by(Payment.created_at.desc()).all()

    return [
        {
            "id": pay.Payment.id,
            "amount": pay.Payment.amount,
            "platform_fee": pay.Payment.platform_fee,
            "clinic_amount": pay.Payment.clinic_amount,
            "status": pay.Payment.status,
            "method": pay.Payment.payment_method,
            "created_at": pay.Payment.created_at,
            "paid_at": pay.Payment.paid_at,
            "appointment_id": pay.Appointment.id,
            "patient_id": pay.Appointment.patient_id if user_type in ["clinica", "admin"] else None,
        }
        for pay in payments
    ]