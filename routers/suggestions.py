"""
Router de Sugestões de Tratamento
===================================
Fluxo:
  Clínica/Dentista:
    POST   /suggestions/             → cria sugestão para um paciente
    GET    /suggestions/clinic       → lista sugestões feitas pela clínica
    DELETE /suggestions/{id}         → cancela sugestão pendente

  Paciente:
    GET    /suggestions/patient      → lista sugestões pendentes/recentes
    POST   /suggestions/{id}/accept  → aceita (cria agendamento + inicia pagamento)
    POST   /suggestions/{id}/decline → recusa
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from database import get_db
from core.security import get_current_user
from models.models import (
    TreatmentSuggestion, Appointment, Clinic, User, Procedure,
    ClinicProcedure, Payment, Notification
)
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import uuid
import json

router = APIRouter(prefix="/suggestions", tags=["suggestions"])

SUGGESTION_EXPIRY_DAYS = 30

PRIORITY_LABELS = {
    "routine": "Rotina",
    "soon": "Em breve",
    "urgent": "Urgente",
}

PRIORITY_COLORS = {
    "routine": "#4A88F7",
    "soon": "#F59E0B",
    "urgent": "#EF4444",
}


# ==========================================
# SCHEMAS
# ==========================================

class SuggestionCreate(BaseModel):
    appointment_id: str          # Consulta de origem
    procedure_id: str            # Procedimento sugerido
    dentist_name: str            # Nome do dentista
    notes: Optional[str] = None  # Notas / motivo
    priority: str = "routine"    # routine | soon | urgent


class SuggestionAccept(BaseModel):
    scheduled_at: datetime         # Data/hora escolhida pelo paciente
    patient_latitude: float
    patient_longitude: float
    slot_id: Optional[str] = None  # Se informado, o slot é vinculado ao agendamento gerado


# ==========================================
# HELPERS
# ==========================================

def _serialize_suggestion(s: TreatmentSuggestion, include_patient: bool = False) -> dict:
    """Serializa uma sugestão de tratamento para JSON."""
    data = {
        "id": s.id,
        "origin_appointment_id": s.origin_appointment_id,
        "clinic_id": s.clinic_id,
        "clinic_name": s.clinic.name if s.clinic else None,
        "clinic_address": s.clinic.address if s.clinic else None,
        "clinic_phone": s.clinic.phone if s.clinic else None,
        "clinic_avatar": s.clinic.avatar_url if s.clinic else None,
        "procedure_id": s.procedure_id,
        "procedure_name": s.procedure.name if s.procedure else None,
        "procedure_description": s.procedure.description if s.procedure else None,
        "procedure_category": s.procedure.category if s.procedure else None,
        "dentist_name": s.dentist_name,
        "notes": s.notes,
        "priority": s.priority,
        "priority_label": PRIORITY_LABELS.get(s.priority, s.priority),
        "priority_color": PRIORITY_COLORS.get(s.priority, "#4A88F7"),
        "suggested_price": s.suggested_price,
        "status": s.status,
        "resulting_appointment_id": s.resulting_appointment_id,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        "responded_at": s.responded_at.isoformat() if s.responded_at else None,
    }
    if include_patient and s.patient:
        data["patient_name"] = s.patient.name
        data["patient_id"] = s.patient_id
    return data


def _expire_old_suggestions(db: Session):
    """Marca sugestões expiradas como 'expired'."""
    db.query(TreatmentSuggestion).filter(
        TreatmentSuggestion.status == "pending",
        TreatmentSuggestion.expires_at < datetime.utcnow()
    ).update({"status": "expired"})
    db.commit()


def _calculate_financial_split(total_amount: float) -> dict:
    """15% plataforma, 85% clínica (procedimento padrão)."""
    platform_fee = round(total_amount * 0.15, 2)
    clinic_amount = round(total_amount * 0.85, 2)
    return {
        "total_amount": total_amount,
        "platform_fee": platform_fee,
        "clinic_amount": clinic_amount,
    }


# ==========================================
# ENDPOINTS DA CLÍNICA
# ==========================================

@router.post("/")
def create_suggestion(
    data: SuggestionCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Clínica cria uma sugestão de tratamento para o paciente de uma consulta.
    Pode ser criada durante a consulta ou após finalizá-la.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas podem criar sugestões")

    if data.priority not in ("routine", "soon", "urgent"):
        raise HTTPException(status_code=400, detail="Prioridade inválida. Use: routine, soon, urgent")

    # Validar consulta de origem
    appointment = db.query(Appointment).filter(
        Appointment.id == data.appointment_id,
        Appointment.clinic_id == str(user.id),
    ).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Consulta não encontrada ou não pertence a esta clínica")

    if appointment.status not in ("confirmed", "in_progress", "completed"):
        raise HTTPException(
            status_code=400,
            detail=f"Sugestões só podem ser criadas em consultas confirmadas, em andamento ou concluídas (atual: {appointment.status})"
        )

    # Verificar procedimento disponível na clínica
    clinic_proc = db.query(ClinicProcedure).filter(
        ClinicProcedure.clinic_id == str(user.id),
        ClinicProcedure.procedure_id == data.procedure_id,
        ClinicProcedure.is_active == True,
    ).first()
    if not clinic_proc:
        raise HTTPException(status_code=404, detail="Procedimento não disponível nesta clínica")

    procedure = db.query(Procedure).filter(Procedure.id == data.procedure_id).first()
    if not procedure or not procedure.is_active:
        raise HTTPException(status_code=404, detail="Procedimento não encontrado ou inativo")

    # Preço: usa o preço da clínica se definido, senão o global do procedimento
    price = clinic_proc.price if clinic_proc.price else procedure.price
    if not price or price <= 0:
        raise HTTPException(status_code=400, detail="Procedimento sem preço definido. Configure o preço antes de sugerir.")

    suggestion = TreatmentSuggestion(
        id=str(uuid.uuid4()),
        origin_appointment_id=data.appointment_id,
        clinic_id=str(user.id),
        patient_id=str(appointment.patient_id),
        procedure_id=data.procedure_id,
        dentist_name=data.dentist_name.strip(),
        notes=data.notes.strip() if data.notes else None,
        priority=data.priority,
        suggested_price=price,
        status="pending",
        expires_at=datetime.utcnow() + timedelta(days=SUGGESTION_EXPIRY_DAYS),
    )
    db.add(suggestion)

    # Notificar paciente
    notification = Notification(
        id=str(uuid.uuid4()),
        user_id=str(appointment.patient_id),
        user_type="paciente",
        title="Nova sugestão de tratamento! 🦷",
        message=f"Dr(a). {data.dentist_name} de {user.name} sugeriu: {procedure.name}",
        type="treatment_suggestion",
        data=json.dumps({
            "suggestion_id": suggestion.id,
            "procedure_name": procedure.name,
            "priority": data.priority,
            "clinic_name": user.name,
        }),
    )
    db.add(notification)
    db.commit()
    db.refresh(suggestion)

    return {
        "message": "Sugestão criada com sucesso",
        "suggestion": _serialize_suggestion(suggestion, include_patient=True),
    }


@router.get("/clinic")
def get_clinic_suggestions(
    status: Optional[str] = None,
    appointment_id: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Lista sugestões feitas pela clínica."""
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Acesso negado")

    _expire_old_suggestions(db)

    query = db.query(TreatmentSuggestion).filter(
        TreatmentSuggestion.clinic_id == str(user.id)
    )
    if status:
        query = query.filter(TreatmentSuggestion.status == status)
    if appointment_id:
        query = query.filter(TreatmentSuggestion.origin_appointment_id == appointment_id)

    suggestions = query.order_by(TreatmentSuggestion.created_at.desc()).limit(limit).all()

    return [_serialize_suggestion(s, include_patient=True) for s in suggestions]


