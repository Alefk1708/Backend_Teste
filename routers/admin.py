# routers/admin.py - Backend completo para telas de admin

from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, desc, extract
from sqlalchemy.sql import text
from datetime import datetime, timedelta, date
from typing import Optional, List
from pydantic import BaseModel
import uuid

from database import get_db
from core.security import get_current_user, require_admin
from models.models import (
    User, Clinic, Appointment, Payment, ClinicReview, 
    EmergencyRequest, Notification, WithdrawalRequest,
    ClinicFinancialAccount, PlatformTransaction, Procedure,
    ClinicProcedure, ClinicEmergencyPrice, PlatformEmergencyPrice,
    TwoFactorAuth, ResetPasswordWithCode, ActionAttempts, UniqueEmail, EmergencyDecline
)

router = APIRouter(prefix="/admin", tags=["admin"])

# ========== SCHEMAS ==========

class DashboardStats(BaseModel):
    totalClinics: int
    pendingClinics: int
    totalPatients: int
    totalAppointments: int
    todayRevenue: float
    monthRevenue: float
    totalRevenue: float
    openTickets: int
    platformCommission: float
    recentActivities: Optional[List[dict]] = None

class ClinicResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    address: Optional[str]
    status: str
    rating: Optional[float]
    total_appointments: int
    total_revenue: float
    emergency_price: float
    avatar_url: Optional[str]
    owner_name: Optional[str]
    created_at: datetime
    is_active: bool
    is_online: bool

class PatientResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: str
    document: str
    status: str
    avatar_url: Optional[str]
    total_appointments: int
    completed_appointments: int
    cancelled_appointments: int
    last_appointment: Optional[datetime]
    created_at: datetime

class PatientHistory(BaseModel):
    total_appointments: int
    completed: int
    pending: int
    cancelled: int
    appointments: List[dict]
    reviews: List[dict]

class TicketResponse(BaseModel):
    id: str
    title: str
    description: str
    user_id: str
    user_name: str
    user_email: str
    user_type: str
    status: str
    priority: str
    category: str
    created_at: datetime
    updated_at: Optional[datetime]
    unread_messages: int
    messages: Optional[List[dict]] = None

class TicketReply(BaseModel):
    message: str

class TicketStatusUpdate(BaseModel):
    status: str

class FinancialReport(BaseModel):
    totalRevenue: float
    platformCommission: float
    clinicPayouts: float
    refunds: float
    chartData: dict
    topClinics: List[dict]
    recentTransactions: List[dict]

class ClinicStatusUpdate(BaseModel):
    status: str  # active, suspended, pending

class BroadcastMessage(BaseModel):
    target: str  # all, clinics, patients
    title: str
    body: str

# ========== HELPERS ==========

def get_clinic_status(clinic: Clinic) -> str:
    """Determina o status da clínica baseado nos campos is_active e outros"""
    if not clinic.is_active:
        return "suspended"
    return "active"

def get_patient_status(user: User) -> str:
    """Determina o status do paciente baseado no campo is_active"""
    if hasattr(user, 'is_active') and not user.is_active:
        return "suspended"
    return "active"

# ========== DASHBOARD ==========

