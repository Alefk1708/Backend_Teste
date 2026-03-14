"""
Serviço de integração com Mercado Pago.
SDK: mercadopago (pip install mercadopago)
Docs: https://www.mercadopago.com.br/developers/pt/docs
"""

import mercadopago
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

ACCESS_TOKEN = os.getenv("MERCADOPAGO_ACCESS_TOKEN")
WEBHOOK_SECRET = os.getenv("MERCADOPAGO_WEBHOOK_SECRET", "")
APP_BASE_URL = os.getenv("APP_BASE_URL", "https://seusite.com")  # URL pública da sua API

def get_sdk():
    if not ACCESS_TOKEN:
        raise RuntimeError("MERCADOPAGO_ACCESS_TOKEN não definido no .env")
    return mercadopago.SDK(ACCESS_TOKEN)


# ==========================================
# PIX
# ==========================================

def create_pix_payment(
    appointment_id: str,
    amount: float,
    patient_email: str,
    patient_name: str,
    patient_cpf: str,
    description: str = "Consulta Dentista Fácil"
) -> dict:
    """
    Cria um pagamento PIX via Mercado Pago.
    Retorna: { pix_code, pix_qr_code_base64, external_id, expires_at }
    """
    sdk = get_sdk()

    # CPF limpo (somente dígitos)
    cpf_clean = "".join(filter(str.isdigit, patient_cpf))

    payment_data = {
        "transaction_amount": round(float(amount), 2),
        "description": description,
        "payment_method_id": "pix",
        "date_of_expiration": (datetime.utcnow() + timedelta(minutes=30)).strftime(
            "%Y-%m-%dT%H:%M:%S.000-03:00"
        ),
        "payer": {
            "email": patient_email,
            "first_name": patient_name.split()[0],
            "last_name": " ".join(patient_name.split()[1:]) or patient_name.split()[0],
            "identification": {
                "type": "CPF",
                "number": cpf_clean,
            },
        },
        "external_reference": appointment_id,  # Nosso ID do agendamento
        "notification_url": f"{APP_BASE_URL}/payments/webhook",
    }

    result = sdk.payment().create(payment_data)
    response = result["response"]

    if result["status"] not in [200, 201]:
        error_msg = response.get("message", "Erro desconhecido no Mercado Pago")
        raise ValueError(f"Erro ao criar PIX: {error_msg}")

    pix_data = response.get("point_of_interaction", {}).get("transaction_data", {})

    return {
        "external_id": str(response["id"]),
        "pix_code": pix_data.get("qr_code"),           # Copia e cola
        "pix_qr_code": pix_data.get("qr_code_base64"), # Imagem base64
        "status": response["status"],                   # "pending"
        "expires_at": (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
    }


# ==========================================
# CARTÃO DE CRÉDITO / DÉBITO
# ==========================================

def create_card_payment(
    appointment_id: str,
    amount: float,
    token: str,                  # Token gerado pelo frontend via MP SDK
    installments: int,
    patient_email: str,
    patient_name: str,
    patient_cpf: str,
    payment_method_id: str,      # ex: "visa", "master", "elo"
    issuer_id: str = None,
    description: str = "Consulta Dentista Fácil"
) -> dict:
    """
    Processa pagamento com cartão.
    O frontend deve usar o SDK JS do MP para tokenizar o cartão antes de enviar.
    Nunca envie dados brutos do cartão para o backend!
    """
    sdk = get_sdk()

    cpf_clean = "".join(filter(str.isdigit, patient_cpf))

    payment_data = {
        "transaction_amount": round(float(amount), 2),
        "token": token,
        "description": description,
        "installments": installments,
        "payment_method_id": payment_method_id,
        "external_reference": appointment_id,
        "notification_url": f"{APP_BASE_URL}/payments/webhook",
        "payer": {
            "email": patient_email,
            "identification": {
                "type": "CPF",
                "number": cpf_clean,
            },
        },
    }

    if issuer_id:
        payment_data["issuer_id"] = issuer_id

    result = sdk.payment().create(payment_data)
    response = result["response"]

    if result["status"] not in [200, 201]:
        import logging
        error_msg = response.get("message", "Erro desconhecido no Mercado Pago")
        causes = response.get("cause", [])
        cause_detail = ""
        cause_codes = []
        if causes:
            cause_codes = [c.get("code") for c in causes if isinstance(c, dict)]
            cause_detail = " | " + ", ".join(
                f"code={c.get('code')} desc={c.get('description')}"
                for c in causes if isinstance(c, dict)
            )
        logging.error(
            f"[MP cartao] HTTP {result['status']} - {error_msg}{cause_detail} | "
            f"method={payment_method_id} issuer={issuer_id} installments={installments} amount={amount} | "
            f"full_response={response}"
        )

        # Erros semânticos conhecidos — retornar mensagem clara para o usuário
        # 10114: cartão internacional não permite parcelamento
        if 10114 in cause_codes:
            raise ValueError(
                "INTERNATIONAL_NO_INSTALLMENTS: "
                "Este cartão internacional não permite parcelamento. "
                "Por favor, pague em 1x ou use outro método de pagamento."
            )
        # 10102: parâmetros inválidos para a bandeira/emissor (ex: ELO sem suporte)
        if 10102 in cause_codes:
            raise ValueError(
                "INSTALLMENTS_NOT_SUPPORTED: "
                "Parcelamento não disponível para este cartão. "
                "Por favor, pague em 1x."
            )

        raise ValueError(f"Erro MP ({result['status']}): {error_msg}{cause_detail}")

    mp_status = response["status"]
    # approved | in_process | rejected | pending
    is_approved = mp_status == "approved"

    return {
        "external_id": str(response["id"]),
        "status": mp_status,
        "status_detail": response.get("status_detail"),
        "is_approved": is_approved,
        "amount": float(response["transaction_amount"]),
        "card_last_four": response.get("card", {}).get("last_four_digits"),
    }


# ==========================================
# REEMBOLSO
# ==========================================

def refund_payment(external_id: str, amount: float = None) -> dict:
    """
    Reembolsa um pagamento aprovado.
    Se amount for None, faz reembolso total.
    """
    sdk = get_sdk()

    refund_data = {}
    if amount:
        refund_data["amount"] = round(float(amount), 2)

    result = sdk.refund().create(external_id, refund_data)
    response = result["response"]

    if result["status"] not in [200, 201]:
        error_msg = response.get("message", "Erro ao reembolsar")
        raise ValueError(f"Erro no reembolso: {error_msg}")

    return {
        "refund_id": str(response["id"]),
        "status": response["status"],
        "amount": response.get("amount"),
    }


# ==========================================
# CONSULTAR STATUS
# ==========================================

def get_payment_status(external_id: str) -> dict:
    """Consulta status atual de um pagamento no Mercado Pago."""
    sdk = get_sdk()
    result = sdk.payment().get(external_id)
    response = result["response"]

    if result["status"] != 200:
        raise ValueError("Pagamento não encontrado no Mercado Pago")

    return {
        "external_id": str(response["id"]),
        "status": response["status"],
        "status_detail": response.get("status_detail"),
        "amount": float(response["transaction_amount"]),
        "paid_at": response.get("date_approved"),
    }


# ==========================================
# VALIDAR WEBHOOK
# ==========================================

def validate_webhook_signature(x_signature: str, x_request_id: str, data_id: str) -> bool:
    """
    Valida a assinatura HMAC-SHA256 enviada pelo Mercado Pago em cada webhook.

    O header x-signature tem o formato:  ts=<timestamp>,v1=<hex_digest>
    O manifest assinado pelo MP é:       id:<data.id>;request-id:<x-request-id>;ts:<ts>;

    ATENÇÃO: o `ts` faz parte do manifest — sem ele o digest nunca bate.

    Ref: https://www.mercadopago.com.br/developers/pt/docs/your-integrations/notifications/webhooks
    """
    import hmac
    import hashlib
    import logging

    if not WEBHOOK_SECRET:
        logging.warning(
            "[webhook] MERCADOPAGO_WEBHOOK_SECRET não definido — "
            "validação de assinatura DESATIVADA. Configure em produção!"
        )
        return True

    # Extrair ts e v1 do header "ts=1234567890,v1=abcdef..."
    parts = {}
    for part in x_signature.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            parts[k.strip()] = v.strip()

    ts = parts.get("ts", "")
    received_digest = parts.get("v1", "")

    if not ts or not received_digest:
        logging.error("[webhook] x-signature mal formatado: %s", x_signature)
        return False

    # Manifest exatamente como o MP assina — ts é obrigatório
    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"

    expected_digest = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    valid = hmac.compare_digest(expected_digest, received_digest)
    if not valid:
        logging.error(
            "[webhook] Assinatura inválida | manifest=%s | received=%s | expected=%s",
            manifest, received_digest, expected_digest,
        )
    return valid