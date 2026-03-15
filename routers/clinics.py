"""
routers/clinics.py — Regras de negócio após a nova arquitetura:

- Procedimentos: clínicas APENAS ativam/desativam os globais (criados pelo admin)
- Emergência: clínica ativa/desativa participação; preço vem de PlatformEmergencyPrice
- Preço: definido no Procedure pelo admin; ClinicProcedure.price não é mais usado
"""

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
from core.security import get_current_user
from models.models import (
    Clinic, ClinicProcedure, Procedure, Appointment,
    EmergencyRequest, ClinicReview, ClinicEmergencyPrice,
    AppointmentSlot, WorkSchedule,
    PlatformEmergencyPrice,
)
from typing import Optional
from datetime import datetime
import math
import uuid

router = APIRouter(prefix="/clinics", tags=["clinics"])


# ── Schemas ──────────────────────────────────────────────────

class ToggleBody(BaseModel):
    is_active: bool

class EmergencySettingsBody(BaseModel):
    is_enabled: bool

class ClinicStatusUpdate(BaseModel):
    is_online: bool


# ── Helper: distância haversine ──────────────────────────────

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371
    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))


# ── Helper: preço global de emergência ───────────────────────

def get_platform_emergency_price(db: Session) -> float:
    record = db.query(PlatformEmergencyPrice).first()
    if not record:
        record = PlatformEmergencyPrice(id=str(uuid.uuid4()), price=99.99)
        db.add(record)
        db.commit()
        db.refresh(record)
    return record.price


# ════════════════════════════════════════════════════════════
# 1. ROTAS ESTÁTICAS (sem {id} variável)
# ════════════════════════════════════════════════════════════

