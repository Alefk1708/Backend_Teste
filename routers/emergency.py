# routers/emergency.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc
from database import get_db
from core.security import get_current_user
from models.models import EmergencyRequest, User, Clinic, Appointment, EmergencyDecline
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel

router = APIRouter(prefix="/emergency", tags=["emergency"])

class EmergencyRequestDetail(BaseModel):
    id: str
    patient_name: str
    patient_phone: str
    procedure_type: str
    description: Optional[str]
    distance: float
    latitude: float
    longitude: float
    status: str
    created_at: Optional[str]
    expires_at: Optional[str]
    claimed_at: Optional[str]
    clinic_name: Optional[str]
    appointment_id: Optional[str]

    class Config:
        from_attributes = True


@router.get("/requests", response_model=List[EmergencyRequestDetail])
def get_emergency_requests(
    status: Optional[str] = Query(None, description="Filter by status: pending, claimed, expired, all"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Retorna solicitações de emergência para a clínica logada.
    BLOQUEADO se clínica estiver offline.
    """
    user = current_user["user"]
    user_type = current_user["payload"]["type"]
    
    if user_type != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas podem acessar")
    
    # ========== VERIFICAÇÃO CRÍTICA: Clínica deve estar ONLINE ==========
    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")
    
    if not clinic.is_online:
        raise HTTPException(
            status_code=403,
            detail="Você está offline. Mude seu status para online para ver solicitações de emergência."
        )

    if not clinic.is_active:
        raise HTTPException(status_code=403, detail="Clínica suspensa")

    if not clinic.emergency_enabled:
        raise HTTPException(
            status_code=403,
            detail="Sua clínica não participa do sistema de urgências. Ative em Configurações de Urgência."
        )
    
    if not clinic.latitude or not clinic.longitude:
        raise HTTPException(status_code=400, detail="Clínica sem localização definida")
    
    # ========== Buscar IDs que esta clínica JÁ RECUSOU ==========
    declined_ids = [
        d.emergency_request_id for d in db.query(EmergencyDecline).filter(
            EmergencyDecline.clinic_id == clinic.id
        ).all()
    ]
    
    query = db.query(EmergencyRequest, User).join(User, EmergencyRequest.patient_id == User.id)
    
    # Filtrar por status
    if status == "pending":
        query = query.filter(
            EmergencyRequest.status == "pending",
            EmergencyRequest.expires_at > datetime.utcnow()
        )
        # EXCLUIR RECUSADAS
        if declined_ids:
            query = query.filter(~EmergencyRequest.id.in_(declined_ids))
            
    elif status == "claimed":
        query = query.filter(
            EmergencyRequest.status == "claimed",
            EmergencyRequest.clinic_id == clinic.id
        )
    elif status == "expired":
        query = query.filter(
            ((EmergencyRequest.status == "pending") & (EmergencyRequest.expires_at <= datetime.utcnow())) |
            (EmergencyRequest.status == "expired")
        )
        # Também excluir recusadas das expiradas
        if declined_ids:
            query = query.filter(~EmergencyRequest.id.in_(declined_ids))
    elif status == "all":
        query = query.filter(
            (EmergencyRequest.clinic_id == clinic.id) |
            (EmergencyRequest.status == "pending")
        )
        if declined_ids:
            query = query.filter(~EmergencyRequest.id.in_(declined_ids))
    else:
        # Default: pendentes próximas (excluindo recusadas)
        query = query.filter(
            EmergencyRequest.status == "pending",
            EmergencyRequest.expires_at > datetime.utcnow()
        )
        if declined_ids:
            query = query.filter(~EmergencyRequest.id.in_(declined_ids))
    
    query = query.order_by(desc(EmergencyRequest.created_at))
    
    results = query.offset(offset).limit(limit).all()
    
    from math import radians, sin, cos, sqrt, atan2
    
    def haversine(lat1, lon1, lat2, lon2):
        R = 6371
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        return R * c
    
    response = []
    for req, patient in results:
        distance = haversine(
            clinic.latitude, clinic.longitude,
            req.latitude, req.longitude
        )
        
        clinic_name = None
        appointment_id = None
        if req.clinic_id:
            claimed_clinic = db.query(Clinic).filter(Clinic.id == req.clinic_id).first()
            if claimed_clinic:
                clinic_name = claimed_clinic.name
            
            appointment = db.query(Appointment).filter(
                Appointment.patient_id == req.patient_id,
                Appointment.type == "emergency",
                Appointment.clinic_id == req.clinic_id
            ).order_by(desc(Appointment.created_at)).first()
            if appointment:
                appointment_id = str(appointment.id)
        
        response.append({
            "id": str(req.id),
            "patient_name": patient.name,
            "patient_phone": patient.phone,
            "procedure_type": req.procedure_type,
            "description": req.description,
            "distance": round(distance, 1),
            "latitude": req.latitude,
            "longitude": req.longitude,
            "status": "expired" if req.status == "pending" and req.expires_at <= datetime.utcnow() else req.status,
            "created_at": req.created_at.isoformat() if req.created_at else None,
            "expires_at": req.expires_at.isoformat() if req.expires_at else None,
            "claimed_at": req.claimed_at.isoformat() if req.claimed_at else None,
            "clinic_name": clinic_name,
            "appointment_id": appointment_id
        })
    
    return response


@router.get("/requests/{request_id}")
def get_emergency_request_detail(
    request_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Detalhes de uma solicitação específica - BLOQUEADO se offline"""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]
    
    # ========== VERIFICAÇÃO: Apenas clínicas online ==========
    if user_type == "clinica":
        clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
        if not clinic or not clinic.is_online:
            raise HTTPException(status_code=403, detail="Clínica deve estar online")
    
    req = db.query(EmergencyRequest).filter(EmergencyRequest.id == request_id).first()
    if not req:
        raise HTTPException(status_code=404, detail="Solicitação não encontrada")
    
    patient = db.query(User).filter(User.id == req.patient_id).first()
    
    return {
        "id": str(req.id),
        "patient_name": patient.name if patient else "Desconhecido",
        "patient_phone": patient.phone if patient else None,
        "procedure_type": req.procedure_type,
        "description": req.description,
        "status": req.status,
        "latitude": req.latitude,
        "longitude": req.longitude,
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "expires_at": req.expires_at.isoformat() if req.expires_at else None
    }


@router.post("/requests/{request_id}/reject")
def reject_emergency_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Clínica recusa uma solicitação - BLOQUEADO se offline"""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]
    
    if user_type != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")
    
    # ========== VERIFICAÇÃO CRÍTICA ==========
    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic or not clinic.is_online:
        raise HTTPException(
            status_code=403,
            detail="Você deve estar online para recusar solicitações"
        )
    if not clinic.emergency_enabled:
        raise HTTPException(
            status_code=403,
            detail="Sua clínica não participa do sistema de urgências."
        )
    
    req = db.query(EmergencyRequest).filter(
        EmergencyRequest.id == request_id,
        EmergencyRequest.status == "pending"
    ).first()
    
    if not req:
        raise HTTPException(status_code=404, detail="Solicitação não disponível")
    
    return {"message": "Solicitação recusada"}


@router.get("/stats")
def get_emergency_stats(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Estatísticas de urgências - BLOQUEADO se offline"""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]
    
    if user_type != "clinica":
        raise HTTPException(status_code=403, detail="Apenas clínicas")
    
    # ========== VERIFICAÇÃO ==========
    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    if not clinic or not clinic.is_online or not clinic.emergency_enabled:
        return {
            "pending_nearby": 0,
            "claimed_today": 0,
            "total_claimed": 0,
            "total_declined": 0,
            "total_interactions": 0,
            "acceptance_rate": 0,
            "is_offline": True,
            "emergency_enabled": clinic.emergency_enabled if clinic else False,
            "message": (
                "Sua clínica não participa de urgências. Ative em Configurações de Urgência."
                if clinic and not clinic.emergency_enabled
                else "Você está offline. Mude para online para ver estatísticas reais."
            )
        }
    
    # ========== CORREÇÃO: Buscar IDs que esta clínica JÁ RECUSOU ==========
    declined_ids = [
        d.emergency_request_id for d in db.query(EmergencyDecline).filter(
            EmergencyDecline.clinic_id == clinic.id
        ).all()
    ]
    
    # ========== CORREÇÃO CRÍTICA: Pendentes APENAS que esta clínica ainda NÃO recusou ==========
    pending_query = db.query(EmergencyRequest).filter(
        EmergencyRequest.status == "pending",
        EmergencyRequest.expires_at > datetime.utcnow()
    )
    
    # EXCLUIR as que esta clínica já recusou
    if declined_ids:
        pending_query = pending_query.filter(~EmergencyRequest.id.in_(declined_ids))
    
    pending_count = pending_query.count()
    
    # Aceitas por esta clínica (hoje)
    from datetime import date
    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    
    claimed_today = db.query(EmergencyRequest).filter(
        EmergencyRequest.clinic_id == clinic.id,
        EmergencyRequest.status == "claimed",
        EmergencyRequest.claimed_at >= today_start
    ).count()
    
    # Total de solicitações que esta clínica ACEITOU
    claimed_total = db.query(EmergencyRequest).filter(
        EmergencyRequest.clinic_id == clinic.id,
        EmergencyRequest.status == "claimed"
    ).count()
    
    # Total de solicitações que esta clínica RECUSOU
    declined_total = db.query(EmergencyDecline).filter(
        EmergencyDecline.clinic_id == clinic.id
    ).count()
    
    # Total de interações = aceitas + recusadas
    total_interactions = claimed_total + declined_total
    
    # Taxa de aceitamento = aceitas / total de interações
    acceptance_rate = (claimed_total / total_interactions * 100) if total_interactions > 0 else 0
    
    return {
        "pending_nearby": pending_count,
        "claimed_today": claimed_today,
        "total_claimed": claimed_total,
        "total_declined": declined_total,
        "total_interactions": total_interactions,
        "acceptance_rate": round(acceptance_rate, 1),
        "is_offline": False
    }