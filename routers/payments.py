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
from database import get_db
from core.security import get_current_user
from models.models import Payment, Appointment, User, Clinic, Notification
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid
import json

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
    payment_method_id: str  # "visa", "master", "elo", etc.
    issuer_id: Optional[str] = None


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

    existing = db.query(Payment).filter(
        Payment.appointment_id == data.appointment_id,
        Payment.status.in_(["pending", "completed"]),
    ).first()

    if existing and existing.status == "completed":
        raise HTTPException(status_code=400, detail="Este agendamento já foi pago")

    if existing and existing.status == "pending" and existing.pix_code:
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
        raise HTTPException(status_code=502, detail=str(e))

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

    if appointment.status not in ["awaiting_payment", "pending"]:
        raise HTTPException(status_code=400, detail="Agendamento não está aguardando pagamento")

    if appointment.total_amount is None:
        raise HTTPException(
            status_code=400,
            detail="Valor do agendamento não definido. Entre em contato com o suporte."
        )

    existing_paid = db.query(Payment).filter(
        Payment.appointment_id == data.appointment_id,
        Payment.status == "completed",
    ).first()
    if existing_paid:
        raise HTTPException(status_code=400, detail="Agendamento já foi pago")

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
        raise HTTPException(status_code=502, detail=str(e))

    mp_status = mp_result["status"]
    internal_status = {
        "approved": "completed",
        "in_process": "pending",
        "rejected": "failed",
        "pending": "pending",
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

    if mp_status == "approved":
        _confirm_appointment_paid(db, appointment, payment)
        background_tasks.add_task(
            _notify_patient_ws,
            str(user.id),
            str(appointment.id),
            str(payment.id),
            float(payment.amount),
        )
        return {
            "payment_id": payment.id,
            "status": "completed",
            "is_approved": True,
            "message": "Pagamento aprovado! Consulta confirmada.",
            "amount": payment.amount,
        }

    elif mp_status == "in_process":
        db.commit()
        return {
            "payment_id": payment.id,
            "status": "in_process",
            "is_approved": False,
            "message": "Pagamento em analise. Voce sera notificado em breve.",
            "amount": payment.amount,
        }

    else:  # rejected
        db.commit()
        status_detail = mp_result.get("status_detail", "")
        detail_messages = {
            "cc_rejected_insufficient_amount": "Saldo insuficiente no cartao.",
            "cc_rejected_bad_filled_card_number": "Numero do cartao invalido.",
            "cc_rejected_bad_filled_date": "Data de validade invalida.",
            "cc_rejected_bad_filled_security_code": "CVV invalido.",
            "cc_rejected_call_for_authorize": "Cartao bloqueado. Contate seu banco.",
            "cc_rejected_blacklist": "Cartao nao autorizado.",
        }
        user_message = detail_messages.get(status_detail, "Cartao recusado. Tente outro cartao ou PIX.")
        raise HTTPException(status_code=402, detail=user_message)


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
    Recebe notificacoes do Mercado Pago.
    Configure em: https://www.mercadopago.com.br/developers/panel/app -> Webhooks
    URL publica: https://SUA-URL/payments/webhook
    Para testes locais use ngrok: ngrok http 8000
    """
    body = await request.json()

    if x_signature and x_request_id:
        data_id = str(body.get("data", {}).get("id", ""))
        if not validate_webhook_signature(x_signature, x_request_id, data_id):
            raise HTTPException(status_code=401, detail="Assinatura do webhook invalida")

    event_type = body.get("type")
    if event_type != "payment":
        return {"status": "ignored"}

    mp_payment_id = str(body.get("data", {}).get("id", ""))
    if not mp_payment_id:
        return {"status": "ignored"}

    try:
        mp_data = get_payment_status(mp_payment_id)
    except ValueError:
        return {"status": "error", "reason": "Pagamento nao encontrado no MP"}

    mp_status = mp_data["status"]

    payment = db.query(Payment).filter(Payment.external_id == mp_payment_id).first()

    # Tenta pelo appointment se nao achou pelo external_id
    if not payment:
        external_reference = str(body.get("data", {}).get("external_reference", ""))
        if external_reference:
            payment = db.query(Payment).filter(
                Payment.appointment_id == external_reference,
                Payment.status == "pending",
            ).first()

    if not payment:
        return {"status": "ignored", "reason": "Pagamento nao encontrado no banco"}

    if payment.status == "completed":
        return {"status": "ok", "reason": "Ja confirmado"}

    appointment = db.query(Appointment).filter(
        Appointment.id == payment.appointment_id
    ).first()

    if not appointment:
        return {"status": "error", "reason": "Agendamento nao encontrado"}

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

    elif mp_status in ["rejected", "cancelled"]:
        payment.status = "failed"
        db.commit()

    elif mp_status in ["refunded", "charged_back"]:
        payment.status = "refunded"
        payment.refunded_at = datetime.utcnow()
        appointment.status = "cancelled"
        appointment.cancellation_reason = "Pagamento estornado"
        db.commit()

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
