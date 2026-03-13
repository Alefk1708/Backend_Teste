from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from routers.websocket import (
    notify_new_emergency, 
    notify_emergency_accepted,
    manager
)
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
from core.security import get_current_user
from models.models import (
    User, Clinic, Appointment, EmergencyRequest, Procedure, 
    ClinicProcedure, Payment, Notification, ClinicEmergencyPrice,
    PlatformEmergencyPrice, EmergencyDecline, ClinicReview
)
from schemas.appointment import EmergencyRequestResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import math
import uuid

router = APIRouter(prefix="/appointments", tags=["appointments"])

MAX_DISTANCE_KM = 35
EMERGENCY_MAX_DISTANCE_KM = 10

def validate_distance(db: Session, patient_lat: float, patient_lng: float, clinic_id: str, max_distance: float = MAX_DISTANCE_KM):
    """Valida se a clínica está dentro da distância máxima permitida"""
    clinic = db.query(Clinic).filter(Clinic.id == clinic_id).first()
    
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")
    
    if not clinic.latitude or not clinic.longitude:
        raise HTTPException(status_code=400, detail="Clínica sem localização definida")
    
    distance = calculate_distance(patient_lat, patient_lng, clinic.latitude, clinic.longitude)
    
    if distance > max_distance:
        raise HTTPException(
            status_code=400, 
            detail=f"Clínica muito distante ({distance:.1f}km). Máximo permitido: {max_distance}km"
        )
    
    return distance

class AppointmentCreate(BaseModel):
    clinic_id: str
    procedure_id: str
    scheduled_at: datetime
    service_type: str
    notes: Optional[str] = None
    patient_latitude: float
    patient_longitude: float
    # Campos de lentes de contato (opcionais — só presentes quando category == "lentes_contato")
    lens_upper_count: Optional[int] = None
    lens_lower_count: Optional[int] = None
    lens_total_price: Optional[float] = None

class EmergencyRequestCreate(BaseModel):
    latitude: float
    longitude: float
    description: Optional[str] = None
    procedure_type: str = "urgencia"
    max_distance_km: float = 10.0 

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def is_first_appointment(db: Session, patient_id: str, clinic_id: str) -> bool:
    previous = db.query(Appointment).filter(
        Appointment.patient_id == patient_id,
        Appointment.clinic_id == clinic_id,
        Appointment.status == "completed"
    ).first()
    return previous is None

def notify_nearby_clinics(db: Session, request_id: str, lat: float, lng: float, radius: float):
    clinics = db.query(Clinic).filter(
        Clinic.is_online == True,
        Clinic.is_active == True,
        Clinic.emergency_enabled == True
    ).all()
    
    for clinic in clinics:
        if clinic.latitude and clinic.longitude:
            distance = calculate_distance(lat, lng, clinic.latitude, clinic.longitude)
            if distance <= radius:
                notification = Notification(
                    id=str(uuid.uuid4()),
                    user_id=clinic.id,
                    user_type="clinica",
                    title="Nova solicitação de urgência!",
                    message=f"Paciente a {distance:.1f}km solicitou atendimento de urgência",
                    type="emergency",
                    data=str({"request_id": request_id, "distance": distance})
                )
                db.add(notification)
    
    db.commit()

def calculate_financial_split(
    db: Session, 
    patient_id: str, 
    clinic_id: str, 
    total_amount: float,
    service_type: str = "procedure"
) -> dict:
    """
    Calcula divisão financeira:
    - 1ª consulta: App paga 100% para clínica (investimento do app)
    - Procedimentos: App fica com 15%, clínica recebe 85%
    - Emergência: Regra especial (definir com cliente)
    """
    
    # Verifica se é primeira vez nesta clínica
    previous_appointments = db.query(Appointment).filter(
        Appointment.patient_id == patient_id,
        Appointment.clinic_id == clinic_id,
        Appointment.status == "completed",
        Appointment.service_type.in_(["first_consultation", "procedure"])
    ).count()
    
    is_first_time = previous_appointments == 0
    
    if service_type == "first_consultation" or (service_type == "emergency" and is_first_time):
        return {
            "service_type": "first_consultation",
            "total_amount": total_amount, 
            "platform_fee": 0.0,
            "clinic_amount": total_amount,
            "is_first_time": True,
            "platform_profit": -total_amount,
            "description": "Primeira consulta - App investe no paciente"
        }
    else:
        platform_fee = total_amount * 0.15
        clinic_amount = total_amount * 0.85
        
        return {
            "service_type": "procedure",
            "total_amount": total_amount,
            "platform_fee": platform_fee,
            "clinic_amount": clinic_amount,
            "is_first_time": False,
            "platform_profit": platform_fee,
            "description": f"Procedimento - Comissão 15% (R${platform_fee:.2f})"
        }