@router.get("/dashboard", response_model=DashboardStats)
def get_admin_dashboard(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Retorna estatísticas gerais do dashboard admin"""
    
    # Total de clínicas
    total_clinics = db.query(Clinic).count()
    
    # Clínicas pendentes (is_active=False)
    pending_clinics = db.query(Clinic).filter(
        Clinic.is_active == False
    ).count()
    
    # Total de pacientes
    total_patients = db.query(User).filter(
        User.role == "paciente"
    ).count()
    
    # Total de agendamentos
    total_appointments = db.query(Appointment).count()
    
    # Receita de hoje
    today_start = datetime.combine(date.today(), datetime.min.time())
    today_end = datetime.combine(date.today(), datetime.max.time())
    
    today_revenue = db.query(func.sum(Payment.amount)).filter(
        Payment.status == "completed",
        Payment.paid_at >= today_start,
        Payment.paid_at <= today_end
    ).scalar() or 0.0
    
    # Receita do mês
    month_start = today_start.replace(day=1)
    month_revenue = db.query(func.sum(Payment.amount)).filter(
        Payment.status == "completed",
        Payment.paid_at >= month_start,
        Payment.paid_at <= today_end
    ).scalar() or 0.0
    
    # Receita total
    total_revenue = db.query(func.sum(Payment.amount)).filter(
        Payment.status == "completed"
    ).scalar() or 0.0
    
    # Comissão da plataforma
    platform_commission = db.query(func.sum(Appointment.platform_fee)).filter(
        Appointment.status == "completed"
    ).scalar() or 0.0
    
    # Tickets abertos — query direta na tabela support_tickets
    try:
        from sqlalchemy import text as sql_text
        open_tickets = db.execute(
            sql_text("SELECT COUNT(*) FROM support_tickets WHERE status IN ('open', 'in_progress')")
        ).scalar() or 0
    except Exception:
        open_tickets = 0
    
    # Atividades recentes
    recent_activities = []
    
    # Últimas clínicas cadastradas
    recent_clinics = db.query(Clinic).order_by(desc(Clinic.created_at)).limit(5).all()
    for clinic in recent_clinics:
        recent_activities.append({
            "type": "clinic",
            "description": f"Nova clínica cadastrada: {clinic.name}",
            "time": clinic.created_at.isoformat() if clinic.created_at else None
        })
    
    # Últimos agendamentos
    recent_apps = db.query(Appointment).order_by(desc(Appointment.created_at)).limit(5).all()
    for app in recent_apps:
        patient = db.query(User).filter(User.id == app.patient_id).first()
        clinic = db.query(Clinic).filter(Clinic.id == app.clinic_id).first()
        if patient and clinic:
            recent_activities.append({
                "type": "appointment",
                "description": f"Consulta: {patient.name} → {clinic.name}",
                "time": app.created_at.isoformat() if app.created_at else None
            })
    
    recent_activities.sort(key=lambda x: x["time"] if x["time"] else "", reverse=True)
    recent_activities = recent_activities[:10]
    
    return DashboardStats(
        totalClinics=total_clinics,
        pendingClinics=pending_clinics,
        totalPatients=total_patients,
        totalAppointments=total_appointments,
        todayRevenue=float(today_revenue),
        monthRevenue=float(month_revenue),
        totalRevenue=float(total_revenue),
        openTickets=open_tickets,
        platformCommission=float(platform_commission),
        recentActivities=recent_activities
    )

# ========== CLINICAS ==========

@router.get("/clinics", response_model=List[dict])
def get_admin_clinics(
    status: Optional[str] = Query(None, description="Filtrar por status: all, pending, active, suspended"),
    search: Optional[str] = Query(None, description="Buscar por nome ou email"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Lista todas as clínicas com filtros e busca"""
    
    query = db.query(Clinic)
    
    if status == "pending":
        query = query.filter(Clinic.is_active == False)
    elif status == "active":
        query = query.filter(Clinic.is_active == True)
    elif status == "suspended":
        query = query.filter(Clinic.is_active == False)
    
    if search:
        search_filter = or_(
            Clinic.name.ilike(f"%{search}%"),
            Clinic.email.ilike(f"%{search}%"),
            Clinic.cnpj.ilike(f"%{search}%")
        )
        query = query.filter(search_filter)
    
    clinics = query.order_by(desc(Clinic.created_at)).offset(offset).limit(limit).all()
    
    result = []
    for clinic in clinics:
        total_apps = db.query(Appointment).filter(
            Appointment.clinic_id == clinic.id
        ).count()
        
        total_revenue = db.query(func.sum(Appointment.clinic_amount)).filter(
            Appointment.clinic_id == clinic.id,
            Appointment.status == "completed"
        ).scalar() or 0.0
        
        avg_rating = db.query(func.avg(ClinicReview.rating)).filter(
            ClinicReview.clinic_id == clinic.id
        ).scalar()
        
        emergency_price = db.query(ClinicEmergencyPrice).filter(
            ClinicEmergencyPrice.clinic_id == clinic.id
        ).first()
        
        clinic_status = "active" if clinic.is_active else "pending"
        
        result.append({
            "id": clinic.id,
            "name": clinic.name,
            "email": clinic.email,
            "phone": clinic.phone,
            "address": clinic.address,
            "status": clinic_status,
            "rating": round(float(avg_rating), 1) if avg_rating else 4.5,
            "total_appointments": total_apps,
            "total_revenue": float(total_revenue),
            "emergency_price": emergency_price.price if emergency_price else 99.99,
            "avatar_url": clinic.avatar_url,
            "owner_name": clinic.name,
            "created_at": clinic.created_at,
            "is_active": clinic.is_active,
            "is_online": clinic.is_online
        })
    
    return result

@router.patch("/clinics/{clinic_id}/approve")
def approve_clinic(
    clinic_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Aprova uma clínica pendente"""
    clinic = db.query(Clinic).filter(Clinic.id == clinic_id).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")
    
    clinic.is_active = True
    db.commit()
    
    notification = Notification(
        id=str(uuid.uuid4()),
        user_id=clinic_id,
        user_type="clinica",
        title="Clínica Aprovada!",
        message="Sua clínica foi aprovada e já está visível para pacientes.",
        type="clinic_approved"
    )
    db.add(notification)
    db.commit()
    
    return {"message": "Clínica aprovada com sucesso"}

@router.patch("/clinics/{clinic_id}/suspend")
def suspend_clinic(
    clinic_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Suspende uma clínica ativa"""
    clinic = db.query(Clinic).filter(Clinic.id == clinic_id).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")
    
    clinic.is_active = False
    clinic.is_online = False  # Força offline também
    
    # Cancelar agendamentos futuros
    future_apps = db.query(Appointment).filter(
        Appointment.clinic_id == clinic_id,
        Appointment.scheduled_at > datetime.utcnow(),
        Appointment.status.in_(["pending", "confirmed", "awaiting_payment"])
    ).all()
    
    for app in future_apps:
        app.status = "cancelled"
        app.cancellation_reason = "Clínica suspensa pelo administrador"
        
        # Reembolsar
        payment = db.query(Payment).filter(
            Payment.appointment_id == app.id,
            Payment.status == "completed"
        ).first()
        if payment:
            payment.status = "refunded"
            payment.refunded_at = datetime.utcnow()
    
    db.commit()
    
    # Notificar clínica
    notification = Notification(
        id=str(uuid.uuid4()),
        user_id=clinic_id,
        user_type="clinica",
        title="Clínica Suspensa",
        message="Sua clínica foi suspensa. Entre em contato com o suporte.",
        type="clinic_suspended"
    )
    db.add(notification)
    db.commit()
    
    return {
        "message": "Clínica suspensa com sucesso",
        "cancelled_appointments": len(future_apps)
    }

@router.delete("/clinics/{clinic_id}")
def delete_clinic(
    clinic_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Exclui permanentemente uma clínica e TODOS os dados vinculados"""
    clinic = db.query(Clinic).filter(Clinic.id == clinic_id).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")
    
    # O cascade nos relacionamentos vai deletar:
    # - appointments (e payments, reviews via cascade)
    # - clinic_procedures
    # - emergency_price
    # - financial_account
    # - withdrawal_requests
    
    # Deletar notificações
    db.query(Notification).filter(
        Notification.user_id == clinic_id,
        Notification.user_type == "clinica"
    ).delete(synchronize_session=False)
    
    # Deletar registros de autenticação
    db.query(TwoFactorAuth).filter(
        TwoFactorAuth.entity_id == clinic_id,
        TwoFactorAuth.entity_type == "clinica"
    ).delete(synchronize_session=False)
    
    db.query(ResetPasswordWithCode).filter(
        ResetPasswordWithCode.entity_id == clinic_id,
        ResetPasswordWithCode.entity_type == "clinica"
    ).delete(synchronize_session=False)
    
    db.query(ActionAttempts).filter(
        ActionAttempts.entity_id == clinic_id
    ).delete(synchronize_session=False)
    
    # Deletar registros de emergência
    db.query(EmergencyDecline).filter(
        EmergencyDecline.clinic_id == clinic_id
    ).delete(synchronize_session=False)
    
    # Deletar email único
    db.query(UniqueEmail).filter(
        UniqueEmail.entity_id == clinic_id
    ).delete(synchronize_session=False)
    
    # Deletar a clínica (cascade faz o resto)
    db.delete(clinic)
    db.commit()
    
    return {
        "message": "Clínica e todos os dados vinculados removidos com sucesso",
        "deleted_clinic_id": clinic_id
    }

# ========== PACIENTES ==========

@router.get("/patients", response_model=List[dict])
def get_admin_patients(
    status: Optional[str] = Query(None, description="Filtrar por status: all, active, suspended"),
    search: Optional[str] = Query(None, description="Buscar por nome, email ou CPF"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Lista todos os pacientes com filtros e busca"""
    
    query = db.query(User).filter(User.role == "paciente")
    
    # ========== FILTRO POR STATUS ==========
    if status == "active":
        query = query.filter(User.is_active == True)
    elif status == "suspended":
        query = query.filter(User.is_active == False)
    
    if search:
        search_filter = or_(
            User.name.ilike(f"%{search}%"),
            User.email.ilike(f"%{search}%"),
            User.cpf.ilike(f"%{search}%")
        )
        query = query.filter(search_filter)
    
    patients = query.order_by(desc(User.created_at)).offset(offset).limit(limit).all()
    
    result = []
    for patient in patients:
        total_apps = db.query(Appointment).filter(
            Appointment.patient_id == patient.id
        ).count()
        
        completed_apps = db.query(Appointment).filter(
            Appointment.patient_id == patient.id,
            Appointment.status == "completed"
        ).count()
        
        cancelled_apps = db.query(Appointment).filter(
            Appointment.patient_id == patient.id,
            Appointment.status == "cancelled"
        ).count()
        
        last_app = db.query(Appointment).filter(
            Appointment.patient_id == patient.id
        ).order_by(desc(Appointment.scheduled_at)).first()
        
        result.append({
            "id": patient.id,
            "name": patient.name,
            "email": patient.email,
            "phone": patient.phone,
            "document": patient.cpf,
            "status": "active" if patient.is_active else "suspended",  # ✅ CORRIGIDO
            "avatar_url": patient.avatar_url,
            "total_appointments": total_apps,
            "completed_appointments": completed_apps,
            "cancelled_appointments": cancelled_apps,
            "last_appointment": last_app.scheduled_at if last_app else None,
            "created_at": patient.created_at
        })
    
    return result

@router.get("/patients/{patient_id}/history", response_model=PatientHistory)
def get_patient_history(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Retorna histórico completo de um paciente"""
    
    patient = db.query(User).filter(User.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    
    appointments = db.query(Appointment).filter(
        Appointment.patient_id == patient_id
    ).order_by(desc(Appointment.scheduled_at)).all()
    
    appointment_list = []
    for app in appointments:
        clinic = db.query(Clinic).filter(Clinic.id == app.clinic_id).first()
        procedure = db.query(Procedure).filter(Procedure.id == app.procedure_id).first()
        
        appointment_list.append({
            "id": app.id,
            "clinic_name": clinic.name if clinic else "Clínica não encontrada",
            "procedure_name": procedure.name if procedure else "Consulta",
            "status": app.status,
            "scheduled_at": app.scheduled_at,
            "completed_at": app.completed_at,
            "total_amount": app.total_amount
        })
    
    reviews = db.query(ClinicReview).filter(
        ClinicReview.patient_id == patient_id
    ).order_by(desc(ClinicReview.created_at)).all()
    
    review_list = []
    for review in reviews:
        clinic = db.query(Clinic).filter(Clinic.id == review.clinic_id).first()
        review_list.append({
            "id": review.id,
            "clinic_name": clinic.name if clinic else "Clínica",
            "rating": review.rating,
            "comment": review.comment,
            "created_at": review.created_at
        })
    
    completed = len([a for a in appointments if a.status == "completed"])
    pending = len([a for a in appointments if a.status in ["pending", "confirmed", "awaiting_payment"]])
    cancelled = len([a for a in appointments if a.status == "cancelled"])
    
    return PatientHistory(
        total_appointments=len(appointments),
        completed=completed,
        pending=pending,
        cancelled=cancelled,
        appointments=appointment_list,
        reviews=review_list
    )

@router.patch("/patients/{patient_id}/suspend")
def suspend_patient(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Suspende um paciente - impede login e acesso"""
    patient = db.query(User).filter(User.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    
    # Cancelar agendamentos futuros
    future_apps = db.query(Appointment).filter(
        Appointment.patient_id == patient_id,
        Appointment.scheduled_at > datetime.utcnow(),
        Appointment.status.in_(["pending", "confirmed", "awaiting_payment"])
    ).all()
    
    for app in future_apps:
        app.status = "cancelled"
        app.cancellation_reason = "Paciente suspenso pelo administrador"
        
        # Reembolsar pagamentos pendentes
        payment = db.query(Payment).filter(
            Payment.appointment_id == app.id,
            Payment.status == "completed"
        ).first()
        if payment:
            payment.status = "refunded"
            payment.refunded_at = datetime.utcnow()
    
    # Marcar como inativo (is_active = False)
    patient.is_active = False
    
    db.commit()
    
    return {
        "message": "Paciente suspenso com sucesso",
        "cancelled_appointments": len(future_apps)
    }

@router.patch("/patients/{patient_id}/activate")
def activate_patient(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Reativa um paciente suspenso"""
    patient = db.query(User).filter(User.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    
    patient.is_active = True
    db.commit()
    
    return {"message": "Paciente reativado com sucesso"}

@router.delete("/patients/{patient_id}")
def delete_patient(
    patient_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Exclui permanentemente um paciente e TODOS os dados vinculados (GDPR compliance)"""
    patient = db.query(User).filter(User.id == patient_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    
    # O cascade="all, delete-orphan" nos relacionamentos vai automaticamente:
    # - Deletar todos os appointments do paciente
    # - Deletar todos os payments (via cascade em Appointment)
    # - Deletar todos os reviews do paciente
    # - Deletar todos os emergency_requests do paciente
    
    # Deletar notificações do paciente
    db.query(Notification).filter(
        Notification.user_id == patient_id,
        Notification.user_type == "paciente"
    ).delete(synchronize_session=False)
    
    # Deletar registros de autenticação
    db.query(TwoFactorAuth).filter(
        TwoFactorAuth.entity_id == patient_id,
        TwoFactorAuth.entity_type == "paciente"
    ).delete(synchronize_session=False)
    
    db.query(ResetPasswordWithCode).filter(
        ResetPasswordWithCode.entity_id == patient_id,
        ResetPasswordWithCode.entity_type == "paciente"
    ).delete(synchronize_session=False)
    
    db.query(ActionAttempts).filter(
        ActionAttempts.entity_id == patient_id
    ).delete(synchronize_session=False)
    
    # Deletar email do registro de emails únicos
    db.query(UniqueEmail).filter(
        UniqueEmail.entity_id == patient_id
    ).delete(synchronize_session=False)
    
    # Finalmente, deletar o paciente (cascade cuida do resto)
    db.delete(patient)
    db.commit()
    
    return {
        "message": "Paciente e todos os dados vinculados removidos com sucesso",
        "deleted_patient_id": patient_id
    }

# ========== FINANCEIRO ==========

@router.get("/financial/reports", response_model=FinancialReport)
def get_financial_reports(
    period: str = Query("month", description="Período: week, month, year"),
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Retorna relatórios financeiros detalhados"""
    
    now = datetime.utcnow()
    
    if period == "week":
        start_date = now - timedelta(days=7)
    elif period == "year":
        start_date = now - timedelta(days=365)
    else:
        start_date = now - timedelta(days=30)
    
    total_revenue = db.query(func.sum(Payment.amount)).filter(
        Payment.status == "completed",
        Payment.paid_at >= start_date,
        Payment.paid_at <= now
    ).scalar() or 0.0
    
    platform_commission = db.query(func.sum(Appointment.platform_fee)).filter(
        Appointment.status == "completed",
        Appointment.completed_at >= start_date,
        Appointment.completed_at <= now
    ).scalar() or 0.0
    
    clinic_payouts = db.query(func.sum(Appointment.clinic_amount)).filter(
        Appointment.status == "completed",
        Appointment.completed_at >= start_date,
        Appointment.completed_at <= now
    ).scalar() or 0.0
    
    refunds = db.query(func.sum(Payment.amount)).filter(
        Payment.status == "refunded",
        Payment.refunded_at >= start_date,
        Payment.refunded_at <= now
    ).scalar() or 0.0
    
    chart_data = {"labels": [], "datasets": [{"data": []}]}
    
    if period == "week":
        for i in range(6, -1, -1):
            day = now - timedelta(days=i)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day.replace(hour=23, minute=59, second=59)
            
            day_revenue = db.query(func.sum(Payment.amount)).filter(
                Payment.status == "completed",
                Payment.paid_at >= day_start,
                Payment.paid_at <= day_end
            ).scalar() or 0.0
            
            day_names = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]
            chart_data["labels"].append(day_names[day.weekday()])
            chart_data["datasets"][0]["data"].append(float(day_revenue))
    
    elif period == "month":
        for i in range(29, -1, -1):
            day = now - timedelta(days=i)
            day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day.replace(hour=23, minute=59, second=59)
            
            day_revenue = db.query(func.sum(Payment.amount)).filter(
                Payment.status == "completed",
                Payment.paid_at >= day_start,
                Payment.paid_at <= day_end
            ).scalar() or 0.0
            
            if i % 5 == 0 or i == 0:
                chart_data["labels"].append(day.strftime("%d/%m"))
                chart_data["datasets"][0]["data"].append(float(day_revenue))
    
    else:
        for i in range(11, -1, -1):
            month_date = now - timedelta(days=i*30)
            month_start = month_date.replace(day=1, hour=0, minute=0, second=0)
            month_end = (month_date.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(seconds=1)
            
            month_revenue = db.query(func.sum(Payment.amount)).filter(
                Payment.status == "completed",
                Payment.paid_at >= month_start,
                Payment.paid_at <= month_end
            ).scalar() or 0.0
            
            month_names = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", 
                          "Jul", "Ago", "Set", "Out", "Nov", "Dez"]
            chart_data["labels"].append(month_names[month_date.month - 1])
            chart_data["datasets"][0]["data"].append(float(month_revenue))
    
    top_clinics_query = db.query(
        Clinic.id,
        Clinic.name,
        func.count(Appointment.id).label("total_appointments"),
        func.sum(Appointment.clinic_amount).label("revenue")
    ).join(Appointment, Clinic.id == Appointment.clinic_id).filter(
        Appointment.status == "completed",
        Appointment.completed_at >= start_date
    ).group_by(Clinic.id).order_by(desc("revenue")).limit(10).all()
    
    top_clinics = []
    for clinic_data in top_clinics_query:
        top_clinics.append({
            "id": clinic_data.id,
            "name": clinic_data.name,
            "total_appointments": clinic_data.total_appointments or 0,
            "revenue": float(clinic_data.revenue or 0)
        })
    
    recent_transactions = db.query(Payment, Appointment).join(
        Appointment, Payment.appointment_id == Appointment.id
    ).filter(
        Payment.created_at >= start_date
    ).order_by(desc(Payment.created_at)).limit(20).all()
    
    transactions_list = []
    for pay, app in recent_transactions:
        patient = db.query(User).filter(User.id == app.patient_id).first()
        clinic = db.query(Clinic).filter(Clinic.id == app.clinic_id).first()
        
        trans_type = "payment"
        if pay.status == "refunded":
            trans_type = "refund"
        elif pay.status == "pending":
            trans_type = "pending"
        
        transactions_list.append({
            "id": pay.id,
            "type": trans_type,
            "amount": float(pay.amount),
            "description": f"{patient.name if patient else 'Paciente'} → {clinic.name if clinic else 'Clínica'}",
            "date": pay.paid_at.strftime("%d/%m/%Y %H:%M") if pay.paid_at else pay.created_at.strftime("%d/%m/%Y"),
            "status": pay.status
        })
    
    return FinancialReport(
        totalRevenue=float(total_revenue),
        platformCommission=float(platform_commission),
        clinicPayouts=float(clinic_payouts),
        refunds=float(refunds),
        chartData=chart_data,
        topClinics=top_clinics,
        recentTransactions=transactions_list
    )

# ========== SUPORTE / TICKETS ==========

MOCK_TICKETS = [
    {
        "id": "ticket_001",
        "title": "Problema com pagamento PIX",
        "description": "Meu pagamento via PIX não foi confirmado automaticamente.",
        "user_id": "user_001",
        "user_name": "João Silva",
        "user_email": "joao@email.com",
        "user_type": "paciente",
        "status": "open",
        "priority": "high",
        "category": "financial",
        "created_at": datetime.utcnow() - timedelta(hours=2),
        "unread_messages": 1,
        "messages": [
            {
                "text": "Meu pagamento via PIX não foi confirmado automaticamente.",
                "is_admin": False,
                "created_at": datetime.utcnow() - timedelta(hours=2)
            }
        ]
    },
    {
        "id": "ticket_002",
        "title": "Como alterar preço de urgência?",
        "description": "Gostaria de saber como posso alterar o valor cobrado para atendimentos de urgência.",
        "user_id": "clinic_001",
        "user_name": "Clínica Dental Plus",
        "user_email": "contato@dentalplus.com",
        "user_type": "clinica",
        "status": "in_progress",
        "priority": "medium",
        "category": "question",
        "created_at": datetime.utcnow() - timedelta(days=1),
        "unread_messages": 0,
        "messages": [
            {
                "text": "Gostaria de saber como posso alterar o valor cobrado para atendimentos de urgência.",
                "is_admin": False,
                "created_at": datetime.utcnow() - timedelta(days=1)
            },
            {
                "text": "Olá! Você pode alterar o preço de urgência em Configurações > Preços > Urgência.",
                "is_admin": True,
                "created_at": datetime.utcnow() - timedelta(hours=20)
            }
        ]
    }
]

@router.get("/support/tickets", response_model=List[dict])
def get_support_tickets(
    status: Optional[str] = Query("all", description="Filtrar por status"),
    priority: Optional[str] = Query("all", description="Filtrar por prioridade"),
    search: Optional[str] = Query(None, description="Buscar por título ou usuário"),
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Lista todos os tickets de suporte"""
    
    tickets = MOCK_TICKETS.copy()
    
    if status != "all":
        tickets = [t for t in tickets if t["status"] == status]
    
    if priority != "all":
        tickets = [t for t in tickets if t["priority"] == priority]
    
    if search:
        search_lower = search.lower()
        tickets = [t for t in tickets if 
                   search_lower in t["title"].lower() or 
                   search_lower in t["user_name"].lower()]
    
    return tickets

@router.get("/support/tickets/{ticket_id}", response_model=dict)
def get_ticket_detail(
    ticket_id: str,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Retorna detalhes de um ticket específico"""
    
    ticket = next((t for t in MOCK_TICKETS if t["id"] == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket não encontrado")
    
    return ticket

@router.post("/support/tickets/{ticket_id}/reply")
def reply_to_ticket(
    ticket_id: str,
    reply: TicketReply,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Responde a um ticket de suporte"""
    
    ticket = next((t for t in MOCK_TICKETS if t["id"] == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket não encontrado")
    
    ticket["messages"].append({
        "text": reply.message,
        "is_admin": True,
        "created_at": datetime.utcnow()
    })
    
    ticket["status"] = "in_progress"
    ticket["unread_messages"] = 0
    
    return {"message": "Resposta enviada com sucesso"}

@router.patch("/support/tickets/{ticket_id}/status")
def update_ticket_status(
    ticket_id: str,
    update: TicketStatusUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Atualiza o status de um ticket"""
    
    ticket = next((t for t in MOCK_TICKETS if t["id"] == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket não encontrado")
    
    ticket["status"] = update.status
    
    return {"message": f"Status atualizado para {update.status}"}

# ========== ENDPOINTS ADICIONAIS ==========

@router.get("/stats/overview")
def get_platform_overview(
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Visão geral da plataforma para admin"""
    
    now = datetime.utcnow()
    today_start = datetime.combine(date.today(), datetime.min.time())
    
    yesterday_start = today_start - timedelta(days=1)
    yesterday_end = today_start - timedelta(seconds=1)
    
    today_apps = db.query(Appointment).filter(
        Appointment.created_at >= today_start
    ).count()
    
    yesterday_apps = db.query(Appointment).filter(
        Appointment.created_at >= yesterday_start,
        Appointment.created_at <= yesterday_end
    ).count()
    
    growth = ((today_apps - yesterday_apps) / yesterday_apps * 100) if yesterday_apps > 0 else 0
    
    return {
        "today": {
            "new_appointments": today_apps,
            "new_users": db.query(User).filter(
                User.created_at >= today_start
            ).count(),
            "new_clinics": db.query(Clinic).filter(
                Clinic.created_at >= today_start
            ).count(),
            "revenue": float(db.query(func.sum(Payment.amount)).filter(
                Payment.status == "completed",
                Payment.paid_at >= today_start
            ).scalar() or 0.0)
        },
        "growth_percentage": round(growth, 1),
        "platform_health": {
            "active_clinics": db.query(Clinic).filter(Clinic.is_active == True).count(),
            "online_clinics": db.query(Clinic).filter(Clinic.is_online == True).count(),
            "total_users": db.query(User).filter(User.role == "paciente").count(),
            "pending_withdrawals": db.query(WithdrawalRequest).filter(
                WithdrawalRequest.status == "pending"
            ).count()
        }
    }

@router.post("/broadcast")
def send_broadcast_message(
    message: BroadcastMessage,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user = Depends(require_admin)
):
    """Envia mensagem broadcast para todos os usuários ou tipo específico"""
    
    target = message.target
    title = message.title
    body = message.body
    
    if target in ["all", "clinics"]:
        clinics = db.query(Clinic).all()
        for clinic in clinics:
            notification = Notification(
                id=str(uuid.uuid4()),
                user_id=clinic.id,
                user_type="clinica",
                title=title,
                message=body,
                type="broadcast"
            )
            db.add(notification)
    
    if target in ["all", "patients"]:
        patients = db.query(User).filter(User.role == "paciente").all()
        for patient in patients:
            notification = Notification(
                id=str(uuid.uuid4()),
                user_id=patient.id,
                user_type="paciente",
                title=title,
                message=body,
                type="broadcast"
            )
            db.add(notification)
    
    db.commit()
    
    return {
        "message": "Broadcast enviado com sucesso",
        "target": target,
        "recipients": len(clinics if target in ["all", "clinics"] else []) + 
                      len(patients if target in ["all", "patients"] else [])
    }

# ════════════════════════════════════════════════════════════
# PROCEDIMENTOS GLOBAIS — admin CRUD
# ════════════════════════════════════════════════════════════

class ProcedureCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: float
    duration_minutes: int
    category: str = "consulta"
    is_active: bool = True
    # Campos para lentes de contato (obrigatórios se category == "lentes_contato")
    max_upper_teeth: Optional[int] = None
    max_lower_teeth: Optional[int] = None

class ProcedureUpdate(ProcedureCreate):
    pass

class ProcedureToggle(BaseModel):
    is_active: bool


@router.get("/procedures")
def list_global_procedures(
    is_active: Optional[bool] = None,
    category: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Lista todos os procedimentos globais da plataforma.
    Acessível por admin (gestão) e clínicas (para ativar/desativar).
    """
    user_type = current_user["payload"]["type"]
    if user_type not in ("admin", "clinica"):
        raise HTTPException(status_code=403, detail="Acesso negado")

    query = db.query(Procedure)
    if is_active is not None:
        query = query.filter(Procedure.is_active == is_active)
    if category:
        query = query.filter(Procedure.category == category)

    procs = query.order_by(Procedure.category, Procedure.name).all()

    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "category": p.category,
            "price": p.price,
            "duration_minutes": p.default_duration_minutes,
            "is_active": p.is_active,
            "max_upper_teeth": p.max_upper_teeth,
            "max_lower_teeth": p.max_lower_teeth,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }
        for p in procs
    ]


@router.post("/procedures")
def create_global_procedure(
    data: ProcedureCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Admin cria um procedimento global. Clínicas NÃO têm acesso a este endpoint."""

    # Validação específica para lentes de contato
    if data.category == "lentes_contato":
        if not data.max_upper_teeth or not data.max_lower_teeth:
            raise HTTPException(
                status_code=400,
                detail="Lentes de contato exigem max_upper_teeth e max_lower_teeth (1–6)",
            )
        if not (1 <= data.max_upper_teeth <= 6) or not (1 <= data.max_lower_teeth <= 6):
            raise HTTPException(status_code=400, detail="Quantidade de dentes deve ser entre 1 e 6")

    if data.price <= 0:
        raise HTTPException(status_code=400, detail="Preço deve ser maior que zero")

    proc = Procedure(
        id=str(uuid.uuid4()),
        name=data.name.strip(),
        description=data.description,
        category=data.category,
        price=data.price,
        default_duration_minutes=data.duration_minutes,
        is_active=data.is_active,
        max_upper_teeth=data.max_upper_teeth if data.category == "lentes_contato" else None,
        max_lower_teeth=data.max_lower_teeth if data.category == "lentes_contato" else None,
    )
    db.add(proc)
    db.commit()
    db.refresh(proc)

    return {
        "message": "Procedimento criado com sucesso",
        "id": proc.id,
        "name": proc.name,
    }


@router.put("/procedures/{procedure_id}")
def update_global_procedure(
    procedure_id: str,
    data: ProcedureUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Admin edita um procedimento global."""
    proc = db.query(Procedure).filter(Procedure.id == procedure_id).first()
    if not proc:
        raise HTTPException(status_code=404, detail="Procedimento não encontrado")

    if data.category == "lentes_contato":
        if not data.max_upper_teeth or not data.max_lower_teeth:
            raise HTTPException(
                status_code=400,
                detail="Lentes de contato exigem max_upper_teeth e max_lower_teeth",
            )
        if not (1 <= data.max_upper_teeth <= 6) or not (1 <= data.max_lower_teeth <= 6):
            raise HTTPException(status_code=400, detail="Quantidade de dentes deve ser entre 1 e 6")

    proc.name = data.name.strip()
    proc.description = data.description
    proc.category = data.category
    proc.price = data.price
    proc.default_duration_minutes = data.duration_minutes
    proc.is_active = data.is_active
    proc.max_upper_teeth = data.max_upper_teeth if data.category == "lentes_contato" else None
    proc.max_lower_teeth = data.max_lower_teeth if data.category == "lentes_contato" else None

    db.commit()
    return {"message": "Procedimento atualizado com sucesso"}


@router.patch("/procedures/{procedure_id}/toggle")
def toggle_global_procedure(
    procedure_id: str,
    data: ProcedureToggle,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Admin ativa ou desativa globalmente. Clínicas que tinham ativo deixam de exibir."""
    proc = db.query(Procedure).filter(Procedure.id == procedure_id).first()
    if not proc:
        raise HTTPException(status_code=404, detail="Procedimento não encontrado")

    proc.is_active = data.is_active
    db.commit()
    return {"message": "Status atualizado", "is_active": proc.is_active}


@router.delete("/procedures/{procedure_id}")
def delete_global_procedure(
    procedure_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """
    Admin exclui um procedimento global.
    Remove também todos os ClinicProcedure relacionados (cascade).
    """
    proc = db.query(Procedure).filter(Procedure.id == procedure_id).first()
    if not proc:
        raise HTTPException(status_code=404, detail="Procedimento não encontrado")

    # Verificar se há agendamentos ativos com este procedimento
    active_appointments = db.query(Appointment).filter(
        Appointment.procedure_id == procedure_id,
        Appointment.status.in_(["pending", "confirmed", "awaiting_payment"]),
    ).count()

    if active_appointments > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Não é possível excluir: há {active_appointments} agendamento(s) ativo(s) com este procedimento.",
        )

    db.delete(proc)
    db.commit()
    return {"message": "Procedimento excluído com sucesso"}


# ════════════════════════════════════════════════════════════
# PREÇO GLOBAL DE EMERGÊNCIA — admin define, clínicas só leem
# ════════════════════════════════════════════════════════════

class EmergencyPriceUpdate(BaseModel):
    price: float


def _get_or_create_platform_price(db: Session) -> PlatformEmergencyPrice:
    record = db.query(PlatformEmergencyPrice).first()
    if not record:
        record = PlatformEmergencyPrice(id=str(uuid.uuid4()), price=99.99)
        db.add(record)
        db.commit()
        db.refresh(record)
    return record


@router.get("/emergency-price")
def get_platform_emergency_price_admin(
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Admin lê o preço atual de urgência da plataforma."""
    record = _get_or_create_platform_price(db)
    return {
        "price": record.price,
        "updated_at": record.updated_at,
        "updated_by": record.updated_by,
    }


@router.put("/emergency-price")
def set_platform_emergency_price(
    data: EmergencyPriceUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Admin define o preço único de urgência para toda a plataforma."""
    if data.price <= 0:
        raise HTTPException(status_code=400, detail="Preço deve ser maior que zero")

    admin_user = current_user["user"]
    record = _get_or_create_platform_price(db)
    record.price = data.price
    record.updated_by = str(admin_user.id)
    db.commit()

    return {
        "message": "Preço de urgência atualizado com sucesso",
        "price": record.price,
    }


# ════════════════════════════════════════════════════════════
# ADMINISTRADORES — admin pode criar, listar, suspender
# ════════════════════════════════════════════════════════════

class AdminCreate(BaseModel):
    name: str
    email: str
    cpf: str
    phone: str
    password: str

class AdminUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None

@router.get("/admins")
def list_admins(
    search: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Lista todos os administradores da plataforma."""
    from core.security import get_current_user as _gcu
    query = db.query(User).filter(User.role == "admin")
    if search:
        query = query.filter(
            or_(
                User.name.ilike(f"%{search}%"),
                User.email.ilike(f"%{search}%"),
                User.cpf.ilike(f"%{search}%"),
            )
        )
    admins = query.order_by(desc(User.created_at)).all()
    current_id = str(current_user["user"].id)
    return [
        {
            "id": a.id,
            "name": a.name,
            "email": a.email,
            "phone": a.phone,
            "cpf": a.cpf,
            "is_active": a.is_active,
            "is_current": a.id == current_id,
            "created_at": a.created_at,
        }
        for a in admins
    ]


@router.post("/admins", status_code=201)
def create_admin(
    data: AdminCreate,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Admin cria outro administrador."""
    from core.security import hash_password
    from utils.cpf import is_valid_cpf

    # Validação de CPF
    cpf_clean = data.cpf.replace(".", "").replace("-", "").replace(" ", "")
    if not is_valid_cpf(cpf_clean):
        raise HTTPException(status_code=400, detail="CPF inválido")

    # Verificar unicidade de email
    if db.query(UniqueEmail).filter(UniqueEmail.email == data.email).first():
        raise HTTPException(status_code=400, detail="E-mail já cadastrado na plataforma")

    # Verificar unicidade de CPF
    if db.query(User).filter(User.cpf == cpf_clean).first():
        raise HTTPException(status_code=400, detail="CPF já cadastrado")

    if len(data.password) < 8:
        raise HTTPException(status_code=400, detail="A senha deve ter no mínimo 8 caracteres")

    admin = User(
        id=str(uuid.uuid4()),
        name=data.name.strip(),
        email=data.email.strip().lower(),
        password_hash=hash_password(data.password),
        cpf=cpf_clean,
        phone=data.phone.strip(),
        role="admin",
        is_admin=True,
        is_active=True,
    )
    db.add(admin)
    db.add(UniqueEmail(
        email=admin.email,
        entity_type="admin",
        entity_id=admin.id,
    ))
    db.commit()
    db.refresh(admin)

    return {
        "message": "Administrador criado com sucesso",
        "id": admin.id,
        "name": admin.name,
        "email": admin.email,
    }


@router.patch("/admins/{admin_id}/suspend")
def suspend_admin(
    admin_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Suspende um administrador. Não é possível suspender a si mesmo."""
    if str(current_user["user"].id) == admin_id:
        raise HTTPException(status_code=400, detail="Você não pode suspender sua própria conta")

    admin = db.query(User).filter(User.id == admin_id, User.role == "admin").first()
    if not admin:
        raise HTTPException(status_code=404, detail="Administrador não encontrado")

    admin.is_active = False
    db.commit()
    return {"message": "Administrador suspenso com sucesso"}


@router.patch("/admins/{admin_id}/activate")
def activate_admin(
    admin_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Reativa um administrador suspenso."""
    admin = db.query(User).filter(User.id == admin_id, User.role == "admin").first()
    if not admin:
        raise HTTPException(status_code=404, detail="Administrador não encontrado")

    admin.is_active = True
    db.commit()
    return {"message": "Administrador reativado com sucesso"}


@router.patch("/admins/{admin_id}")
def update_admin(
    admin_id: str,
    data: AdminUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Admin edita nome/telefone de outro admin (ou de si mesmo)."""
    admin = db.query(User).filter(User.id == admin_id, User.role == "admin").first()
    if not admin:
        raise HTTPException(status_code=404, detail="Administrador não encontrado")

    if data.name is not None:
        admin.name = data.name.strip()
    if data.phone is not None:
        admin.phone = data.phone.strip()

    db.commit()
    return {"message": "Administrador atualizado com sucesso"}


@router.delete("/admins/{admin_id}")
def delete_admin(
    admin_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Remove permanentemente um administrador. Não pode remover a si mesmo."""
    if str(current_user["user"].id) == admin_id:
        raise HTTPException(status_code=400, detail="Você não pode remover sua própria conta")

    # Impede remover o último admin ativo
    active_admins = db.query(User).filter(
        User.role == "admin",
        User.is_active == True,
        User.id != admin_id,
    ).count()
    if active_admins == 0:
        raise HTTPException(
            status_code=400,
            detail="Não é possível remover: este é o último administrador ativo da plataforma",
        )

    admin = db.query(User).filter(User.id == admin_id, User.role == "admin").first()
    if not admin:
        raise HTTPException(status_code=404, detail="Administrador não encontrado")

    db.query(TwoFactorAuth).filter(
        TwoFactorAuth.entity_id == admin_id,
        TwoFactorAuth.entity_type == "admin",
    ).delete(synchronize_session=False)
    db.query(ResetPasswordWithCode).filter(
        ResetPasswordWithCode.entity_id == admin_id,
        ResetPasswordWithCode.entity_type == "admin",
    ).delete(synchronize_session=False)
    db.query(ActionAttempts).filter(
        ActionAttempts.entity_id == admin_id
    ).delete(synchronize_session=False)
    db.query(UniqueEmail).filter(
        UniqueEmail.entity_id == admin_id
    ).delete(synchronize_session=False)

    db.delete(admin)
    db.commit()
    return {"message": "Administrador removido com sucesso"}
