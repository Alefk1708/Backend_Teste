"""
Router de Slots de Agendamento — Sistema de Agenda da Clínica

FLUXO DO PACIENTE (App Mobile):
  1. GET  /slots/{clinic_id}/available?date=YYYY-MM-DD → vê horários livres
  2. POST /slots/{slot_id}/reserve                     → reserva por 10 min
  3. POST /appointments/schedule  (com slot_id)        → cria o agendamento
  4. POST /payments/card ou /payments/pix              → paga
     ↓ (webhook ou aprovação imediata)
  5. Slot status → "confirmed"

FLUXO DA CLÍNICA (Painel de Gestão):
  1. POST /slots/schedule/setup         → define regra de trabalho por dia
  2. POST /slots/generate               → gera os slots para um período
  3. GET  /slots/my?date=YYYY-MM-DD     → painel em tempo real
  4. PATCH /slots/{id}/occupy           → encaixe presencial (some do app)
  5. PATCH /slots/{id}/checkin          → paciente chegou (sala de espera)
  6. PATCH /slots/{id}/in-progress      → atendimento iniciado
  7. PATCH /slots/{id}/complete         → atendimento finalizado
  8. PATCH /slots/{id}/cancel           → cancelar/desbloquear slot
"""

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from database import get_db
from core.security import get_current_user
from models.models import (
    AppointmentSlot, WorkSchedule, Clinic, User, Appointment, Notification
)
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date, timedelta
import uuid
import json
import logging

from routers.websocket import manager

router = APIRouter(prefix="/slots", tags=["slots"])

RESERVATION_MINUTES = 10  # Tempo de reserva temporária

# ──────────────────────────────────────────────────────────
# SCHEMAS
# ──────────────────────────────────────────────────────────

class WorkScheduleUpsert(BaseModel):
    """Define ou atualiza a regra de trabalho de um dia da semana."""
    day_of_week: int               # 0=Seg ... 6=Dom
    start_time: str                # "09:00"
    end_time: str                  # "18:00"
    lunch_start: Optional[str] = None   # "12:00"
    lunch_end:   Optional[str] = None   # "13:00"
    slot_duration_minutes: int = 30
    is_active: bool = True


class GenerateSlotsRequest(BaseModel):
    """Gera slots para um intervalo de datas com base nas WorkSchedules ativas."""
    date_from: str   # "YYYY-MM-DD"
    date_to: str     # "YYYY-MM-DD"
    overwrite: bool = False  # Se True, recria slots "available" já existentes


class OccupySlotRequest(BaseModel):
    """Marca slot como ocupado por paciente presencial."""
    walk_in_patient_name: Optional[str] = None


# ──────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────

def _release_expired_reservations(db: Session):
    """Libera reservas temporárias de 10 minutos que expiraram."""
    now = datetime.utcnow()
    expired = db.query(AppointmentSlot).filter(
        AppointmentSlot.status == "reserved",
        AppointmentSlot.reservation_expires_at != None,
        AppointmentSlot.reservation_expires_at < now,
    ).all()
    for slot in expired:
        slot.status = "available"
        slot.reserved_by = None
        slot.reserved_at = None
        slot.reservation_expires_at = None
    if expired:
        db.commit()
    return len(expired)


def _time_str_to_hhmm(t: str) -> tuple:
    """Converte "HH:MM" para (h, m)."""
    h, m = map(int, t.split(":"))
    return h, m


