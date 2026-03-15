"""
Cancelamento automático de consultas não pagas no prazo de 1 hora.

Funciona de duas formas:
  1. Endpoint GET /appointments/expire-unpaid  → chamado pelo frontend ao abrir o app
                                                 ou periodicamente via cron externo
  2. Função cancel_expired_appointments()      → chamada no startup do FastAPI
     via asyncio.create_task a cada 5 minutos
"""

import asyncio
import logging
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from models.models import Appointment, Notification, User, Clinic, AppointmentSlot
import uuid, json

logger = logging.getLogger(__name__)
router = APIRouter(tags=["payment_expiry"])

PAYMENT_LIMIT_HOURS = 1  # limite em horas para pagar


def _release_expired_slot_reservations(db: Session) -> int:
    """
    Libera reservas temporárias de slots que expiraram (10 minutos).
    Chamado junto com o loop de cancelamento de pagamentos.
    """
    now = datetime.utcnow()
    expired_slots = db.query(AppointmentSlot).filter(
        AppointmentSlot.status == "reserved",
        AppointmentSlot.reservation_expires_at != None,
        AppointmentSlot.reservation_expires_at < now,
    ).all()

    released = 0
    for slot in expired_slots:
        slot.status = "available"
        slot.reserved_by = None
        slot.reserved_at = None
        slot.reservation_expires_at = None
        slot.walk_in_patient_name = None
        released += 1

    if released:
        db.commit()
        logger.info(f"[payment_expiry] {released} reserva(s) de slot expirada(s) liberada(s)")

    return released


def _cancel_expired(db: Session) -> int:
    """
    Cancela consultas em awaiting_payment cujo payment_deadline já passou.
    Retorna o número de consultas canceladas.
    """
    now = datetime.utcnow()

    expired = db.query(Appointment).filter(
        Appointment.status == "awaiting_payment",
        Appointment.payment_deadline != None,
        Appointment.payment_deadline < now,
    ).all()

    cancelled = 0
    for appt in expired:
        appt.status = "cancelled"
        appt.cancellation_reason = (
            f"Cancelada automaticamente: pagamento não realizado "
            f"dentro do prazo de {PAYMENT_LIMIT_HOURS}h."
        )

        # Liberar o slot vinculado (se existir)
        try:
            slot = db.query(AppointmentSlot).filter(
                AppointmentSlot.appointment_id == appt.id,
            ).first()
            if slot and slot.status in ("reserved", "confirmed"):
                slot.status = "available"
                slot.appointment_id = None
                slot.reserved_by = None
                slot.reserved_at = None
                slot.reservation_expires_at = None
        except Exception:
            pass

        # Notificar paciente
        try:
            patient = db.query(User).filter(User.id == appt.patient_id).first()
            if patient:
                db.add(Notification(
                    id=str(uuid.uuid4()),
                    user_id=str(patient.id),
                    user_type="paciente",
                    title="Consulta cancelada ⏰",
                    message=(
                        "Sua consulta foi cancelada pois o pagamento não foi "
                        f"realizado dentro de {PAYMENT_LIMIT_HOURS} hora(s)."
                    ),
                    type="appointment",
                    is_read=False,
                    data=json.dumps({"appointment_id": appt.id}),
                ))
        except Exception:
            pass

        # Notificar clínica
        try:
            clinic = db.query(Clinic).filter(Clinic.id == appt.clinic_id).first()
            if clinic:
                db.add(Notification(
                    id=str(uuid.uuid4()),
                    user_id=str(clinic.id),
                    user_type="clinica",
                    title="Consulta cancelada por falta de pagamento",
                    message=(
                        "Uma consulta foi cancelada automaticamente pois o "
                        f"paciente não pagou dentro de {PAYMENT_LIMIT_HOURS}h."
                    ),
                    type="appointment",
                    is_read=False,
                    data=json.dumps({"appointment_id": appt.id}),
                ))
        except Exception:
            pass

        cancelled += 1

    if cancelled:
        db.commit()
        logger.info(f"[payment_expiry] {cancelled} consulta(s) cancelada(s) por falta de pagamento")

    return cancelled


# ── Endpoint manual (útil para chamar ao abrir o app / cron externo) ─────────
@router.get("/appointments/expire-unpaid")
def expire_unpaid(db: Session = Depends(get_db)):
    """
    Cancela imediatamente todas as consultas com prazo de pagamento vencido.
    Pode ser chamado pelo frontend no startup ou por um cron externo.
    """
    n = _cancel_expired(db)
    return {"cancelled": n, "message": f"{n} consulta(s) cancelada(s)"}


# ── Background task que roda a cada 5 minutos ─────────────────────────────────
async def start_expiry_loop():
    """Inicia o loop de cancelamento automático. Chamar no startup do FastAPI."""
    from database import SessionLocal
    while True:
        try:
            db = SessionLocal()
            _cancel_expired(db)
            _release_expired_slot_reservations(db)
        except Exception as e:
            logger.error(f"[payment_expiry] erro no loop: {e}")
        finally:
            try:
                db.close()
            except Exception:
                pass
        await asyncio.sleep(300)  # a cada 5 minutos
