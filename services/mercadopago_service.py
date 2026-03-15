"""
Serviço de integração com Mercado Pago via API REST direta.
Usamos httpx em vez do SDK oficial para evitar o bug
"http is unavailable for request create_ti" do SDK Python v2.x.

Docs: https://www.mercadopago.com.br/developers/pt/reference
"""

import httpx
import os
import hmac
import hashlib
import logging
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

ACCESS_TOKEN  = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "")
WEBHOOK_SECRET = os.getenv("MERCADOPAGO_WEBHOOK_SECRET", "")
APP_BASE_URL  = os.getenv("APP_BASE_URL", "https://seusite.com")

MP_BASE = "https://api.mercadopago.com"

def _headers():
    if not ACCESS_TOKEN:
        raise RuntimeError("MERCADOPAGO_ACCESS_TOKEN não definido no .env")
    return {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type":  "application/json",
        "X-Idempotency-Key": "",  # preenchido por função quando necessário
    }

def _post(path: str, payload: dict, idempotency_key: str = "") -> dict:
    """POST autenticado na API do MP. Levanta ValueError em caso de erro."""
    headers = _headers()
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key

    with httpx.Client(timeout=30) as client:
        resp = client.post(f"{MP_BASE}{path}", json=payload, headers=headers)

    body = resp.json()
    if resp.status_code not in (200, 201):
        msg = body.get("message", "Erro desconhecido no Mercado Pago")
        causes = body.get("cause", [])
        cause_str = " | ".join(
            f"code={c.get('code')} desc={c.get('description')}"
            for c in causes if isinstance(c, dict)
        ) if causes else ""
        full = f"{msg}{' | ' + cause_str if cause_str else ''}"
        logging.error("[MP] POST %s → %s | %s", path, resp.status_code, full)
        raise _map_mp_error(body, resp.status_code, full)

    return body


def _get(path: str) -> dict:
    """GET autenticado na API do MP."""
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{MP_BASE}{path}", headers=_headers())
    body = resp.json()
    if resp.status_code != 200:
        raise ValueError(f"MP GET {path} → {resp.status_code}: {body.get('message','?')}")
    return body


def _map_mp_error(body: dict, status: int, full_msg: str) -> ValueError:
    """Converte erros conhecidos do MP em mensagens amigáveis."""
    causes = body.get("cause", [])
    codes  = [c.get("code") for c in causes if isinstance(c, dict)]

    if 10114 in codes:
        return ValueError(
            "INTERNATIONAL_NO_INSTALLMENTS: "
            "Este cartão internacional não permite parcelamento. "
            "Por favor, pague em 1x ou use outro método de pagamento."
        )
    if 10102 in codes:
        return ValueError(
            "INSTALLMENTS_NOT_SUPPORTED: "
            "Parcelamento não disponível para este cartão. "
            "Por favor, pague em 1x."
        )
    return ValueError(f"Erro MP ({status}): {full_msg}")


# ══════════════════════════════════════════════════════════════
# PIX
# ══════════════════════════════════════════════════════════════

def create_pix_payment(
    appointment_id: str,
    amount: float,
    patient_email: str,
    patient_name: str,
    patient_cpf: str,
    description: str = "Consulta Dentista Fácil",
) -> dict:
    """
    Cria pagamento PIX. Retorna pix_code, pix_qr_code, external_id, expires_at.
    """
    cpf_clean  = "".join(filter(str.isdigit, patient_cpf))
    name_parts = patient_name.split()
    expires_at = (datetime.utcnow() + timedelta(minutes=30)).strftime(
        "%Y-%m-%dT%H:%M:%S.000-03:00"
    )

    payload = {
        "transaction_amount": round(float(amount), 2),
        "description":        description,
        "payment_method_id":  "pix",
        "date_of_expiration": expires_at,
        "external_reference": appointment_id,
        "notification_url":   f"{APP_BASE_URL}/payments/webhook",
        "payer": {
            "email":      patient_email,
            "first_name": name_parts[0],
            "last_name":  " ".join(name_parts[1:]) or name_parts[0],
            "identification": {"type": "CPF", "number": cpf_clean},
        },
    }

    body = _post("/v1/payments", payload, idempotency_key=appointment_id)

    pix_data = (
        body.get("point_of_interaction", {})
            .get("transaction_data", {})
    )

    return {
        "external_id":  str(body["id"]),
        "pix_code":     pix_data.get("qr_code"),
        "pix_qr_code":  pix_data.get("qr_code_base64"),
        "status":       body["status"],
        "expires_at":   (datetime.utcnow() + timedelta(minutes=30)).isoformat(),
    }


