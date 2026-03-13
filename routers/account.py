from fastapi import APIRouter, Depends, HTTPException, Form, File, UploadFile, BackgroundTasks
from sqlalchemy.orm import Session
from core.security import get_current_user, create_access_token, hash_password
from utils.verifyCodeEmail import code_generator, seed_email_code
from schemas.Account import EditCredentials, DeleteAccountRequest, DeleteAccountConfirm
from database import get_db
from services.image_uploader import upload_avatar
from datetime import datetime, timezone, timedelta
from cloudinary.uploader import destroy

from models.models import (
    User, Clinic, TwoFactorAuth, UniqueEmail, ActionAttempts,
    ResetPasswordWithCode, Appointment, Payment, Notification, 
    ClinicReview, EmergencyRequest, EmergencyDecline, ClinicProcedure,
    ClinicFinancialAccount, ClinicEmergencyPrice, WithdrawalRequest,
    PlatformTransaction
)

router = APIRouter(prefix="/account", tags=["account"])

@router.put("/EditAccount")
def edit_account(
    name: str = Form(None),
    phone: str = Form(None),
    avatar: UploadFile = File(None),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
    ):
    auth_user = current_user["user"]
    payload = current_user["payload"]

    account = db.query(User).filter(User.id == auth_user.id).first()
    entity_type = "paciente"
    

    if not account:
        account = db.query(Clinic).filter(Clinic.id == auth_user.id).first()
        entity_type = "clinica"
    
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    
    if name:
        account.name = name
    if phone:
        account.phone = phone
    
    if avatar:
        if avatar.content_type not in ["image/jpeg", "image/png", "image/web"]:
            raise HTTPException(status_code=400, detail="Formato de imagem inválido")
        
        if account.avatar_public_id:
            destroy(account.avatar_public_id)
        

        folder = f"avatars/{entity_type}"
        upload_result = upload_avatar(avatar.file, folder)
        account.avatar_url = upload_result["url"]
        account.avatar_public_id = upload_result["public_id"]
    
    exp_timestamp = payload.get("exp")
    now = int(datetime.now(tz=timezone.utc).timestamp())
    remaining_seconds = exp_timestamp - now

    if remaining_seconds <= 0:
        raise HTTPException(status_code=401, detail="Token expirado")

    

    db.commit()
    db.refresh(account)
    
    access_token = create_access_token(
        data={
            "sub": str(account.id), 
            "email": account.email,  
            "name": account.name,    
            "phone": account.phone,  
            "type": account.role, 
            "remember_me": payload.get("remember_me"), 
            "avatar_url": account.avatar_url,
            "avatar_public_id" : account.avatar_public_id
        }, 
        expires_delta=timedelta(seconds=remaining_seconds)
    )

    return {"message": "Conta editada com sucesso", "access_token": access_token}