def _generate_slots_for_day(
    clinic_id: str,
    target_date: date,
    schedule: WorkSchedule,
    overwrite: bool,
    db: Session,
) -> int:
    """Gera slots para um dia específico baseado na WorkSchedule. Retorna qtd criados."""
    start_h, start_m = _time_str_to_hhmm(schedule.start_time)
    end_h,   end_m   = _time_str_to_hhmm(schedule.end_time)
    duration = timedelta(minutes=schedule.slot_duration_minutes)

    lunch_start_dt = None
    lunch_end_dt   = None
    if schedule.lunch_start and schedule.lunch_end:
        lsh, lsm = _time_str_to_hhmm(schedule.lunch_start)
        leh, lem = _time_str_to_hhmm(schedule.lunch_end)
        lunch_start_dt = datetime(target_date.year, target_date.month, target_date.day, lsh, lsm)
        lunch_end_dt   = datetime(target_date.year, target_date.month, target_date.day, leh, lem)

    current = datetime(target_date.year, target_date.month, target_date.day, start_h, start_m)
    day_end = datetime(target_date.year, target_date.month, target_date.day, end_h, end_m)

    created = 0
    while current + duration <= day_end:
        slot_end = current + duration

        # Pular horário de almoço
        if lunch_start_dt and lunch_end_dt:
            if current < lunch_end_dt and slot_end > lunch_start_dt:
                current = lunch_end_dt
                continue

        # Verificar se já existe slot para este horário
        existing = db.query(AppointmentSlot).filter(
            AppointmentSlot.clinic_id == clinic_id,
            AppointmentSlot.start_time == current,
        ).first()

        if existing:
            if overwrite and existing.status == "available":
                # Recriar apenas se ainda disponível
                existing.end_time = slot_end
                existing.updated_at = datetime.utcnow()
            current += duration
            continue

        slot = AppointmentSlot(
            id=str(uuid.uuid4()),
            clinic_id=clinic_id,
            slot_date=target_date,
            start_time=current,
            end_time=slot_end,
            status="available",
        )
        db.add(slot)
        created += 1
        current += duration

    db.commit()
    return created


# ──────────────────────────────────────────────────────────
# ENDPOINTS — ADMINISTRAÇÃO DA CLÍNICA
# ──────────────────────────────────────────────────────────