# ══════════════════════════════════════════════════════════════
# CARTÃO
# ══════════════════════════════════════════════════════════════

def create_card_payment(
    appointment_id: str,
    amount: float,
    token: str,
    installments: int,
    patient_email: str,
    patient_name: str,
    patient_cpf: str,
    payment_method_id: str,
    issuer_id: str = None,
    description: str = "Consulta Dentista Fácil",
) -> dict:
    """
    Processa pagamento com cartão. O frontend tokeniza via SDK JS do MP.
    Nunca envie dados brutos do cartão para o backend!
    """
    cpf_clean = "".join(filter(str.isdigit, patient_cpf))

    payload = {
        "transaction_amount": round(float(amount), 2),
        "token":              token,
        "description":        description,
        "installments":       installments,
        "payment_method_id":  payment_method_id,
        "external_reference": appointment_id,
        "notification_url":   f"{APP_BASE_URL}/payments/webhook",
        "payer": {
            "email": patient_email,
            "identification": {"type": "CPF", "number": cpf_clean},
        },
    }
    if issuer_id:
        payload["issuer_id"] = issuer_id

    body = _post("/v1/payments", payload, idempotency_key=appointment_id)

    return {
        "external_id":    str(body["id"]),
        "status":         body["status"],
        "status_detail":  body.get("status_detail"),
        "is_approved":    body["status"] == "approved",
        "amount":         float(body["transaction_amount"]),
        "card_last_four": body.get("card", {}).get("last_four_digits"),
    }


# ══════════════════════════════════════════════════════════════
# REEMBOLSO
# ══════════════════════════════════════════════════════════════

def refund_payment(external_id: str, amount: float = None) -> dict:
    """Reembolsa um pagamento. Se amount=None, faz reembolso total."""
    payload = {}
    if amount:
        payload["amount"] = round(float(amount), 2)

    body = _post(f"/v1/payments/{external_id}/refunds", payload)

    return {
        "refund_id": str(body["id"]),
        "status":    body["status"],
        "amount":    body.get("amount"),
    }


# ══════════════════════════════════════════════════════════════
# CONSULTAR STATUS
# ══════════════════════════════════════════════════════════════

def get_payment_status(external_id: str) -> dict:
    """Consulta status de um pagamento no MP."""
    body = _get(f"/v1/payments/{external_id}")
    return {
        "external_id":   str(body["id"]),
        "status":        body["status"],
        "status_detail": body.get("status_detail"),
        "amount":        float(body["transaction_amount"]),
        "paid_at":       body.get("date_approved"),
    }


# ══════════════════════════════════════════════════════════════
# VALIDAR WEBHOOK
# ══════════════════════════════════════════════════════════════

def validate_webhook_signature(
    x_signature: str, x_request_id: str, data_id: str
) -> bool:
    """
    Valida a assinatura HMAC-SHA256 do webhook do Mercado Pago.
    Manifest: id:<data.id>;request-id:<x-request-id>;ts:<ts>;
    """
    if not WEBHOOK_SECRET:
        logging.warning(
            "[webhook] MERCADOPAGO_WEBHOOK_SECRET não definido — "
            "validação DESATIVADA. Configure em produção!"
        )
        return True

    parts = {}
    for part in x_signature.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            parts[k.strip()] = v.strip()

    ts              = parts.get("ts", "")
    received_digest = parts.get("v1", "")

    if not ts or not received_digest:
        logging.error("[webhook] x-signature mal formatado: %s", x_signature)
        return False

    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    expected = hmac.new(
        WEBHOOK_SECRET.encode(),
        manifest.encode(),
        hashlib.sha256,
    ).hexdigest()

    valid = hmac.compare_digest(expected, received_digest)
    if not valid:
        logging.error(
            "[webhook] Assinatura inválida | manifest=%s | received=%s | expected=%s",
            manifest, received_digest, expected,
        )
    return valid