@router.post("/emergency/request")
async def create_emergency_request(
    data: EmergencyRequestCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Paciente solicita atendimento de urgência (Uber-like) - COM VALIDAÇÃO DE DISTÂNCIA"""
    user = current_user["user"]
    if current_user["payload"]["type"] != "paciente":
        raise HTTPException(status_code=403, detail="Apenas pacientes")
    
    # ========== VALIDAÇÃO DE DISTÂNCIA ==========
    # Verificar se há clínicas próximas o suficiente
    nearby_clinics = db.query(Clinic).filter(
        Clinic.is_active == True,
        Clinic.is_online == True,
        Clinic.emergency_enabled == True,
        Clinic.latitude.isnot(None),
        Clinic.longitude.isnot(None)
    ).all()
    
    clinics_in_range = []
    for clinic in nearby_clinics:
        distance = calculate_distance(
            data.latitude, 
            data.longitude, 
            clinic.latitude, 
            clinic.longitude
        )
        if distance <= data.max_distance_km:  # Usar o raio definido pelo paciente
            clinics_in_range.append({
                "clinic": clinic,
                "distance": distance
            })
    
    if not clinics_in_range:
        raise HTTPException(
            status_code=400, 
            detail=f"Nenhuma clínica disponível dentro de {data.max_distance_km}km. "
                   f"Tente aumentar o raio de busca ou verifique sua localização."
        )
    
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    
    emergency = EmergencyRequest(
        id=str(uuid.uuid4()),
        patient_id=user.id,
        latitude=data.latitude,
        longitude=data.longitude,
        procedure_type=data.procedure_type,
        description=data.description,
        expires_at=expires_at,
        status="pending"
    )
    
    db.add(emergency)
    db.commit()
    
    await notify_new_emergency(emergency, db)
    
    return {
        "request_id": emergency.id,
        "clinics_found": len(clinics_in_range),  # Informar quantas clínicas encontrou
        "message": f"Solicitação criada. {len(clinics_in_range)} clínicas próximas notificadas.",
        "expires_at": expires_at,
        "estimated_price": 99.99
    }


@router.get("/emergency/pending", response_model=list[EmergencyRequestResponse])
def get_pending_emergency_requests(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")
    
    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic or not clinic.latitude:
        raise HTTPException(status_code=400, detail="Clínica sem localização")
    
    if not clinic.is_online:
        raise HTTPException(
            status_code=403,
            detail="Você está offline. Mude seu status para online para ver solicitações."
        )
    
    declined_ids = [
        d.emergency_request_id for d in db.query(EmergencyDecline).filter(
            EmergencyDecline.clinic_id == user.id
        ).all()
    ]
    
    requests = db.query(EmergencyRequest, User).join(User).filter(
        EmergencyRequest.status == "pending",
        EmergencyRequest.expires_at > datetime.utcnow(),
        ~EmergencyRequest.id.in_(declined_ids) if declined_ids else True
    ).all()
    
    nearby_requests = []
    for req, patient in requests:
        distance = calculate_distance(
            clinic.latitude, clinic.longitude,
            req.latitude, req.longitude
        )
        
        nearby_requests.append({
            "id": str(req.id),
            "patient_name": patient.name,
            "patient_phone": patient.phone,
            "procedure_type": req.procedure_type,
            "description": req.description,
            "distance": round(distance, 1),
            "latitude": req.latitude,
            "longitude": req.longitude,
            "created_at": req.created_at.isoformat() if req.created_at else None,
            "expires_at": req.expires_at.isoformat() if req.expires_at else None
        })
    
    nearby_requests.sort(key=lambda x: x["distance"])
    return nearby_requests

@router.post("/emergency/{request_id}/claim")
async def claim_emergency_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Clínica aceita atender emergência - BLOQUEADO se offline"""
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")
    
    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")
    
    if not clinic.is_online:
        raise HTTPException(
            status_code=403,
            detail="Você está offline. Mude seu status para online para aceitar emergências."
        )

    if not clinic.is_active:
        raise HTTPException(status_code=403, detail="Clínica suspensa")

    if not clinic.emergency_enabled:
        raise HTTPException(
            status_code=403,
            detail="Sua clínica não está participando do sistema de urgências. Ative em Configurações de Urgência."
        )
    
    emergency = db.query(EmergencyRequest).filter(
        EmergencyRequest.id == request_id,
        EmergencyRequest.status == "pending"
    ).first()
    
    if not emergency or emergency.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Solicitação não disponível")
    
    # Preço de urgência é global (definido pelo admin), não mais por clínica
    platform_price_record = db.query(PlatformEmergencyPrice).first()
    total_amount = platform_price_record.price if platform_price_record else 99.99
    
    financial_split = calculate_financial_split(
        db, 
        emergency.patient_id, 
        user.id, 
        total_amount,
        service_type="emergency"
    )
    
    appointment = Appointment(
        id=str(uuid.uuid4()),
        patient_id=emergency.patient_id,
        clinic_id=user.id,
        status="awaiting_payment",
        type="emergency",
        service_type=financial_split["service_type"],
        patient_latitude=emergency.latitude,
        patient_longitude=emergency.longitude,
        description=emergency.description,
        total_amount=financial_split["total_amount"],
        platform_fee=financial_split["platform_fee"],
        clinic_amount=financial_split["clinic_amount"],
        scheduled_at=datetime.utcnow(),
        payment_deadline=datetime.utcnow() + timedelta(hours=1)
    )
    
    db.add(appointment)
    
    emergency.status = "claimed"
    emergency.clinic_id = user.id
    emergency.claimed_at = datetime.utcnow()
    
    await manager.broadcast_to_clinics({
        "type": "emergency_claimed",
        "title": "Solicitação atendida",
        "body": "Outra clínica já aceitou esta solicitação",
        "data": {"emergency_id": str(request_id)}
    }, exclude_user_id=str(user.id))
    
    clinic_data = db.query(Clinic).filter(Clinic.id == user.id).first()
    await notify_emergency_accepted(
        emergency.patient_id, 
        clinic_data, 
        appointment.id, 
        {
            "total_amount": financial_split["total_amount"],
            "platform_fee": financial_split["platform_fee"],
            "clinic_amount": financial_split["clinic_amount"],
            "is_first_consultation": financial_split["is_first_time"],
            "service_type": financial_split["service_type"]
        }
    )
    
    db.commit()
    
    return {
        "appointment_id": appointment.id,
        "financial_details": financial_split,
        "message": "Atendimento aguardando pagamento do paciente",
        "next_step": "patient_payment"
    }


@router.post("/schedule")
def create_scheduled_appointment(
    data: AppointmentCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Agendamento normal de consulta/procedimento - COM VALIDAÇÃO DE DISTÂNCIA"""
    user = current_user["user"]
    if current_user["payload"]["type"] != "paciente":
        raise HTTPException(status_code=403, detail="Apenas pacientes")
    
    # ========== VALIDAÇÃO DE DISTÂNCIA ==========
    # Verificar se o paciente tem localização
    if not data.patient_latitude or not data.patient_longitude:
        raise HTTPException(status_code=400, detail="Localização do paciente é obrigatória")
    
    # Validar distância máxima
    distance = validate_distance(
        db, 
        data.patient_latitude, 
        data.patient_longitude, 
        data.clinic_id
    )
    
    # Verificar que o procedimento global existe e está ativo
    global_proc = db.query(Procedure).filter(
        Procedure.id == data.procedure_id,
        Procedure.is_active == True
    ).first()

    if not global_proc:
        raise HTTPException(status_code=404, detail="Procedimento não encontrado ou inativo")

    # Verificar que a clínica tem este procedimento ativo
    clinic_proc = db.query(ClinicProcedure).filter(
        ClinicProcedure.clinic_id == data.clinic_id,
        ClinicProcedure.procedure_id == data.procedure_id,
        ClinicProcedure.is_active == True
    ).first()

    if not clinic_proc:
        raise HTTPException(status_code=404, detail="Esta clínica não oferece este procedimento")

    # Calcular valor total:
    # - Lentes de contato: preço por dente * (superiores + inferiores)
    # - Demais: preço do procedimento global
    is_lentes = global_proc.category == "lentes_contato"
    if is_lentes and data.lens_upper_count is not None and data.lens_lower_count is not None:
        total_teeth = data.lens_upper_count + data.lens_lower_count
        if total_teeth == 0:
            raise HTTPException(status_code=400, detail="Selecione ao menos 1 dente para lentes de contato")
        max_upper = global_proc.max_upper_teeth or 6
        max_lower = global_proc.max_lower_teeth or 6
        if data.lens_upper_count > max_upper:
            raise HTTPException(status_code=400, detail=f"Máximo de dentes superiores: {max_upper}")
        if data.lens_lower_count > max_lower:
            raise HTTPException(status_code=400, detail=f"Máximo de dentes inferiores: {max_lower}")
        effective_price = global_proc.price * total_teeth
    else:
        effective_price = global_proc.price

    is_consulta = "consulta" in global_proc.name.lower() or global_proc.category == "consulta"
    service_type = "first_consultation" if is_consulta else "procedure"

    financial_split = calculate_financial_split(
        db,
        user.id,
        data.clinic_id,
        effective_price,
        service_type
    )

    appointment = Appointment(
        id=str(uuid.uuid4()),
        patient_id=user.id,
        clinic_id=data.clinic_id,
        procedure_id=data.procedure_id,
        service_type=financial_split["service_type"],
        status="awaiting_payment",
        type="scheduled",
        scheduled_at=data.scheduled_at,
        description=data.notes,
        total_amount=financial_split["total_amount"],
        platform_fee=financial_split["platform_fee"],
        clinic_amount=financial_split["clinic_amount"],
        payment_deadline=datetime.utcnow() + timedelta(hours=1),
        patient_latitude=data.patient_latitude,
        patient_longitude=data.patient_longitude,
        # Dados de lentes de contato
        lens_upper_count=data.lens_upper_count if is_lentes else None,
        lens_lower_count=data.lens_lower_count if is_lentes else None,
        lens_total_price=effective_price if is_lentes else None,
    )
    
    db.add(appointment)
    db.commit()
    
    lens_info = None
    if is_lentes and data.lens_upper_count is not None:
        lens_info = {
            "upper_count": data.lens_upper_count,
            "lower_count": data.lens_lower_count,
            "total_teeth": data.lens_upper_count + data.lens_lower_count,
            "price_per_tooth": global_proc.price,
            "total_price": effective_price,
        }

    return {
        "appointment_id": appointment.id,
        "distance_km": round(distance, 1),
        "financial_details": financial_split,
        "lens_details": lens_info,
        "message": "Agendamento criado. Proceda com o pagamento.",
        "payment_required": True
    }

@router.post("/")
def create_appointment(
    data: AppointmentCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    user = current_user["user"]
    if current_user["payload"]["type"] != "paciente":
        raise HTTPException(status_code=403, detail="Apenas pacientes podem agendar")

    # Busca o procedimento global (fonte do preço)
    global_proc = db.query(Procedure).filter(
        Procedure.id == data.procedure_id,
        Procedure.is_active == True
    ).first()

    if not global_proc:
        raise HTTPException(status_code=404, detail="Procedimento não encontrado ou inativo")

    # Verifica se a clínica oferece este procedimento
    clinic_proc = db.query(ClinicProcedure).filter(
        ClinicProcedure.clinic_id == data.clinic_id,
        ClinicProcedure.procedure_id == data.procedure_id,
        ClinicProcedure.is_active == True
    ).first()

    if not clinic_proc:
        raise HTTPException(status_code=404, detail="Procedimento não encontrado na clínica")

    # Calcula preço: lentes de contato têm preço por dente, demais usam preço global
    is_lentes = global_proc.category == "lentes_contato"
    if is_lentes and data.lens_upper_count is not None and data.lens_lower_count is not None:
        total_teeth = data.lens_upper_count + data.lens_lower_count
        if total_teeth == 0:
            raise HTTPException(status_code=400, detail="Selecione ao menos 1 dente para lentes de contato")
        effective_price = global_proc.price * total_teeth
    else:
        effective_price = global_proc.price

    if effective_price is None:
        raise HTTPException(status_code=400, detail="Preço do procedimento não definido")

    is_consulta = "consulta" in global_proc.name.lower() or global_proc.category == "consulta"
    service_type = "first_consultation" if is_consulta else "procedure"

    financial_split = calculate_financial_split(
        db,
        user.id,
        data.clinic_id,
        effective_price,
        service_type
    )

    appointment = Appointment(
        id=str(uuid.uuid4()),
        patient_id=user.id,
        clinic_id=data.clinic_id,
        procedure_id=data.procedure_id,
        service_type=financial_split["service_type"],
        status="awaiting_payment",
        type="scheduled",
        scheduled_at=data.scheduled_at,
        description=data.notes,
        total_amount=financial_split["total_amount"],
        platform_fee=financial_split["platform_fee"],
        clinic_amount=financial_split["clinic_amount"],
        payment_deadline=datetime.utcnow() + timedelta(hours=1),
        patient_latitude=data.patient_latitude,
        patient_longitude=data.patient_longitude,
        lens_upper_count=data.lens_upper_count if is_lentes else None,
        lens_lower_count=data.lens_lower_count if is_lentes else None,
        lens_total_price=effective_price if is_lentes else None,
    )

    db.add(appointment)
    db.commit()

    return {
        "appointment_id": appointment.id,
        "total_amount": financial_split["total_amount"],
        "platform_fee": financial_split["platform_fee"],
        "clinic_amount": financial_split["clinic_amount"],
        "is_first_appointment": financial_split["is_first_time"],
        "message": "Agendamento criado. Proceda com o pagamento para confirmar."
    }

@router.patch("/{appointment_id}/start")
def start_appointment(
    appointment_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Clínica inicia o atendimento (muda de confirmed para in_progress) - BLOQUEADO se offline"""
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")
    
    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic or not clinic.is_online:
        raise HTTPException(
            status_code=403, 
            detail="Você está offline. Mude para online para iniciar atendimentos."
        )
    
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment or appointment.clinic_id != user.id:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    
    if appointment.status != "confirmed":
        raise HTTPException(status_code=400, detail="Consulta precisa estar confirmada")
    
    appointment.status = "in_progress"
    db.commit()
    
    return {"message": "Atendimento iniciado", "status": "in_progress"}

@router.get("/my")
def get_my_appointments(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    user = current_user["user"]
    user_type = current_user["payload"]["type"]
    
    query = db.query(Appointment)
    
    if user_type == "paciente":
        query = query.filter(Appointment.patient_id == user.id)
    else:
        query = query.filter(Appointment.clinic_id == user.id)
    
    if status:
        query = query.filter(Appointment.status == status)
    
    appointments = query.order_by(Appointment.scheduled_at.desc()).all()
    
    result = []
    for app in appointments:
        clinic = db.query(Clinic).filter(Clinic.id == app.clinic_id).first()
        patient = db.query(User).filter(User.id == app.patient_id).first()
        procedure = db.query(Procedure).filter(Procedure.id == app.procedure_id).first() if app.procedure_id else None
        
        review = db.query(ClinicReview).filter(
            ClinicReview.appointment_id == app.id
        ).first()
        
        result.append({
            "id": app.id,
            "status": app.status,
            "type": app.type,
            "scheduled_at": app.scheduled_at,
            "total_amount": app.total_amount,
            "platform_fee": app.platform_fee,
            "clinic_amount": app.clinic_amount,
            "clinic_name": clinic.name if clinic else None,
            "patient_name": patient.name if patient else None,
            "patient_phone": patient.phone if patient else None,
            "procedure_name": procedure.name if procedure else "Consulta",
            "created_at": app.created_at,
            "rating": review.rating if review else None,
            "review_comment": review.comment if review else None
        })
    
    return result

@router.patch("/{appointment_id}/cancel")
def cancel_appointment(
    appointment_id: str,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    user = current_user["user"]
    user_type = current_user["payload"]["type"]
    
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    
    if user_type == "paciente" and appointment.patient_id != user.id:
        raise HTTPException(status_code=403, detail="Sem permissão")
    if user_type == "clinica" and appointment.clinic_id != user.id:
        raise HTTPException(status_code=403, detail="Sem permissão")
    
    appointment.status = "cancelled"
    appointment.cancellation_reason = reason
    
    payment = db.query(Payment).filter(
        Payment.appointment_id == appointment_id,
        Payment.status == "completed"
    ).first()
    
    if payment:
        payment.status = "refunded"
        payment.refunded_at = datetime.utcnow()
    
    db.commit()
    
    return {"message": "Agendamento cancelado com sucesso"}

@router.patch("/{appointment_id}/confirm")
def confirm_appointment(
    appointment_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Confirma agendamento - BLOQUEADO se offline"""
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas podem confirmar")
    
    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic or not clinic.is_online:
        raise HTTPException(
            status_code=403, 
            detail="Você está offline. Mude para online para confirmar agendamentos."
        )
    
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment or appointment.clinic_id != user.id:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    
    appointment.status = "confirmed"
    db.commit()
    
    return {"message": "Agendamento confirmado"}

@router.patch("/{appointment_id}/complete")
def complete_appointment(
    appointment_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Finaliza atendimento - BLOQUEADO se offline"""
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas podem finalizar")
    
    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic or not clinic.is_online:
        raise HTTPException(
            status_code=403, 
            detail="Você está offline. Mude para online para finalizar atendimentos."
        )
    
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    if not appointment or appointment.clinic_id != user.id:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    
    appointment.status = "completed"
    appointment.completed_at = datetime.utcnow()
    db.commit()
    
    return {"message": "Atendimento finalizado com sucesso"}

@router.get("/{appointment_id}")
def get_appointment_details(
    appointment_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Busca detalhes completos de um agendamento específico"""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]
    
    appointment = db.query(Appointment).filter(Appointment.id == appointment_id).first()
    
    if not appointment:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    
    current_user_id = str(user.id)
    appointment_patient_id = str(appointment.patient_id)
    appointment_clinic_id = str(appointment.clinic_id)
    
    if user_type == "paciente" and current_user_id != appointment_patient_id:
        raise HTTPException(status_code=403, detail="Sem permissão para ver este agendamento")
    
    if user_type == "clinica" and current_user_id != appointment_clinic_id:
        raise HTTPException(status_code=403, detail="Sem permissão para ver este agendamento")
    
    clinic = db.query(Clinic).filter(Clinic.id == appointment.clinic_id).first()
    patient = db.query(User).filter(User.id == appointment.patient_id).first()
    procedure = db.query(Procedure).filter(Procedure.id == appointment.procedure_id).first() if appointment.procedure_id else None
    
    payment = db.query(Payment).filter(
        Payment.appointment_id == appointment_id
    ).order_by(Payment.created_at.desc()).first()
    
    visible_status = appointment.status
    
    if user_type == "clinica":
        if appointment.status == "awaiting_payment":
            visible_status = "Aguardando pagamento do paciente"
        elif appointment.status == "confirmed":
            visible_status = "confirmado"
        elif appointment.status == "completed":
            visible_status = "completed"
    
    if user_type == "clinica":
        response = {
            "id": appointment.id,
            "status": appointment.status,
            "status_label": visible_status,
            "type": appointment.type,
            "service_type": appointment.service_type,
            "scheduled_at": appointment.scheduled_at,
            "created_at": appointment.created_at,
            "completed_at": appointment.completed_at,
            "description": appointment.description,
            "cancellation_reason": appointment.cancellation_reason,
            "total_amount": appointment.total_amount,
            "lens_upper_count": appointment.lens_upper_count,
            "lens_lower_count": appointment.lens_lower_count,
            "lens_total_price": appointment.lens_total_price,
            "platform_fee": appointment.platform_fee,
            "clinic_amount": appointment.clinic_amount,
            "is_paid_to_clinic": appointment.is_paid_to_clinic,
            "paid_to_clinic_at": appointment.paid_to_clinic_at,
            "patient_id": appointment.patient_id,
            "patient_name": patient.name if patient else "Paciente não identificado",
            "patient_phone": patient.phone if patient else None,
            "patient_avatar_url": patient.avatar_url if patient else None,
            "clinic_id": appointment.clinic_id,
            "clinic_name": clinic.name if clinic else None,
            "clinic_phone": clinic.phone if clinic else None,
            "clinic_address": clinic.address if clinic else None,
            "clinic_latitude": clinic.latitude if clinic else None,
            "clinic_longitude": clinic.longitude if clinic else None,
            "procedure_id": appointment.procedure_id,
            "procedure_name": procedure.name if procedure else "Consulta",
            "payment_status": payment.status if payment else None,
            "payment_method": payment.payment_method if payment else None,
            "paid_at": payment.paid_at if payment else None,
            "patient_latitude": appointment.patient_latitude,
            "patient_longitude": appointment.patient_longitude
        }
    else:
        response = {
            "id": appointment.id,
            "status": appointment.status,
            "type": appointment.type,
            "service_type": appointment.service_type,
            "scheduled_at": appointment.scheduled_at,
            "created_at": appointment.created_at,
            "completed_at": appointment.completed_at,
            "description": appointment.description,
            "cancellation_reason": appointment.cancellation_reason,
            "total_amount": appointment.total_amount,
            "clinic_id": appointment.clinic_id,
            "clinic_name": clinic.name if clinic else None,
            "clinic_phone": clinic.phone if clinic else None,
            "clinic_address": clinic.address if clinic else None,
            "clinic_latitude": clinic.latitude if clinic else None,
            "clinic_longitude": clinic.longitude if clinic else None,
            "clinic_avatar_url": clinic.avatar_url if clinic else None,
            "patient_id": appointment.patient_id,
            "patient_name": patient.name if patient else None,
            "procedure_id": appointment.procedure_id,
            "procedure_name": procedure.name if procedure else "Consulta",
            "payment": {
                "id": payment.id,
                "status": payment.status,
                "method": payment.payment_method,
                "amount": payment.amount,
                "paid_at": payment.paid_at,
                "pix_code": payment.pix_code if payment and payment.payment_method == "pix" else None
            } if payment else None
        }
    
    return response

@router.post("/emergency/{request_id}/decline")
async def decline_emergency_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Clínica recusa atender emergência - não será mais notificada sobre esta solicitação - BLOQUEADO se offline"""
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas podem recusar")
    
    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic or not clinic.is_online:
        raise HTTPException(
            status_code=403, 
            detail="Você deve estar online para recusar solicitações"
        )
    
    emergency = db.query(EmergencyRequest).filter(
        EmergencyRequest.id == request_id,
        EmergencyRequest.status == "pending",
        EmergencyRequest.expires_at > datetime.utcnow()
    ).first()
    
    if not emergency:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada ou já expirada")
    
    existing_decline = db.query(EmergencyDecline).filter(
        EmergencyDecline.emergency_request_id == request_id,
        EmergencyDecline.clinic_id == user.id
    ).first()
    
    if existing_decline:
        return {"message": "Solicitação já recusada anteriormente"}
    
    decline = EmergencyDecline(
        id=str(uuid.uuid4()),
        emergency_request_id=request_id,
        clinic_id=user.id
    )
    db.add(decline)
    db.commit()
    
    declined_clinics = [
        d.clinic_id for d in db.query(EmergencyDecline).filter(
            EmergencyDecline.emergency_request_id == request_id
        ).all()
    ]

    patient = db.query(User).filter(User.id == emergency.patient_id).first()
    
    await manager.broadcast_to_clinics({
        "type": "emergency_still_available",
        "title": "Urgência ainda disponível",
        "body": "Uma clínica recusou, você ainda pode aceitar!",
        "data": {
            "emergency_id": str(request_id),
            "patient_name": patient.name if patient else "Paciente",
            "procedure_type": emergency.procedure_type,
            "declined_count": len(declined_clinics)
        }
    }, exclude_user_ids=declined_clinics)
    
    return {
        "message": "Solicitação recusada com sucesso",
        "emergency_id": request_id,
        "declined_at": decline.declined_at.isoformat()
    }