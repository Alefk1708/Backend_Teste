# routers/financial.py - COMPLETO E CORRIGIDO

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, extract
from database import get_db
from core.security import get_current_user
from models.models import (
    Clinic, 
    Appointment, 
    ClinicFinancialAccount, 
    Payment,
    WithdrawalRequest
)
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta, date
from collections import defaultdict
import uuid

router = APIRouter(prefix="/financial", tags=["financial"])

# ========== SCHEMAS ==========

class WithdrawRequest(BaseModel):
    amount: float
    pix_key: Optional[str] = None  # Se None, usa a chave salva na conta

class BankAccountUpdate(BaseModel):
    pix_key: Optional[str] = None
    bank_code: Optional[str] = None
    agency: Optional[str] = None
    account: Optional[str] = None
    account_type: Optional[str] = None  # corrente | poupanca | pagamento

class WithdrawalResponse(BaseModel):
    id: str
    amount: float
    pix_key: str
    status: str
    created_at: datetime
    
    class Config:
        from_attributes = True

# ========== HELPERS ==========

def get_or_create_financial_account(db: Session, clinic_id: str):
    """Obtém ou cria conta financeira da clínica"""
    account = db.query(ClinicFinancialAccount).filter(
        ClinicFinancialAccount.clinic_id == clinic_id
    ).first()
    
    if not account:
        account = ClinicFinancialAccount(
            id=str(uuid.uuid4()),
            clinic_id=clinic_id,
            available_balance=0.0,
            pending_balance=0.0
        )
        db.add(account)
        db.commit()
        db.refresh(account)
    
    return account

def calculate_clinic_balance(db: Session, clinic_id: str):
    """Calcula saldo real baseado em appointments completados"""
    
    # Total já pago para clínica (disponível)
    paid_to_clinic = db.query(func.sum(Appointment.clinic_amount)).filter(
        Appointment.clinic_id == clinic_id,
        Appointment.status == "completed",
        Appointment.is_paid_to_clinic == True
    ).scalar() or 0.0
    
    # Total pendente de liberação (30 dias após conclusão)
    pending_threshold = datetime.utcnow() - timedelta(days=30)
    
    pending_balance = db.query(func.sum(Appointment.clinic_amount)).filter(
        Appointment.clinic_id == clinic_id,
        Appointment.status == "completed",
        Appointment.is_paid_to_clinic == False,
        Appointment.completed_at <= pending_threshold  # Já passou 30 dias
    ).scalar() or 0.0
    
    # Ainda bloqueado (menos de 30 dias)
    blocked_balance = db.query(func.sum(Appointment.clinic_amount)).filter(
        Appointment.clinic_id == clinic_id,
        Appointment.status == "completed",
        Appointment.is_paid_to_clinic == False,
        Appointment.completed_at > pending_threshold  # Ainda não passou 30 dias
    ).scalar() or 0.0
    
    # Total ganho no histórico
    total_earned = db.query(func.sum(Appointment.clinic_amount)).filter(
        Appointment.clinic_id == clinic_id,
        Appointment.status == "completed"
    ).scalar() or 0.0
    
    return {
        "available_balance": float(paid_to_clinic),   # Saldo já liberado e pago à clínica
        "pending_balance": float(pending_balance),    # Liberado (passou 30 dias) mas não sacado ainda
        "blocked_balance": float(blocked_balance),    # Em carência (menos de 30 dias)
        "total_earned": float(total_earned)
    }

# ========== ENDPOINTS ==========