@router.delete("/{suggestion_id}")
def cancel_suggestion(
    suggestion_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Clínica cancela uma sugestão ainda pendente."""
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Acesso negado")

    suggestion = db.query(TreatmentSuggestion).filter(
        TreatmentSuggestion.id == suggestion_id,
        TreatmentSuggestion.clinic_id == str(user.id),
    ).first()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Sugestão não encontrada")
    if suggestion.status != "pending":
        raise HTTPException(status_code=400, detail=f"Não é possível cancelar uma sugestão com status '{suggestion.status}'")

    suggestion.status = "cancelled"
    db.commit()
    return {"message": "Sugestão cancelada"}


# ==========================================
# ENDPOINTS DO PACIENTE
# ==========================================

@router.get("/patient")
def get_patient_suggestions(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Lista sugestões de tratamento para o paciente logado.
    Por padrão retorna as pendentes. Pode filtrar por status.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "paciente":
        raise HTTPException(status_code=403, detail="Acesso negado")

    _expire_old_suggestions(db)

    query = db.query(TreatmentSuggestion).filter(
        TreatmentSuggestion.patient_id == str(user.id)
    )
    if status:
        query = query.filter(TreatmentSuggestion.status == status)
    else:
        # Padrão: todas que não foram canceladas
        query = query.filter(TreatmentSuggestion.status.in_(["pending", "accepted", "declined", "expired"]))

    suggestions = query.order_by(TreatmentSuggestion.created_at.desc()).all()
    return [_serialize_suggestion(s) for s in suggestions]


@router.get("/patient/pending-count")
def get_pending_count(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Retorna o número de sugestões pendentes para o paciente. Usado para badge/notificação."""
    user = current_user["user"]
    if current_user["payload"]["type"] != "paciente":
        return {"count": 0}

    _expire_old_suggestions(db)

    count = db.query(TreatmentSuggestion).filter(
        TreatmentSuggestion.patient_id == str(user.id),
        TreatmentSuggestion.status == "pending",
    ).count()
    return {"count": count}


@router.post("/{suggestion_id}/accept")
def accept_suggestion(
    suggestion_id: str,
    data: SuggestionAccept,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Paciente aceita a sugestão de tratamento.
    Cria um novo agendamento com status 'awaiting_payment' e retorna
    os dados necessários para o paciente ir direto para o pagamento.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "paciente":
        raise HTTPException(status_code=403, detail="Apenas pacientes podem aceitar sugestões")

    suggestion = db.query(TreatmentSuggestion).filter(
        TreatmentSuggestion.id == suggestion_id,
        TreatmentSuggestion.patient_id == str(user.id),
    ).first()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Sugestão não encontrada")
    if suggestion.status != "pending":
        raise HTTPException(status_code=400, detail=f"Sugestão não está pendente (status: {suggestion.status})")
    if suggestion.expires_at and suggestion.expires_at < datetime.utcnow():
        suggestion.status = "expired"
        db.commit()
        raise HTTPException(status_code=400, detail="Esta sugestão expirou")

    # Calcular divisão financeira
    split = _calculate_financial_split(suggestion.suggested_price)

    # Criar agendamento aguardando pagamento
    new_appointment = Appointment(
        id=str(uuid.uuid4()),
        patient_id=str(user.id),
        clinic_id=suggestion.clinic_id,
        procedure_id=suggestion.procedure_id,
        service_type="procedure",
        status="awaiting_payment",
        type="scheduled",
        scheduled_at=data.scheduled_at,
        patient_latitude=data.patient_latitude,
        patient_longitude=data.patient_longitude,
        description=f"Agendado a partir de sugestão do Dr(a). {suggestion.dentist_name}",
        total_amount=split["total_amount"],
        platform_fee=split["platform_fee"],
        clinic_amount=split["clinic_amount"],
    )
    db.add(new_appointment)

    # Atualizar sugestão
    suggestion.status = "accepted"
    suggestion.resulting_appointment_id = new_appointment.id
    suggestion.responded_at = datetime.utcnow()

    # ── Vincular slot ao agendamento (se slot_id foi fornecido) ──────────
    if data.slot_id:
        from models.models import AppointmentSlot
        slot = db.query(AppointmentSlot).filter(
            AppointmentSlot.id == data.slot_id,
            AppointmentSlot.clinic_id == suggestion.clinic_id,
            AppointmentSlot.reserved_by == str(user.id),
            AppointmentSlot.status == "reserved",
        ).first()
        if slot:
            slot.status = "confirmed"
            slot.appointment_id = new_appointment.id
            slot.reserved_by = None
            slot.reservation_expires_at = None
        # Se slot não encontrado, não bloqueia o fluxo — apenas segue sem slot

    # Notificar clínica
    notification = Notification(
        id=str(uuid.uuid4()),
        user_id=suggestion.clinic_id,
        user_type="clinica",
        title="Sugestão aceita! 🎉",
        message=f"{user.name} aceitou a sugestão de {suggestion.procedure.name if suggestion.procedure else 'tratamento'}",
        type="suggestion_accepted",
        data=json.dumps({
            "suggestion_id": suggestion.id,
            "appointment_id": new_appointment.id,
            "patient_name": user.name,
        }),
    )
    db.add(notification)
    db.commit()
    db.refresh(new_appointment)

    return {
        "message": "Sugestão aceita! Prossiga para o pagamento.",
        "appointment_id": new_appointment.id,
        "amount": split["total_amount"],
        "clinic_name": suggestion.clinic.name if suggestion.clinic else None,
        "procedure_name": suggestion.procedure.name if suggestion.procedure else None,
        "scheduled_at": data.scheduled_at.isoformat(),
    }


@router.post("/{suggestion_id}/decline")
def decline_suggestion(
    suggestion_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Paciente recusa a sugestão de tratamento."""
    user = current_user["user"]
    if current_user["payload"]["type"] != "paciente":
        raise HTTPException(status_code=403, detail="Acesso negado")

    suggestion = db.query(TreatmentSuggestion).filter(
        TreatmentSuggestion.id == suggestion_id,
        TreatmentSuggestion.patient_id == str(user.id),
    ).first()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Sugestão não encontrada")
    if suggestion.status != "pending":
        raise HTTPException(status_code=400, detail="Sugestão não está mais pendente")

    suggestion.status = "declined"
    suggestion.responded_at = datetime.utcnow()
    db.commit()

    return {"message": "Sugestão recusada"}


@router.get("/{suggestion_id}")
def get_suggestion_detail(
    suggestion_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Detalhe de uma sugestão (paciente ou clínica)."""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]

    suggestion = db.query(TreatmentSuggestion).filter(
        TreatmentSuggestion.id == suggestion_id
    ).first()
    if not suggestion:
        raise HTTPException(status_code=404, detail="Sugestão não encontrada")

    # Verificar acesso
    if user_type == "paciente" and str(suggestion.patient_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Sem permissão")
    if user_type == "clinica" and str(suggestion.clinic_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Sem permissão")

    return _serialize_suggestion(suggestion, include_patient=(user_type == "clinica"))