@router.get("/nearby")
def get_nearby_clinics(
    latitude: float = Query(...),
    longitude: float = Query(...),
    radius: float = Query(10.0),
    procedure: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Clínicas próximas ao paciente (online + ativas)."""
    clinics = db.query(Clinic).filter(Clinic.is_active == True, Clinic.is_online == True).all()

    platform_price = get_platform_emergency_price(db)
    nearby = []

    for clinic in clinics:
        if clinic.latitude and clinic.longitude:
            dist = calculate_distance(latitude, longitude, clinic.latitude, clinic.longitude)
            if dist <= radius:
                # Pega o preço do procedimento "consulta" ativo desta clínica
                consulta = (
                    db.query(ClinicProcedure)
                    .join(Procedure)
                    .filter(
                        ClinicProcedure.clinic_id == clinic.id,
                        ClinicProcedure.is_active == True,
                        Procedure.is_active == True,
                        Procedure.name.ilike("%consulta%"),
                    )
                    .first()
                )
                avg_rating = (
                    db.query(func.avg(ClinicReview.rating))
                    .filter(ClinicReview.clinic_id == clinic.id)
                    .scalar() or 4.5
                )
                nearby.append({
                    "id": clinic.id,
                    "name": clinic.name,
                    "latitude": clinic.latitude,
                    "longitude": clinic.longitude,
                    "distance": round(dist, 1),
                    "rating": round(float(avg_rating), 1),
                    # Preço de consulta agora vem do Procedure global
                    "consultation_price": consulta.procedure.price if consulta else None,
                    "avatar_url": clinic.avatar_url,
                    "address": clinic.address,
                    "phone": clinic.phone,
                    "is_online": clinic.is_online,
                    "emergency_enabled": clinic.emergency_enabled,
                })

    nearby.sort(key=lambda x: x["distance"])
    return nearby


# ── Emergência: settings da clínica (nova rota) ───────────────

@router.get("/emergency-settings")
def get_emergency_settings(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Retorna se a clínica participa de urgências e o preço global da plataforma.
    Substitui GET /clinics/emergency-price para o novo fluxo.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")

    platform_price = get_platform_emergency_price(db)

    return {
        "is_enabled": clinic.emergency_enabled if clinic.emergency_enabled is not None else True,
        "platform_price": platform_price,
    }


@router.patch("/emergency-settings")
def update_emergency_settings(
    data: EmergencySettingsBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Clínica ativa ou desativa participação em urgências."""
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")

    clinic.emergency_enabled = data.is_enabled
    db.commit()

    return {
        "message": f"Urgências {'ativadas' if data.is_enabled else 'desativadas'} com sucesso",
        "is_enabled": data.is_enabled,
    }


# ── Emergência: rota legada mantida por compatibilidade ───────

@router.get("/emergency-price")
def get_my_emergency_price_legacy(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    LEGADO — mantido para não quebrar clientes antigos.
    Retorna o preço global da plataforma (não mais por clínica).
    """
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    price = get_platform_emergency_price(db)
    return {"price": price, "is_active": True}


@router.put("/emergency-price")
def update_emergency_price_legacy(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    LEGADO — clínicas não podem mais alterar o preço.
    Retorna 403 com mensagem clara.
    """
    raise HTTPException(
        status_code=403,
        detail="O preço de urgência é definido pela plataforma. Acesse 'Configurações de Urgência' para ativar/desativar sua participação.",
    )


# ── Status online/offline ─────────────────────────────────────

@router.patch("/status")
def update_clinic_status(
    data: ClinicStatusUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas podem alterar status")

    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")

    if data.is_online and not clinic.is_active:
        raise HTTPException(status_code=400, detail="Clínica suspensa não pode ficar online.")

    old_status = clinic.is_online
    clinic.is_online = data.is_online
    db.commit()

    from routers.websocket import manager
    background_tasks.add_task(manager.send_to_user, str(user.id), {
        "type": "clinic_status_changed",
        "clinic_id": str(user.id),
        "is_online": data.is_online,
        "timestamp": datetime.utcnow().isoformat(),
    })
    if not data.is_online and old_status:
        background_tasks.add_task(manager.send_to_user, str(user.id), {
            "type": "notification",
            "title": "Você está offline",
            "body": "Você não receberá solicitações de emergência até ficar online novamente.",
            "data": {"type": "offline_warning"},
        })

    return {
        "message": f"Status atualizado para {'online' if data.is_online else 'offline'}",
        "is_online": data.is_online,
        "can_receive_emergencies": data.is_online and clinic.is_active and clinic.emergency_enabled,
    }


# ── Dashboard ─────────────────────────────────────────────────

@router.get("/dashboard/stats")
def get_clinic_dashboard_stats(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Acesso negado")

    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")

    from datetime import timedelta, date as date_type
    today = datetime.utcnow().date()
    today_start = datetime.combine(today, datetime.min.time())
    today_end   = datetime.combine(today, datetime.max.time())

    # ── Agendamentos de hoje (todos os status ativos + concluídos + aguardando pagamento) ─────────
    # "awaiting_payment" é incluído pois o slot já está reservado — o paciente só não pagou ainda.
    ACTIVE_STATUSES = ["awaiting_payment", "confirmed", "waiting", "in_progress", "completed"]

    today_appointments = db.query(Appointment).filter(
        Appointment.clinic_id == user.id,
        Appointment.scheduled_at >= today_start,
        Appointment.scheduled_at <= today_end,
        Appointment.status.in_(ACTIVE_STATUSES),
    ).count()

    # ── Faturamento do dia (somente concluídos) ────────────────────────────
    # Usa completed_at para capturar urgências e atendimentos concluídos hoje,
    # independente de quando foram agendados.
    today_revenue = (
        db.query(func.sum(Appointment.clinic_amount))
        .filter(
            Appointment.clinic_id == user.id,
            Appointment.completed_at >= today_start,
            Appointment.completed_at <= today_end,
            Appointment.status == "completed",
        )
        .scalar() or 0
    )

    # ── Faturamento do mês ────────────────────────────────────────────────
    month_start = datetime.combine(
        today.replace(day=1), datetime.min.time()
    )
    month_revenue = (
        db.query(func.sum(Appointment.clinic_amount))
        .filter(
            Appointment.clinic_id == user.id,
            Appointment.completed_at >= month_start,
            Appointment.status == "completed",
        )
        .scalar() or 0
    )

    # ── Urgências pendentes ────────────────────────────────────────────────
    pending_requests = 0
    if clinic.is_online and clinic.emergency_enabled:
        pending_requests = db.query(EmergencyRequest).filter(
            EmergencyRequest.status == "pending",
            EmergencyRequest.expires_at > datetime.utcnow(),
        ).count()

    # ── Avaliação média ────────────────────────────────────────────────────
    avg_rating = (
        db.query(func.avg(ClinicReview.rating))
        .filter(ClinicReview.clinic_id == user.id)
        .scalar() or 4.5
    )

    total_reviews = db.query(ClinicReview).filter(
        ClinicReview.clinic_id == user.id
    ).count()

    # ── Slots de hoje (resumo por status) ─────────────────────────────────
    today_slots = db.query(AppointmentSlot).filter(
        AppointmentSlot.clinic_id == user.id,
        AppointmentSlot.slot_date == today,
    ).all()

    slots_summary = {
        "total": len(today_slots),
        "available":   sum(1 for s in today_slots if s.status == "available"),
        "reserved":    sum(1 for s in today_slots if s.status == "reserved"),
        "confirmed":   sum(1 for s in today_slots if s.status == "confirmed"),
        "waiting":     sum(1 for s in today_slots if s.status == "waiting"),
        "in_progress": sum(1 for s in today_slots if s.status == "in_progress"),
        "occupied":    sum(1 for s in today_slots if s.status == "occupied"),
        "completed":   sum(1 for s in today_slots if s.status == "completed"),
    }

    # ── Agendamentos confirmados futuros (próximos 7 dias) ─────────────────
    upcoming = db.query(Appointment).filter(
        Appointment.clinic_id == user.id,
        Appointment.scheduled_at > today_end,
        Appointment.scheduled_at <= today_end + timedelta(days=7),
        Appointment.status.in_(["confirmed", "awaiting_payment"]),
    ).count()

    # ── Agenda configurada? ────────────────────────────────────────────────
    # Verifica se a clínica tem ao menos 1 regra de trabalho ativa em
    # WorkSchedule. Mais confiável que contar slots (que podem ser 0 num
    # fim de semana ou antes da primeira geração de slots).
    has_schedule_setup = db.query(WorkSchedule).filter(
        WorkSchedule.clinic_id == user.id,
        WorkSchedule.is_active == True,
    ).first() is not None

    return {
        # Indicadores principais (usados pelos StatCards)
        "today_appointments": today_appointments,
        "today_revenue": round(float(today_revenue), 2),
        "pending_requests": pending_requests,
        "rating": round(float(avg_rating), 1),

        # Informações extras
        "total_reviews": total_reviews,
        "month_revenue": round(float(month_revenue), 2),
        "upcoming_7days": upcoming,

        # Resumo dos slots de hoje
        "slots_today": slots_summary,

        # Status da clínica
        "is_online": clinic.is_online,
        "is_active": clinic.is_active,
        "emergency_enabled": clinic.emergency_enabled,
        "can_receive_emergencies": clinic.is_online and clinic.is_active and clinic.emergency_enabled,

        # Configuração de agenda — true se há ao menos 1 WorkSchedule ativo
        "has_schedule_setup": has_schedule_setup,
    }


# ════════════════════════════════════════════════════════════
# 2. PROCEDIMENTOS — clínica só ativa/desativa
# ════════════════════════════════════════════════════════════

@router.get("/my/procedures/active-ids")
def get_my_active_procedure_ids(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Retorna os IDs dos procedimentos globais que ESTA clínica ativou."""
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    clinic = current_user["user"]
    rows = (
        db.query(ClinicProcedure.procedure_id)
        .filter(ClinicProcedure.clinic_id == clinic.id, ClinicProcedure.is_active == True)
        .all()
    )
    return {"ids": [r.procedure_id for r in rows]}


@router.get("/my/procedures")
def get_my_procedures(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Lista todos os procedimentos globais com o status de ativação desta clínica."""
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas podem acessar")

    clinic = current_user["user"]

    # Todos procedimentos globais ativos
    global_procs = db.query(Procedure).filter(Procedure.is_active == True).all()

    # Mapa de ativação desta clínica
    cp_map = {
        cp.procedure_id: cp
        for cp in db.query(ClinicProcedure).filter(ClinicProcedure.clinic_id == clinic.id).all()
    }

    return [
        {
            "id": proc.id,
            "name": proc.name,
            "description": proc.description,
            "category": proc.category,
            "price": proc.price,
            "duration_minutes": proc.default_duration_minutes,
            "max_upper_teeth": proc.max_upper_teeth,
            "max_lower_teeth": proc.max_lower_teeth,
            "is_active": cp_map[proc.id].is_active if proc.id in cp_map else False,
        }
        for proc in global_procs
    ]


@router.patch("/my/procedures/{procedure_id}/toggle")
def toggle_my_procedure(
    procedure_id: str,
    data: ToggleBody,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Clínica ativa ou desativa um procedimento global para si mesma."""
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    # Verificar que o procedimento global existe e está ativo
    proc = db.query(Procedure).filter(
        Procedure.id == procedure_id, Procedure.is_active == True
    ).first()
    if not proc:
        raise HTTPException(status_code=404, detail="Procedimento não encontrado ou inativo na plataforma")

    # Upsert de ClinicProcedure
    cp = db.query(ClinicProcedure).filter(
        ClinicProcedure.clinic_id == user.id,
        ClinicProcedure.procedure_id == procedure_id,
    ).first()

    if cp:
        cp.is_active = data.is_active
    else:
        cp = ClinicProcedure(
            id=str(uuid.uuid4()),
            clinic_id=user.id,
            procedure_id=procedure_id,
            is_active=data.is_active,
        )
        db.add(cp)

    db.commit()
    return {"message": "Status atualizado", "is_active": data.is_active}


# Rota legada: POST /clinics/procedures → retorna 403
@router.post("/procedures")
def create_procedure_legacy():
    raise HTTPException(
        status_code=403,
        detail="Clínicas não podem criar procedimentos. Entre em contato com o administrador da plataforma.",
    )

@router.get("/procedures")
def get_logged_clinic_procedures(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Retorna apenas os procedimentos globais que a clínica LOGADA ativou.
    Rota utilizada pelo frontend na tela de Sugestões de Tratamento.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas podem acessar")

    # 1. Encontra todas as relações de procedimentos que esta clínica ativou
    active_clinic_procs = db.query(ClinicProcedure).filter(
        ClinicProcedure.clinic_id == user.id,
        ClinicProcedure.is_active == True
    ).all()
    
    if not active_clinic_procs:
        return []

    # 2. Extrai os IDs para buscar os dados completos
    active_proc_ids = [cp.procedure_id for cp in active_clinic_procs]

    # 3. Busca os dados dos procedimentos globais (onde ficam os nomes, categorias e preços)
    global_procs = db.query(Procedure).filter(
        Procedure.id.in_(active_proc_ids),
        Procedure.is_active == True
    ).all()

    # 4. Retorna a estrutura exata que o TreatmentSuggestionsClinicScreen.js mapeia
    return [
        {
            "id": proc.id,                 # O Front-end usa p.id
            "procedure_id": proc.id,       # ou p.procedure_id
            "name": proc.name,             # p.name
            "category": proc.category,     # p.procedure?.category
            "price": proc.price,           # p.price
            "is_active": True              # Para passar no filtro: p => p.is_active
        }
        for proc in global_procs
    ]

# Rota legada: PUT /clinics/procedures/{id} → retorna 403
@router.put("/procedures/{procedure_id}")
def update_procedure_legacy(procedure_id: str):
    raise HTTPException(
        status_code=403,
        detail="Clínicas não podem editar procedimentos. Entre em contato com o administrador.",
    )


# Rota legada: DELETE /clinics/procedures/{id} → redireciona para desativar
@router.delete("/procedures/{procedure_id}")
def delete_procedure_legacy(procedure_id: str):
    raise HTTPException(
        status_code=403,
        detail="Use PATCH /clinics/my/procedures/{id}/toggle com is_active=false para desativar.",
    )


# Rota legada: PATCH /clinics/procedures/{id}/toggle → mantida mas delegada
@router.patch("/procedures/{procedure_id}/toggle")
def toggle_procedure_legacy(
    procedure_id: str,
    data: dict,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Legado — delega para a nova rota."""
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    is_active = data.get("is_active", True)

    # Busca por ClinicProcedure.id (legado) ou procedure_id (novo)
    cp = db.query(ClinicProcedure).filter(
        ClinicProcedure.id == procedure_id,
        ClinicProcedure.clinic_id == user.id,
    ).first()

    if not cp:
        raise HTTPException(status_code=404, detail="Procedimento não encontrado")

    cp.is_active = is_active
    db.commit()
    return {"message": "Status atualizado", "is_active": cp.is_active}


# ── Reviews ──────────────────────────────────────────────────

@router.get("/my-reviews")
def get_my_reviews(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    from models.models import User as UserModel
    reviews = (
        db.query(ClinicReview, UserModel, Appointment)
        .join(UserModel, ClinicReview.patient_id == UserModel.id)
        .outerjoin(Appointment, ClinicReview.appointment_id == Appointment.id)
        .filter(ClinicReview.clinic_id == user.id)
        .order_by(ClinicReview.created_at.desc())
        .all()
    )
    
    return {
        "reviews": [
            {
                "id": r.ClinicReview.id,
                "rating": r.ClinicReview.rating,
                "comment": r.ClinicReview.comment,
                "created_at": r.ClinicReview.created_at,
                "patient_name": r.User.name,
                "patient_avatar": r.User.avatar_url,
                
                "procedure_name": r.Appointment.procedure.name if r.Appointment and r.Appointment.procedure else None,
            }
            for r in reviews
        ]
    }


@router.get("/review-stats")
def get_review_stats(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    total_reviews = db.query(ClinicReview).filter(ClinicReview.clinic_id == user.id).count()
    avg_rating = (
        db.query(func.avg(ClinicReview.rating)).filter(ClinicReview.clinic_id == user.id).scalar() or 0
    )
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_reviews = db.query(ClinicReview).filter(
        ClinicReview.clinic_id == user.id,
        ClinicReview.created_at >= month_start,
    ).count()
    return {
        "total_reviews": total_reviews,
        "average_rating": round(float(avg_rating), 1),
        "month_reviews": month_reviews,
    }


# ════════════════════════════════════════════════════════════
# 3. ROTAS COM {clinic_id} — SEMPRE NO FINAL
# ════════════════════════════════════════════════════════════

@router.get("/{clinic_id}/procedures")
def get_clinic_procedures(
    clinic_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Lista procedimentos ativos de uma clínica específica (visão do paciente).
    Retorna o preço do Procedure global (não mais de ClinicProcedure).
    """
    rows = (
        db.query(ClinicProcedure, Procedure)
        .join(Procedure, ClinicProcedure.procedure_id == Procedure.id)
        .filter(
            ClinicProcedure.clinic_id == clinic_id,
            ClinicProcedure.is_active == True,
            Procedure.is_active == True,
        )
        .all()
    )
    return [
        {
            "id": row.Procedure.id,
            "name": row.Procedure.name,
            "description": row.Procedure.description,
            "category": row.Procedure.category,
            "price": row.Procedure.price,              # preço do admin
            "duration_minutes": row.Procedure.default_duration_minutes,
            "max_upper_teeth": row.Procedure.max_upper_teeth,
            "max_lower_teeth": row.Procedure.max_lower_teeth,
        }
        for row in rows
    ]


@router.get("/{clinic_id}/emergency-price")
def get_clinic_emergency_price(clinic_id: str, db: Session = Depends(get_db)):
    """Retorna o preço global de urgência da plataforma (igual para todas as clínicas)."""
    price = get_platform_emergency_price(db)
    # Verifica se esta clínica aceita urgências
    clinic = db.query(Clinic).filter(Clinic.id == clinic_id).first()
    emergency_enabled = clinic.emergency_enabled if clinic else True
    return {"price": price, "emergency_enabled": emergency_enabled}


@router.get("/{clinic_id}")
def get_clinic_details(
    clinic_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    clinic = db.query(Clinic).filter(Clinic.id == clinic_id).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")

    avg_rating = (
        db.query(func.avg(ClinicReview.rating)).filter(ClinicReview.clinic_id == clinic.id).scalar() or 4.5
    )
    total_reviews = db.query(ClinicReview).filter(ClinicReview.clinic_id == clinic.id).count()

    return {
        "id": clinic.id,
        "name": clinic.name,
        "email": clinic.email,
        "phone": clinic.phone,
        "address": clinic.address,
        "description": clinic.description,
        "avatar_url": clinic.avatar_url,
        "latitude": clinic.latitude,
        "longitude": clinic.longitude,
        "rating": round(float(avg_rating), 1),
        "total_reviews": total_reviews,
        "is_online": clinic.is_online,
        "emergency_enabled": clinic.emergency_enabled,
    }