@router.post("/RequestUpdateCode")
def request_update_code(background_tasks: BackgroundTasks, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    auth_user = current_user["user"]
    entity_type = current_user["payload"]["type"]

    db.query(TwoFactorAuth).filter(
        TwoFactorAuth.entity_id == str(auth_user.id)
    ).delete()
    db.commit()

    code, expire = code_generator()

    db.add(TwoFactorAuth(
        entity_id=str(auth_user.id),
        entity_type=entity_type,
        code=code,
        expires_at=expire
    ))
    db.commit()

    background_tasks.add_task(
        seed_email_code,
        auth_user.email,
        code,
        10
    )

@router.put("/EditCredentials")
def edit_credentials(payload: EditCredentials, db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    auth_user = current_user["user"]
    entity_type = current_user["payload"]["type"]
    remember_me = current_user["payload"]["remember_me"]
    exp = current_user["payload"]["exp"]

    account = db.query(User).filter(User.id == auth_user.id).first()

    if not account:
        account = db.query(Clinic).filter(Clinic.id == auth_user.id).first()
    
    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada")
    
    
    if not payload.code:
        raise HTTPException(status_code=400, detail="Código não informado")
    
    two_factor = db.query(TwoFactorAuth).filter(
        TwoFactorAuth.entity_id == str(account.id),
        TwoFactorAuth.entity_type == entity_type
    ).order_by(TwoFactorAuth.expires_at.desc()).first()
    
    if not two_factor:
        raise HTTPException(status_code=400, detail="Código não encontrado")
    
    
    if two_factor.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Código expirado")

    if two_factor.code != payload.code:
        raise HTTPException(status_code=400, detail="Código inválido")
    
    if payload.new_email:
        account.email = payload.new_email
    
    if payload.new_password:
        account.password_hash = hash_password(payload.new_password)

    exp_timestamp = exp
    now = int(datetime.now(tz=timezone.utc).timestamp())
    remaining_seconds = exp_timestamp - now

    if remaining_seconds <= 0:
        raise HTTPException(status_code=401, detail="Token expirado")
    
    db.delete(two_factor)
    db.commit()

    access_token = create_access_token(
        data={
            "sub": str(account.id), 
            "email": account.email,  
            "name": account.name,    
            "phone": account.phone,  
            "type": account.role, 
            "remember_me": remember_me, 
            "avatar_url": account.avatar_url,
            "avatar_public_id" : account.avatar_public_id
        }, 
        expires_delta=timedelta(seconds=remaining_seconds)
    )

    return {"message": "Credenciais atualizadas com sucesso", "access_token": access_token}

@router.get("/MyAccount")
def my_account(db: Session = Depends(get_db), current_user = Depends(get_current_user)):
    auth_user = current_user["user"]
    entity_type = current_user["payload"]["type"]
    
    account = db.query(User).filter(User.id == auth_user.id).first()

    if not account:
        account = db.query(Clinic).filter(Clinic.id == auth_user.id).first()

    return { "name": account.name, "email": account.email, "phone": account.phone, "avatar_url": account.avatar_url, "id": account.id, "type": account.role, "create": account.created_at, "document": account.cnpj if account.role == "clinica" else account.cpf, "address": account.address if account.role == "clinica" else ""  }


# ========== EXCLUSÃO DE CONTA COM VERIFICAÇÃO ==========

@router.post("/RequestDeleteCode")
def request_delete_code(
    background_tasks: BackgroundTasks, 
    db: Session = Depends(get_db), 
    current_user = Depends(get_current_user)
):
    """
    Solicita código de verificação para exclusão de conta.
    Envia código de 6 dígitos por email.
    """
    auth_user = current_user["user"]
    entity_type = current_user["payload"]["type"]
    
    # Limpar códigos antigos
    db.query(TwoFactorAuth).filter(
        TwoFactorAuth.entity_id == str(auth_user.id),
        TwoFactorAuth.entity_type == f"{entity_type}_delete"
    ).delete()
    db.commit()
    
    # Gerar novo código
    code, expire = code_generator()
    
    # Salvar com tipo específico para delete
    db.add(TwoFactorAuth(
        entity_id=str(auth_user.id),
        entity_type=f"{entity_type}_delete",  # paciente_delete ou clinica_delete
        code=code,
        expires_at=expire
    ))
    db.commit()
    
    # Enviar email com código
    background_tasks.add_task(
        seed_email_code,
        auth_user.email,
        code,
        10  # 10 minutos de validade
    )
    
    return {
        "message": "Código de verificação enviado para seu email",
        "expires_in_minutes": 10
    }


def _delete_patient_account(db: Session, user_id: str, two_factor_code=None):
    """Deleta conta de paciente e todos os dados vinculados"""
    
    patient = db.query(User).filter(User.id == user_id).first()
    if not patient:
        raise HTTPException(status_code=404, detail="Paciente não encontrado")
    
    # 1. Cancelar e reembolsar agendamentos futuros pagos
    future_apps = db.query(Appointment).filter(
        Appointment.patient_id == user_id,
        Appointment.scheduled_at > datetime.utcnow(),
        Appointment.status.in_(["pending", "confirmed", "awaiting_payment", "scheduled"])
    ).all()
    
    for app in future_apps:
        payment = db.query(Payment).filter(
            Payment.appointment_id == app.id,
            Payment.status == "completed"
        ).first()
        if payment:
            payment.status = "refunded"
            payment.refunded_at = datetime.utcnow()
        
        app.status = "cancelled"
        app.cancellation_reason = "Conta excluída pelo usuário"
    
    # 2. Deletar notificações
    db.query(Notification).filter(
        Notification.user_id == user_id,
        Notification.user_type == "paciente"
    ).delete(synchronize_session=False)
    
    # 3. Deletar registros de autenticação (MENOS o código que já vamos deletar)
    if two_factor_code:
        db.delete(two_factor_code)  # ✅ Deleta o código específico primeiro
    
    # Deleta outros códigos 2FA (se houver) - exceto o que já deletamos
    db.query(TwoFactorAuth).filter(
        TwoFactorAuth.entity_id == user_id,
        TwoFactorAuth.entity_type != f"paciente_delete"  # Evita conflito se o código já foi deletado
    ).delete(synchronize_session=False)
    
    db.query(ResetPasswordWithCode).filter(
        ResetPasswordWithCode.entity_id == user_id
    ).delete(synchronize_session=False)
    
    db.query(ActionAttempts).filter(
        ActionAttempts.entity_id == user_id
    ).delete(synchronize_session=False)
    
    # 4. Deletar solicitações de emergência pendentes
    db.query(EmergencyRequest).filter(
        EmergencyRequest.patient_id == user_id,
        EmergencyRequest.status == "pending"
    ).delete(synchronize_session=False)
    
    # 5. Deletar recusas de emergência (paciente não tem, mas por segurança)
    db.query(EmergencyDecline).filter(
        EmergencyDecline.clinic_id == user_id
    ).delete(synchronize_session=False)
    
    # 6. Remover email de registros únicos
    db.query(UniqueEmail).filter(
        UniqueEmail.entity_id == user_id
    ).delete(synchronize_session=False)
    
    # 7. Deletar paciente (cascade cuida de appointments, reviews, etc)
    db.delete(patient)


def _delete_clinic_account(db: Session, clinic_id: str, two_factor_code=None):
    """Deleta conta de clínica e todos os dados vinculados"""
    
    clinic = db.query(Clinic).filter(Clinic.id == clinic_id).first()
    if not clinic:
        raise HTTPException(status_code=404, detail="Clínica não encontrada")
    
    # 1. Cancelar agendamentos futuros e reembolsar
    future_apps = db.query(Appointment).filter(
        Appointment.clinic_id == clinic_id,
        Appointment.scheduled_at > datetime.utcnow(),
        Appointment.status.in_(["pending", "confirmed", "awaiting_payment"])
    ).all()
    
    for app in future_apps:
        payment = db.query(Payment).filter(
            Payment.appointment_id == app.id,
            Payment.status == "completed"
        ).first()
        if payment:
            payment.status = "refunded"
            payment.refunded_at = datetime.utcnow()
        
        app.status = "cancelled"
        app.cancellation_reason = "Clínica encerrou atividades"
    
    # 2. Verificar saques pendentes
    pending_withdrawals = db.query(WithdrawalRequest).filter(
        WithdrawalRequest.clinic_id == clinic_id,
        WithdrawalRequest.status == "pending"
    ).count()
    
    if pending_withdrawals > 0:
        raise HTTPException(
            status_code=400, 
            detail=f"Existem {pending_withdrawals} saques pendentes. Aguarde processamento ou cancele antes de excluir."
        )
    
    # 3. Verificar saldo disponível
    from routers.financial import calculate_clinic_balance
    balances = calculate_clinic_balance(db, clinic_id)
    
    if balances["available_balance"] > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Saldo disponível de R${balances['available_balance']:.2f}. Realize o saque antes de excluir."
        )
    
    # 4. Deletar notificações
    db.query(Notification).filter(
        Notification.user_id == clinic_id,
        Notification.user_type == "clinica"
    ).delete(synchronize_session=False)
    
    # 5. Deletar registros de autenticação
    if two_factor_code:
        db.delete(two_factor_code)  # ✅ Deleta o código específico primeiro
    
    db.query(TwoFactorAuth).filter(
        TwoFactorAuth.entity_id == clinic_id,
        TwoFactorAuth.entity_type != f"clinica_delete"
    ).delete(synchronize_session=False)
    
    db.query(ResetPasswordWithCode).filter(
        ResetPasswordWithCode.entity_id == clinic_id
    ).delete(synchronize_session=False)
    
    db.query(ActionAttempts).filter(
        ActionAttempts.entity_id == clinic_id
    ).delete(synchronize_session=False)
    
    # 6. Deletar recusas de emergência
    db.query(EmergencyDecline).filter(
        EmergencyDecline.clinic_id == clinic_id
    ).delete(synchronize_session=False)
    
    # 7. Deletar registros de emergência atendidas
    db.query(EmergencyRequest).filter(
        EmergencyRequest.clinic_id == clinic_id
    ).delete(synchronize_session=False)
    
    # 8. Deletar avaliações
    db.query(ClinicReview).filter(
        ClinicReview.clinic_id == clinic_id
    ).delete(synchronize_session=False)
    
    # 9. Remover email único
    db.query(UniqueEmail).filter(
        UniqueEmail.entity_id == clinic_id
    ).delete(synchronize_session=False)
    
    # 10. Deletar clínica (cascade cuida do resto)
    db.delete(clinic)

@router.post("/ConfirmDelete")
def confirm_delete_account(
    data: DeleteAccountConfirm,
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Confirma exclusão de conta com código e deleta permanentemente.
    ⚠️ IRREVERSÍVEL - Deleta todos os dados vinculados!
    """
    auth_user = current_user["user"]
    entity_type = current_user["payload"]["type"]
    user_id = str(auth_user.id)
    
    if not data.code:
        raise HTTPException(status_code=400, detail="Código não informado")
    
    # Verificar código
    two_factor = db.query(TwoFactorAuth).filter(
        TwoFactorAuth.entity_id == user_id,
        TwoFactorAuth.entity_type == f"{entity_type}_delete"
    ).order_by(TwoFactorAuth.expires_at.desc()).first()
    
    if not two_factor:
        raise HTTPException(status_code=400, detail="Código não encontrado. Solicite um novo código.")
    
    if two_factor.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Código expirado. Solicite um novo código.")
    
    if two_factor.code != data.code:
        raise HTTPException(status_code=400, detail="Código inválido")
    
    # ========== EXCLUSÃO DEFINITIVA ==========
    
    try:
        if entity_type == "paciente":
            _delete_patient_account(db, user_id, two_factor)  
        elif entity_type == "clinica":
            _delete_clinic_account(db, user_id, two_factor)   
        else:
            raise HTTPException(status_code=400, detail="Tipo de conta inválido")
        
        # Commit único no final
        db.commit()
        
        return {
            "message": "Conta excluída permanentemente",
            "deleted_at": datetime.utcnow().isoformat(),
            "warning": "Esta ação não pode ser desfeita"
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao excluir conta: {str(e)}")