@router.post("/schedule/setup")
def upsert_work_schedule(
    data: WorkScheduleUpsert,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Clínica define/atualiza a regra de trabalho para um dia da semana.
    Ex: Dentista atende Seg-Sex 09:00–18:00, almoço 12:00–13:00, a cada 30 min.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    if data.day_of_week < 0 or data.day_of_week > 6:
        raise HTTPException(status_code=400, detail="day_of_week deve ser 0 (Seg) a 6 (Dom)")

    if data.slot_duration_minutes < 10 or data.slot_duration_minutes > 240:
        raise HTTPException(status_code=400, detail="Duração do slot deve ser entre 10 e 240 minutos")

    # Upsert
    schedule = db.query(WorkSchedule).filter(
        WorkSchedule.clinic_id == user.id,
        WorkSchedule.day_of_week == data.day_of_week,
    ).first()

    if schedule:
        schedule.start_time = data.start_time
        schedule.end_time = data.end_time
        schedule.lunch_start = data.lunch_start
        schedule.lunch_end = data.lunch_end
        schedule.slot_duration_minutes = data.slot_duration_minutes
        schedule.is_active = data.is_active
        schedule.updated_at = datetime.utcnow()
    else:
        schedule = WorkSchedule(
            id=str(uuid.uuid4()),
            clinic_id=user.id,
            day_of_week=data.day_of_week,
            start_time=data.start_time,
            end_time=data.end_time,
            lunch_start=data.lunch_start,
            lunch_end=data.lunch_end,
            slot_duration_minutes=data.slot_duration_minutes,
            is_active=data.is_active,
        )
        db.add(schedule)

    db.commit()

    day_names = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
    return {
        "message": f"Regra para {day_names[data.day_of_week]} salva com sucesso",
        "schedule_id": schedule.id,
        "day": day_names[data.day_of_week],
        "start_time": schedule.start_time,
        "end_time": schedule.end_time,
        "slot_duration_minutes": schedule.slot_duration_minutes,
        "is_active": schedule.is_active,
    }


@router.get("/schedule")
def get_work_schedules(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Retorna todas as regras de trabalho da clínica."""
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    schedules = db.query(WorkSchedule).filter(
        WorkSchedule.clinic_id == user.id,
    ).order_by(WorkSchedule.day_of_week).all()

    day_names = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]
    return [
        {
            "id": s.id,
            "day_of_week": s.day_of_week,
            "day_name": day_names[s.day_of_week],
            "start_time": s.start_time,
            "end_time": s.end_time,
            "lunch_start": s.lunch_start,
            "lunch_end": s.lunch_end,
            "slot_duration_minutes": s.slot_duration_minutes,
            "is_active": s.is_active,
        }
        for s in schedules
    ]


@router.post("/generate")
def generate_slots(
    data: GenerateSlotsRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Gera automaticamente todos os slots para o período especificado,
    respeitando as WorkSchedules ativas da clínica.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    try:
        date_from = date.fromisoformat(data.date_from)
        date_to   = date.fromisoformat(data.date_to)
    except ValueError:
        raise HTTPException(status_code=400, detail="Formato de data inválido. Use YYYY-MM-DD")

    if date_to < date_from:
        raise HTTPException(status_code=400, detail="date_to deve ser maior ou igual a date_from")

    if (date_to - date_from).days > 90:
        raise HTTPException(status_code=400, detail="Período máximo de geração é 90 dias")

    schedules = db.query(WorkSchedule).filter(
        WorkSchedule.clinic_id == user.id,
        WorkSchedule.is_active == True,
    ).all()

    if not schedules:
        raise HTTPException(
            status_code=400,
            detail="Nenhuma regra de trabalho ativa encontrada. Configure em POST /slots/schedule/setup"
        )

    schedule_map = {s.day_of_week: s for s in schedules}
    total_created = 0
    current_date = date_from

    while current_date <= date_to:
        weekday = current_date.weekday()  # 0=Mon, 6=Sun
        if weekday in schedule_map:
            created = _generate_slots_for_day(
                clinic_id=str(user.id),
                target_date=current_date,
                schedule=schedule_map[weekday],
                overwrite=data.overwrite,
                db=db,
            )
            total_created += created
        current_date += timedelta(days=1)

    return {
        "message": f"{total_created} slot(s) gerado(s) com sucesso",
        "total_created": total_created,
        "period": f"{data.date_from} a {data.date_to}",
    }


@router.get("/my")
def get_clinic_slots(
    date_str: Optional[str] = None,  # "YYYY-MM-DD", padrão = hoje
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Painel da clínica: lista todos os slots do dia com status e dados do paciente.
    Atualiza automaticamente reservas expiradas antes de retornar.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    # Liberar reservas expiradas silenciosamente
    _release_expired_reservations(db)

    target_date = date.today()
    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de data inválido. Use YYYY-MM-DD")

    slots = db.query(AppointmentSlot).filter(
        AppointmentSlot.clinic_id == user.id,
        AppointmentSlot.slot_date == target_date,
    ).order_by(AppointmentSlot.start_time).all()

    result = []
    for slot in slots:
        patient_name = None
        patient_phone = None
        procedure_name = None

        if slot.appointment_id:
            appt = db.query(Appointment).filter(
                Appointment.id == slot.appointment_id
            ).first()
            if appt:
                patient = db.query(User).filter(User.id == appt.patient_id).first()
                if patient:
                    patient_name = patient.name
                    patient_phone = patient.phone
                if appt.procedure_id:
                    from models.models import Procedure
                    proc = db.query(Procedure).filter(
                        Procedure.id == appt.procedure_id
                    ).first()
                    if proc:
                        procedure_name = proc.name

        # Reserva temporária ainda ativa
        if slot.status == "reserved" and slot.reserved_by:
            reserved_user = db.query(User).filter(User.id == slot.reserved_by).first()
            patient_name = f"{reserved_user.name} (reservando...)" if reserved_user else "Paciente reservando..."

        result.append({
            "id": slot.id,
            "date": str(slot.slot_date),
            "start_time": slot.start_time.strftime("%H:%M"),
            "end_time": slot.end_time.strftime("%H:%M"),
            "status": slot.status,
            "patient_name": patient_name or slot.walk_in_patient_name,
            "patient_phone": patient_phone,
            "procedure_name": procedure_name,
            "appointment_id": slot.appointment_id,
            "walk_in_patient_name": slot.walk_in_patient_name,
            "reservation_expires_at": slot.reservation_expires_at.isoformat()
                if slot.reservation_expires_at else None,
        })

    return result


@router.patch("/{slot_id}/occupy")
async def occupy_slot(
    slot_id: str,
    data: OccupySlotRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Clínica marca slot como ocupado (encaixe presencial).
    O slot some do app mobile imediatamente.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    slot = db.query(AppointmentSlot).filter(
        AppointmentSlot.id == slot_id,
        AppointmentSlot.clinic_id == user.id,
    ).first()

    if not slot:
        raise HTTPException(status_code=404, detail="Slot não encontrado")

    if slot.status not in ("available",):
        raise HTTPException(
            status_code=400,
            detail=f"Slot com status '{slot.status}' não pode ser marcado como ocupado"
        )

    slot.status = "occupied"
    slot.walk_in_patient_name = data.walk_in_patient_name
    slot.updated_at = datetime.utcnow()
    db.commit()

    # Notificar via WebSocket o próprio painel
    await manager.send_to_user(str(user.id), {
        "type": "slot_updated",
        "slot_id": slot_id,
        "status": "occupied",
        "start_time": slot.start_time.strftime("%H:%M"),
        "walk_in_patient_name": data.walk_in_patient_name,
    })

    return {
        "message": "Slot marcado como ocupado (presencial)",
        "slot_id": slot_id,
        "status": "occupied",
    }


@router.patch("/{slot_id}/checkin")
async def checkin_slot(
    slot_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Recepcionista faz check-in: paciente do app chegou na clínica.
    Status: confirmed → waiting
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    slot = db.query(AppointmentSlot).filter(
        AppointmentSlot.id == slot_id,
        AppointmentSlot.clinic_id == user.id,
    ).first()

    if not slot:
        raise HTTPException(status_code=404, detail="Slot não encontrado")

    if slot.status != "confirmed":
        raise HTTPException(
            status_code=400,
            detail="Check-in só é possível para slots com status 'confirmed'"
        )

    slot.status = "waiting"
    slot.updated_at = datetime.utcnow()
    db.commit()

    # Notificar painel via WebSocket
    await manager.send_to_user(str(user.id), {
        "type": "slot_updated",
        "slot_id": slot_id,
        "status": "waiting",
        "start_time": slot.start_time.strftime("%H:%M"),
    })

    return {"message": "Check-in realizado. Paciente na sala de espera.", "slot_id": slot_id}


@router.patch("/{slot_id}/in-progress")
async def start_slot(
    slot_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Dentista inicia o atendimento.
    Status: waiting | occupied → in_progress
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    slot = db.query(AppointmentSlot).filter(
        AppointmentSlot.id == slot_id,
        AppointmentSlot.clinic_id == user.id,
    ).first()

    if not slot:
        raise HTTPException(status_code=404, detail="Slot não encontrado")

    if slot.status not in ("waiting", "occupied"):
        raise HTTPException(
            status_code=400,
            detail="Atendimento só pode ser iniciado de 'waiting' ou 'occupied'"
        )

    slot.status = "in_progress"
    slot.updated_at = datetime.utcnow()

    # Atualiza o Appointment vinculado também
    if slot.appointment_id:
        appt = db.query(Appointment).filter(Appointment.id == slot.appointment_id).first()
        if appt and appt.status == "confirmed":
            appt.status = "in_progress"

    db.commit()

    await manager.send_to_user(str(user.id), {
        "type": "slot_updated",
        "slot_id": slot_id,
        "status": "in_progress",
        "start_time": slot.start_time.strftime("%H:%M"),
    })

    return {"message": "Atendimento iniciado", "slot_id": slot_id}


@router.patch("/{slot_id}/complete")
async def complete_slot(
    slot_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Finaliza o atendimento. Clínica recebe o repasse financeiro.
    Status: in_progress → completed
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    slot = db.query(AppointmentSlot).filter(
        AppointmentSlot.id == slot_id,
        AppointmentSlot.clinic_id == user.id,
    ).first()

    if not slot:
        raise HTTPException(status_code=404, detail="Slot não encontrado")

    if slot.status not in ("in_progress", "waiting", "occupied"):
        raise HTTPException(
            status_code=400,
            detail=f"Slot com status '{slot.status}' não pode ser finalizado desta forma"
        )

    slot.status = "completed"
    slot.updated_at = datetime.utcnow()

    # Finalizar o Appointment vinculado
    if slot.appointment_id:
        appt = db.query(Appointment).filter(Appointment.id == slot.appointment_id).first()
        if appt and appt.status in ("confirmed", "in_progress", "waiting"):
            appt.status = "completed"
            appt.completed_at = datetime.utcnow()

    db.commit()

    await manager.send_to_user(str(user.id), {
        "type": "slot_updated",
        "slot_id": slot_id,
        "status": "completed",
        "start_time": slot.start_time.strftime("%H:%M"),
    })

    return {"message": "Atendimento finalizado com sucesso", "slot_id": slot_id}


@router.patch("/{slot_id}/cancel")
async def cancel_slot(
    slot_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Clínica cancela/desbloqueia um slot (ex: ocupado por engano, feriado, etc).
    Só é possível cancelar slots occupied ou confirmed sem atendimento iniciado.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")

    slot = db.query(AppointmentSlot).filter(
        AppointmentSlot.id == slot_id,
        AppointmentSlot.clinic_id == user.id,
    ).first()

    if not slot:
        raise HTTPException(status_code=404, detail="Slot não encontrado")

    if slot.status in ("in_progress", "completed"):
        raise HTTPException(
            status_code=400,
            detail="Não é possível cancelar um slot em andamento ou já finalizado"
        )

    old_status = slot.status
    slot.status = "available"
    slot.walk_in_patient_name = None
    slot.reserved_by = None
    slot.reserved_at = None
    slot.reservation_expires_at = None
    slot.updated_at = datetime.utcnow()

    # Se havia agendamento confirmado, cancelar também
    if slot.appointment_id and old_status == "confirmed":
        appt = db.query(Appointment).filter(Appointment.id == slot.appointment_id).first()
        if appt:
            appt.status = "cancelled"
            appt.cancellation_reason = "Slot cancelado pela clínica"
        slot.appointment_id = None

    db.commit()

    await manager.send_to_user(str(user.id), {
        "type": "slot_updated",
        "slot_id": slot_id,
        "status": "available",
        "start_time": slot.start_time.strftime("%H:%M"),
    })

    return {"message": "Slot liberado e disponível novamente", "slot_id": slot_id}


# ──────────────────────────────────────────────────────────
# ENDPOINTS — PACIENTE (App Mobile)
# ──────────────────────────────────────────────────────────

@router.get("/{clinic_id}/available")
def get_available_slots(
    clinic_id: str,
    date_str: Optional[str] = None,  # "YYYY-MM-DD"
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Paciente vê os horários disponíveis de uma clínica para uma data.
    Reservas de 10 minutos expiradas são liberadas antes de retornar.
    """
    if current_user["payload"]["type"] != "paciente":
        raise HTTPException(status_code=403, detail="Apenas pacientes")

    clinic = db.query(Clinic).filter(
        Clinic.id == clinic_id,
        Clinic.is_active == True,
    ).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")

    # Liberar reservas expiradas antes de mostrar
    _release_expired_reservations(db)

    target_date = date.today()
    if date_str:
        try:
            target_date = date.fromisoformat(date_str)
        except ValueError:
            raise HTTPException(status_code=400, detail="Formato de data inválido. Use YYYY-MM-DD")

    # Apenas slots futuros disponíveis
    now = datetime.utcnow()
    slots = db.query(AppointmentSlot).filter(
        AppointmentSlot.clinic_id == clinic_id,
        AppointmentSlot.slot_date == target_date,
        AppointmentSlot.status == "available",
        AppointmentSlot.start_time > now,
    ).order_by(AppointmentSlot.start_time).all()

    return {
        "clinic_id": clinic_id,
        "clinic_name": clinic.name,
        "date": str(target_date),
        "available_slots": [
            {
                "id": s.id,
                "start_time": s.start_time.strftime("%H:%M"),
                "end_time":   s.end_time.strftime("%H:%M"),
                "start_datetime": s.start_time.isoformat(),
            }
            for s in slots
        ],
        "total": len(slots),
    }


@router.get("/{clinic_id}/calendar")
def get_available_days(
    clinic_id: str,
    month: Optional[int] = None,   # 1-12, padrão = mês atual
    year: Optional[int] = None,    # padrão = ano atual
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Retorna quais dias do mês têm pelo menos 1 slot disponível.
    Usado pelo app para renderizar o calendário visual.
    """
    if current_user["payload"]["type"] != "paciente":
        raise HTTPException(status_code=403, detail="Apenas pacientes")

    clinic = db.query(Clinic).filter(
        Clinic.id == clinic_id,
        Clinic.is_active == True,
    ).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")

    _release_expired_reservations(db)

    today = date.today()
    target_month = month or today.month
    target_year  = year  or today.year

    # Intervalo do mês
    from calendar import monthrange
    _, last_day = monthrange(target_year, target_month)
    month_start = date(target_year, target_month, 1)
    month_end   = date(target_year, target_month, last_day)
    now = datetime.utcnow()

    from sqlalchemy import func
    rows = (
        db.query(
            AppointmentSlot.slot_date,
            func.count(AppointmentSlot.id).label("available_count"),
        )
        .filter(
            AppointmentSlot.clinic_id == clinic_id,
            AppointmentSlot.slot_date >= month_start,
            AppointmentSlot.slot_date <= month_end,
            AppointmentSlot.status == "available",
            AppointmentSlot.start_time > now,
        )
        .group_by(AppointmentSlot.slot_date)
        .all()
    )

    return {
        "clinic_id": clinic_id,
        "year": target_year,
        "month": target_month,
        "days_with_availability": [
            {"date": str(r.slot_date), "available_slots": r.available_count}
            for r in rows
        ],
    }


@router.post("/{slot_id}/reserve")
async def reserve_slot(
    slot_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Paciente reserva um slot por 10 minutos enquanto preenche o pagamento.
    Apenas 1 reserva ativa por paciente é permitida.
    Se o paciente tentar reservar outro slot, o anterior é liberado.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "paciente":
        raise HTTPException(status_code=403, detail="Apenas pacientes")

    # Liberar reservas expiradas primeiro
    _release_expired_reservations(db)

    # Liberar reserva anterior deste paciente (se existir)
    previous = db.query(AppointmentSlot).filter(
        AppointmentSlot.reserved_by == user.id,
        AppointmentSlot.status == "reserved",
    ).first()
    if previous:
        previous.status = "available"
        previous.reserved_by = None
        previous.reserved_at = None
        previous.reservation_expires_at = None

    # Buscar o slot desejado
    slot = db.query(AppointmentSlot).filter(
        AppointmentSlot.id == slot_id,
    ).first()

    if not slot:
        raise HTTPException(status_code=404, detail="Slot não encontrado")

    if slot.status != "available":
        raise HTTPException(
            status_code=409,
            detail="Este horário não está mais disponível. Escolha outro horário."
        )

    if slot.start_time <= datetime.utcnow():
        raise HTTPException(status_code=400, detail="Este horário já passou")

    expires_at = datetime.utcnow() + timedelta(minutes=RESERVATION_MINUTES)
    slot.status = "reserved"
    slot.reserved_by = user.id
    slot.reserved_at = datetime.utcnow()
    slot.reservation_expires_at = expires_at
    slot.updated_at = datetime.utcnow()
    db.commit()

    # Notificar painel da clínica em tempo real
    await manager.send_to_user(str(slot.clinic_id), {
        "type": "slot_updated",
        "slot_id": slot_id,
        "status": "reserved",
        "start_time": slot.start_time.strftime("%H:%M"),
        "patient_name": f"{user.name} (reservando...)",
        "reservation_expires_at": expires_at.isoformat(),
    })

    return {
        "message": "Horário reservado! Você tem 10 minutos para concluir o pagamento.",
        "slot_id": slot_id,
        "start_time": slot.start_time.strftime("%H:%M"),
        "start_datetime": slot.start_time.isoformat(),
        "reservation_expires_at": expires_at.isoformat(),
        "clinic_id": str(slot.clinic_id),
    }


@router.delete("/{slot_id}/reserve")
async def release_reservation(
    slot_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Paciente cancela sua reserva manual (ex: voltou na tela de seleção).
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "paciente":
        raise HTTPException(status_code=403, detail="Apenas pacientes")

    slot = db.query(AppointmentSlot).filter(
        AppointmentSlot.id == slot_id,
        AppointmentSlot.reserved_by == user.id,
        AppointmentSlot.status == "reserved",
    ).first()

    if not slot:
        raise HTTPException(status_code=404, detail="Reserva não encontrada")

    slot.status = "available"
    slot.reserved_by = None
    slot.reserved_at = None
    slot.reservation_expires_at = None
    slot.updated_at = datetime.utcnow()
    db.commit()

    # Notificar clínica
    await manager.send_to_user(str(slot.clinic_id), {
        "type": "slot_updated",
        "slot_id": slot_id,
        "status": "available",
        "start_time": slot.start_time.strftime("%H:%M"),
    })

    return {"message": "Reserva cancelada. Horário liberado."}


# ──────────────────────────────────────────────────────────
# FUNÇÃO INTERNA — chamada por payments.py ao confirmar pag.
# ──────────────────────────────────────────────────────────

async def confirm_slot_payment(
    appointment_id: str,
    db: Session,
):
    """
    Chamada internamente quando pagamento é aprovado.
    Muda slot de 'reserved' → 'confirmed' e vincula appointment_id.
    Notifica painel da clínica via WebSocket.
    """
    appt = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appt:
        return

    slot = db.query(AppointmentSlot).filter(
        AppointmentSlot.clinic_id == appt.clinic_id,
        AppointmentSlot.start_time == appt.scheduled_at,
        AppointmentSlot.status.in_(["reserved", "available"]),
    ).first()

    if not slot:
        # Fallback: buscar slot do paciente reservado
        slot = db.query(AppointmentSlot).filter(
            AppointmentSlot.clinic_id == appt.clinic_id,
            AppointmentSlot.reserved_by == appt.patient_id,
            AppointmentSlot.status == "reserved",
        ).first()

    if not slot:
        logging.warning(
            "[slots] Slot não encontrado para appointment %s ao confirmar pagamento",
            appointment_id,
        )
        return

    slot.status = "confirmed"
    slot.appointment_id = appointment_id
    slot.reserved_by = None
    slot.reservation_expires_at = None
    slot.updated_at = datetime.utcnow()
    db.commit()

    # Buscar nome do paciente para notificar o painel
    patient = db.query(User).filter(User.id == appt.patient_id).first()
    from models.models import Procedure
    proc = db.query(Procedure).filter(Procedure.id == appt.procedure_id).first() if appt.procedure_id else None

    await manager.send_to_user(str(slot.clinic_id), {
        "type": "slot_confirmed",
        "slot_id": str(slot.id),
        "status": "confirmed",
        "start_time": slot.start_time.strftime("%H:%M"),
        "patient_name": patient.name if patient else "Paciente",
        "patient_phone": patient.phone if patient else None,
        "procedure_name": proc.name if proc else "Consulta",
        "appointment_id": appointment_id,
        "total_amount": appt.total_amount,
    })

    logging.info("[slots] Slot %s confirmado para appointment %s", slot.id, appointment_id)
