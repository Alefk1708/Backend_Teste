from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from core.security import get_current_user
from models.models import ClinicReview, Appointment, Clinic, User
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid

router = APIRouter(prefix="/reviews", tags=["reviews"])

class ReviewCreate(BaseModel):
    appointment_id: str
    rating: int  # 1-5
    comment: Optional[str] = None

class ReviewResponse(BaseModel):
    id: str
    clinic_id: str
    patient_id: str
    appointment_id: str
    rating: int
    comment: Optional[str]
    created_at: datetime

@router.post("/", response_model=ReviewResponse)
def create_review(
    data: ReviewCreate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Paciente cria avaliação de uma consulta concluída"""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]
    
    # Apenas pacientes podem avaliar
    if user_type != "paciente":
        raise HTTPException(status_code=403, detail="Apenas pacientes podem avaliar")
    
    # Verificar se agendamento existe e pertence ao paciente
    appointment = db.query(Appointment).filter(
        Appointment.id == data.appointment_id
    ).first()
    
    if not appointment:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    
    if str(appointment.patient_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Este agendamento não pertence a você")
    
    # Verificar se consulta foi concluída
    if appointment.status != "completed":
        raise HTTPException(
            status_code=400, 
            detail="Só é possível avaliar consultas concluídas"
        )
    
    # Verificar se já foi avaliado
    existing_review = db.query(ClinicReview).filter(
        ClinicReview.appointment_id == data.appointment_id
    ).first()
    
    if existing_review:
        raise HTTPException(status_code=400, detail="Esta consulta já foi avaliada")
    
    # Validar rating
    if data.rating < 1 or data.rating > 5:
        raise HTTPException(status_code=400, detail="Avaliação deve ser entre 1 e 5 estrelas")
    
    # Criar avaliação
    review = ClinicReview(
        id=str(uuid.uuid4()),
        clinic_id=appointment.clinic_id,
        patient_id=user.id,
        appointment_id=data.appointment_id,
        rating=data.rating,
        comment=data.comment
    )
    
    db.add(review)
    db.commit()
    db.refresh(review)
    
    return review

@router.get("/clinic/{clinic_id}")
def get_clinic_reviews(
    clinic_id: str,
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """Buscar avaliações de uma clínica"""
    reviews = db.query(ClinicReview, User).join(
        User, ClinicReview.patient_id == User.id
    ).filter(
        ClinicReview.clinic_id == clinic_id
    ).order_by(
        ClinicReview.created_at.desc()
    ).offset(offset).limit(limit).all()
    
    return [
        {
            "id": str(rev.ClinicReview.id),
            "rating": rev.ClinicReview.rating,
            "comment": rev.ClinicReview.comment,
            "created_at": rev.ClinicReview.created_at,
            "patient_name": rev.User.name if rev.User else "Paciente Anônimo"
        }
        for rev in reviews
    ]

@router.get("/my-reviews")
def get_my_reviews(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Paciente vê suas próprias avaliações"""
    user = current_user["user"]
    
    reviews = db.query(ClinicReview, Clinic).join(
        Clinic, ClinicReview.clinic_id == Clinic.id
    ).filter(
        ClinicReview.patient_id == user.id
    ).order_by(
        ClinicReview.created_at.desc()
    ).all()
    
    return [
        {
            "id": str(rev.ClinicReview.id),
            "clinic_name": rev.Clinic.name,
            "rating": rev.ClinicReview.rating,
            "comment": rev.ClinicReview.comment,
            "created_at": rev.ClinicReview.created_at
        }
        for rev in reviews
    ]

# ═══════════════════════════════════════════════════════════
# DENÚNCIA DE AVALIAÇÃO
# Clínica denuncia avaliações falsas/abusivas → notifica admins
# ═══════════════════════════════════════════════════════════
from models.models import Notification
import json as _json

REPORT_CATEGORIES = {
    "fake":       "Avaliação falsa / nunca fui paciente desta clínica",
    "offensive":  "Conteúdo ofensivo ou linguagem inadequada",
    "spam":       "Spam ou conteúdo completamente irrelevante",
    "competitor": "Concorrente agindo de má-fé",
    "other":      "Outro motivo",
}


class ReviewReport(BaseModel):
    reason:   str
    category: str = "other"


@router.get("/report-categories")
def list_report_categories():
    """Retorna as categorias de denúncia disponíveis."""
    return [{"key": k, "label": v} for k, v in REPORT_CATEGORIES.items()]


@router.post("/{review_id}/report")
def report_review(
    review_id: str,
    data: ReviewReport,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Clínica (ou paciente) denuncia uma avaliação abusiva ou falsa.
    Gera notificação para todos os admins ativos da plataforma.
    """
    user      = current_user["user"]
    user_type = current_user["payload"]["type"]

    review = db.query(ClinicReview).filter(ClinicReview.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Avaliação não encontrada")

    # Clínica só pode denunciar avaliações da sua própria clínica
    if user_type == "clinica" and str(review.clinic_id) != str(user.id):
        raise HTTPException(status_code=403, detail="Esta avaliação não pertence à sua clínica")

    if not data.reason.strip():
        raise HTTPException(status_code=400, detail="Informe o motivo da denúncia")

    if data.category not in REPORT_CATEGORIES:
        raise HTTPException(status_code=400, detail="Categoria inválida")

    cat_label    = REPORT_CATEGORIES[data.category]
    reporter_name = getattr(user, "name", "Usuário")

    # Notificar todos os admins ativos
    admins = db.query(User).filter(User.role == "admin", User.is_active == True).all()
    for admin in admins:
        db.add(Notification(
            id=str(uuid.uuid4()),
            user_id=str(admin.id),
            user_type="admin",
            title="Nova denúncia de avaliação 🚨",
            message=f"{reporter_name}: {cat_label}",
            type="review_report",
            is_read=False,
            data=_json.dumps({
                "review_id":      review_id,
                "reporter_id":    str(user.id),
                "reporter_type":  user_type,
                "reporter_name":  reporter_name,
                "category":       data.category,
                "category_label": cat_label,
                "reason":         data.reason,
                "review_rating":  review.rating,
                "review_comment": review.comment or "",
            }),
        ))
    db.commit()
    return {"message": "Denúncia enviada. Nossa equipe analisará em até 48h."}