@router.get("/clinic/balance")
def get_clinic_balance(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Retorna saldo financeiro da clínica.
    PERMITIDO offline (consulta de dados).
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Acesso negado")
    
    # Garantir que conta existe
    account = get_or_create_financial_account(db, str(user.id))
    
    # Calcular saldos reais
    balances = calculate_clinic_balance(db, str(user.id))
    
    # Sincronizar com tabela de conta (se necessário)
    if account.available_balance != balances["available_balance"]:
        account.available_balance = balances["available_balance"]
        db.commit()
    
    return {
        "available_balance": balances["available_balance"],
        "pending_balance": balances["pending_balance"],
        "blocked_balance": balances["blocked_balance"],
        "total_earned": balances["total_earned"],
        "pix_key_registered": account.pix_key,
        "bank_registered": bool(account.bank_code and account.account)
    }

@router.get("/clinic/transactions")
def get_clinic_transactions(
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,  # completed, pending, cancelled
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Histórico de atendimentos com valores financeiros.
    PERMITIDO offline (consulta de dados).
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Acesso negado")
    
    query = db.query(Appointment).filter(
        Appointment.clinic_id == user.id
    )
    
    if status:
        query = query.filter(Appointment.status == status)
    
    # Ordenar por mais recentes
    appointments = query.order_by(
        Appointment.created_at.desc()
    ).offset(offset).limit(limit).all()
    
    result = []
    for app in appointments:
        # Buscar dados do paciente
        from models.models import User
        patient = db.query(User).filter(User.id == app.patient_id).first()
        
        # Buscar procedimento
        from models.models import Procedure
        procedure = db.query(Procedure).filter(
            Procedure.id == app.procedure_id
        ).first() if app.procedure_id else None
        
        # Buscar pagamento relacionado
        payment = db.query(Payment).filter(
            Payment.appointment_id == app.id
        ).first()
        
        result.append({
            "id": app.id,
            "date": app.scheduled_at or app.created_at,
            "status": app.status,
            "service_type": app.service_type,
            "patient_name": patient.name if patient else "Paciente",
            "procedure_name": procedure.name if procedure else (
                "Primeira Consulta" if app.service_type == "first_consultation" else "Consulta"
            ),
            "total_amount": app.total_amount,
            "clinic_amount": app.clinic_amount,
            "platform_fee": app.platform_fee,
            "is_paid": app.is_paid_to_clinic,
            "paid_at": app.paid_to_clinic_at,
            "payment_method": payment.payment_method if payment else None,
            "can_withdraw": (
                app.status == "completed" and 
                app.is_paid_to_clinic and
                not app.paid_to_clinic_at  # Ainda não sacou
            )
        })
    
    return result

@router.get("/clinic/bank-account")
def get_bank_account(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Retorna os dados bancários cadastrados da clínica.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Acesso negado")

    account = get_or_create_financial_account(db, str(user.id))
    return {
        "pix_key": account.pix_key,
        "bank_code": account.bank_code,
        "agency": account.agency,
        "account": account.account,
        "account_type": account.account_type,
    }


@router.put("/clinic/bank-account")
def update_bank_account(
    data: BankAccountUpdate,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Salva ou atualiza os dados bancários da clínica.
    É necessário informar ao menos uma chave PIX ou dados bancários completos.
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Acesso negado")

    if not data.pix_key and not data.account:
        raise HTTPException(
            status_code=400,
            detail="Informe ao menos uma chave PIX ou número de conta"
        )

    account = get_or_create_financial_account(db, str(user.id))

    if data.pix_key is not None:
        account.pix_key = data.pix_key.strip() or None
    if data.bank_code is not None:
        account.bank_code = data.bank_code.strip() or None
    if data.agency is not None:
        account.agency = data.agency.strip() or None
    if data.account is not None:
        account.account = data.account.strip() or None
    if data.account_type is not None:
        account.account_type = data.account_type.strip() or None

    db.commit()
    db.refresh(account)

    return {
        "message": "Dados bancários atualizados com sucesso",
        "pix_key": account.pix_key,
        "bank_code": account.bank_code,
        "agency": account.agency,
        "account": account.account,
        "account_type": account.account_type,
    }


@router.post("/clinic/withdraw")
def request_withdrawal(
    data: WithdrawRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Solicita saque do saldo disponível.
    PERMITIDO offline (ação financeira administrativa).
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Acesso negado")
    
    # NÃO verifica is_online aqui - saque é permitido offline
    
    # Validações
    if data.amount <= 0:
        raise HTTPException(status_code=400, detail="Valor deve ser maior que zero")

    if data.amount < 10:
        raise HTTPException(status_code=400, detail="Valor mínimo para saque é R$ 10,00")

    # Usa a chave PIX fornecida ou a chave cadastrada na conta
    pix_key_to_use = data.pix_key
    if not pix_key_to_use:
        account_check = get_or_create_financial_account(db, str(user.id))
        pix_key_to_use = account_check.pix_key

    if not pix_key_to_use or len(pix_key_to_use.strip()) < 5:
        raise HTTPException(
            status_code=400,
            detail="Chave PIX inválida. Cadastre seus dados bancários antes de solicitar saque."
        )
    
    # Verificar saldo
    balances = calculate_clinic_balance(db, str(user.id))
    
    if data.amount > balances["available_balance"]:
        raise HTTPException(status_code=400, detail="Saldo insuficiente")
    
    # Criar solicitação de saque
    withdrawal = WithdrawalRequest(
        id=str(uuid.uuid4()),
        clinic_id=user.id,
        amount=data.amount,
        pix_key=pix_key_to_use.strip(),
        status="pending",  # pending, processing, completed, failed
        created_at=datetime.utcnow()
    )
    db.add(withdrawal)
    
    # Atualizar saldo disponível (reservar valor)
    account = get_or_create_financial_account(db, str(user.id))
    account.available_balance -= data.amount
    
    # TODO: Integrar com API de pagamentos (MercadoPago, etc)
    # Por enquanto, simular processamento automático em background
    
    db.commit()
    
    return {
        "message": "Saque solicitado com sucesso",
        "withdrawal_id": withdrawal.id,
        "amount": data.amount,
        "status": "pending",
        "estimated_processing": "24 horas úteis"
    }

@router.get("/clinic/withdrawals")
def get_withdrawal_history(
    status: Optional[str] = None,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """Histórico de saques da clínica - PERMITIDO offline"""
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Acesso negado")
    
    query = db.query(WithdrawalRequest).filter(
        WithdrawalRequest.clinic_id == user.id
    )
    
    if status:
        query = query.filter(WithdrawalRequest.status == status)
    
    withdrawals = query.order_by(
        WithdrawalRequest.created_at.desc()
    ).limit(limit).all()
    
    return [
        {
            "id": w.id,
            "amount": w.amount,
            "pix_key": w.pix_key,
            "status": w.status,
            "created_at": w.created_at,
            "processed_at": w.processed_at,
            "failure_reason": w.failure_reason
        }
        for w in withdrawals
    ]

@router.get("/clinic/earnings-history")
def get_earnings_history(
    period: str = "week",  # week, month, year
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Retorna histórico de ganhos da clínica para o gráfico.
    PERMITIDO offline (consulta de dados).
    """
    user = current_user["user"]
    if current_user["payload"]["type"] != "clinica":
        raise HTTPException(status_code=403, detail="Acesso negado")
    
    now = datetime.utcnow()
    
    if period == "week":
        # Últimos 7 dias
        start_date = now - timedelta(days=6)
        date_format = "%d/%m"  # DD/MM
        
        # Gerar todas as datas do período
        dates = []
        current = start_date
        while current <= now:
            dates.append(current.date())
            current += timedelta(days=1)
        
        # Buscar ganhos por dia
        earnings = db.query(
            func.date(Appointment.completed_at).label('date'),
            func.sum(Appointment.clinic_amount).label('total')
        ).filter(
            Appointment.clinic_id == user.id,
            Appointment.status == "completed",
            Appointment.completed_at >= start_date
        ).group_by(
            func.date(Appointment.completed_at)
        ).all()
        
        # Criar dicionário de ganhos
        earnings_dict = {e.date: float(e.total) for e in earnings}
        
        # Montar arrays para o gráfico
        labels = [d.strftime("%a") for d in dates]  # Seg, Ter, Qua...
        # Traduzir dias da semana
        day_names = {
            "Mon": "Seg", "Tue": "Ter", "Wed": "Qua", 
            "Thu": "Qui", "Fri": "Sex", "Sat": "Sáb", "Sun": "Dom"
        }
        labels = [day_names.get(l, l) for l in labels]
        data = [earnings_dict.get(d, 0.0) for d in dates]
        
    elif period == "month":
        # Últimos 30 dias agrupados por semana ou dia
        start_date = now - timedelta(days=29)
        
        # Agrupar por dia
        dates = []
        current = start_date
        while current <= now:
            dates.append(current.date())
            current += timedelta(days=1)
        
        earnings = db.query(
            func.date(Appointment.completed_at).label('date'),
            func.sum(Appointment.clinic_amount).label('total')
        ).filter(
            Appointment.clinic_id == user.id,
            Appointment.status == "completed",
            Appointment.completed_at >= start_date
        ).group_by(
            func.date(Appointment.completed_at)
        ).all()
        
        earnings_dict = {e.date: float(e.total) for e in earnings}
        
        # Mostrar a cada 5 dias para não ficar poluído
        labels = []
        data = []
        for i, d in enumerate(dates):
            if i % 5 == 0 or i == len(dates) - 1:
                labels.append(d.strftime("%d/%m"))
                data.append(earnings_dict.get(d, 0.0))
            else:
                # Preencher com 0 para manter o gráfico contínuo
                if len(data) > 0:
                    data[-1] += earnings_dict.get(d, 0.0)
        
        # Se ficou muito grande, simplificar
        if len(labels) > 10:
            labels = labels[::2]
            data = data[::2]
            
    else:  # year
        # Últimos 12 meses
        start_date = now - timedelta(days=365)
        
        earnings = db.query(
            extract('month', Appointment.completed_at).label('month'),
            extract('year', Appointment.completed_at).label('year'),
            func.sum(Appointment.clinic_amount).label('total')
        ).filter(
            Appointment.clinic_id == user.id,
            Appointment.status == "completed",
            Appointment.completed_at >= start_date
        ).group_by(
            extract('month', Appointment.completed_at),
            extract('year', Appointment.completed_at)
        ).order_by(
            extract('year', Appointment.completed_at),
            extract('month', Appointment.completed_at)
        ).all()
        
        # Criar dicionário de meses
        month_names = {
            1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
            5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
            9: "Set", 10: "Out", 11: "Nov", 12: "Dez"
        }
        
        # Preencher meses sem dados com 0
        months_data = defaultdict(float)
        for e in earnings:
            key = f"{int(e.month)}/{int(e.year)}"
            months_data[key] = float(e.total)
        
        # Gerar últimos 12 meses
        labels = []
        data = []
        for i in range(11, -1, -1):
            d = now - timedelta(days=i*30)
            key = f"{d.month}/{d.year}"
            labels.append(month_names.get(d.month, str(d.month)))
            data.append(months_data.get(key, 0.0))
    
    return {
        "period": period,
        "labels": labels,
        "data": data,
        "total": sum(data),
        "average": sum(data) / len(data) if data else 0
    }

# ========== ADMIN ENDPOINTS ==========

@router.post("/admin/release-payments")
def auto_release_payments(
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    [ADMIN] Libera pagamentos pendentes que já passaram 7 dias.
    Verifica se é admin no payload.
    """
    user = current_user["user"]
    user_type = current_user["payload"]["type"]
    
    # Verificação simples de admin pelo tipo ou campo específico
    # Você pode ajustar isso conforme sua lógica de admin
    if user_type != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    
    # Buscar appointments completados há mais de 7 dias, não pagos
    pending_releases = db.query(Appointment).filter(
        Appointment.status == "completed",
        Appointment.is_paid_to_clinic == False,
        Appointment.completed_at <= seven_days_ago
    ).all()
    
    released_count = 0
    total_amount = 0.0
    
    for app in pending_releases:
        app.is_paid_to_clinic = True
        
        # Atualizar saldo da clínica
        account = get_or_create_financial_account(db, str(app.clinic_id))
        account.available_balance += app.clinic_amount
        account.pending_balance -= app.clinic_amount
        
        released_count += 1
        total_amount += app.clinic_amount
    
    db.commit()
    
    return {
        "message": f"{released_count} pagamentos liberados",
        "total_amount": total_amount,
        "released_ids": [app.id for app in pending_releases]
    }

@router.get("/admin/dashboard")
def get_admin_financial_dashboard(
    period: str = "month",  # day, week, month, year
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """[ADMIN] Dashboard financeiro completo da plataforma"""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]
    
    # Verificação simples de admin
    if user_type != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    
    # Período
    now = datetime.utcnow()
    if period == "day":
        start_date = now - timedelta(days=1)
    elif period == "week":
        start_date = now - timedelta(weeks=1)
    elif period == "year":
        start_date = now - timedelta(days=365)
    else:  # month
        start_date = now - timedelta(days=30)
    
    # Métricas
    total_appointments = db.query(Appointment).filter(
        Appointment.created_at >= start_date
    ).count()
    
    completed = db.query(Appointment).filter(
        Appointment.status == "completed",
        Appointment.created_at >= start_date
    )
    
    # Financeiro
    total_revenue = db.query(func.sum(Appointment.total_amount)).filter(
        Appointment.created_at >= start_date,
        Appointment.status == "completed"
    ).scalar() or 0
    
    platform_revenue = db.query(func.sum(Appointment.platform_fee)).filter(
        Appointment.created_at >= start_date,
        Appointment.status == "completed"
    ).scalar() or 0
    
    clinic_payouts = db.query(func.sum(Appointment.clinic_amount)).filter(
        Appointment.created_at >= start_date,
        Appointment.status == "completed"
    ).scalar() or 0
    
    # Por tipo de serviço
    first_consultations = db.query(Appointment).filter(
        Appointment.service_type == "first_consultation",
        Appointment.created_at >= start_date
    ).count()
    
    procedures = db.query(Appointment).filter(
        Appointment.service_type == "procedure",
        Appointment.created_at >= start_date
    ).count()
    
    # Investimento em primeira consulta
    first_consult_cost = db.query(func.sum(Appointment.clinic_amount)).filter(
        Appointment.service_type == "first_consultation",
        Appointment.status == "completed",
        Appointment.created_at >= start_date
    ).scalar() or 0
    
    return {
        "period": period,
        "summary": {
            "total_appointments": total_appointments,
            "completed_appointments": completed.count(),
            "total_revenue": float(total_revenue),
            "platform_revenue": float(platform_revenue),
            "clinic_payouts": float(clinic_payouts),
            "net_profit": float(platform_revenue - first_consult_cost)
        },
        "by_service_type": {
            "first_consultations": {
                "count": first_consultations,
                "investment_cost": float(first_consult_cost)
            },
            "procedures": {
                "count": procedures,
                "commission_revenue": float(platform_revenue)
            }
        },
        "platform_health": {
            "active_clinics": db.query(Clinic).filter(Clinic.is_active == True).count(),
            "online_clinics": db.query(Clinic).filter(Clinic.is_online == True, Clinic.is_active == True).count(),
            "offline_clinics": db.query(Clinic).filter(Clinic.is_online == False, Clinic.is_active == True).count(),
            "pending_releases": db.query(Appointment).filter(
                Appointment.is_paid_to_clinic == False,
                Appointment.status == "completed"
            ).count()
        }
    }