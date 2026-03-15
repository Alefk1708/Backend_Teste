from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from database import get_db
from core.security import hash_password, verify_password, create_access_token

from schemas.Users import UserCreate, UserAcess
from schemas.Auth import VerifyCode, ResetCode, ResetPasswordCode
from schemas.Clinics import ClinicCreate

from models.models import (
    User,
    Clinic,
    TwoFactorAuth,
    ResetPasswordWithCode,
    ActionAttempts,
    UniqueEmail
)

from utils.cpf import is_valid_cpf
from utils.cnpj import is_valid_cnpj, verify_cnpj
from utils.verifyCodeEmail import code_generator, seed_email_code

router = APIRouter(prefix="/auth", tags=["auth"])

# =========================
# LIMITES
# =========================
LIMITS = {
    "login": 5,
    "resend_code": 5,
    "reset_password": 3
}

# =========================
# RATE LIMIT CORE
# =========================
def get_or_create_attempts(db, entity_id, entity_type):
    record = db.query(ActionAttempts).filter(
        ActionAttempts.entity_id == entity_id
    ).first()

    if not record:
        record = ActionAttempts(
            entity_id=entity_id,
            entity_type=entity_type
        )
        db.add(record)
        db.commit()
        db.refresh(record)

    return record


def check_and_increment_attempt(record, action: str):
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    attempts_field = f"{action}_attempts"
    last_field = f"{action}_last"

    attempts = getattr(record, attempts_field)
    last_attempt = getattr(record, last_field)

    if not last_attempt or last_attempt < today_start:
        attempts = 0

    if attempts >= LIMITS[action]:
        raise HTTPException(
            status_code=429,
            detail="Limite diário de tentativas atingido."
        )

    setattr(record, attempts_field, attempts + 1)
    setattr(record, last_field, now)

# =========================
# CRIAR USUÁRIO
# =========================
@router.post("/createAccountUser")
def user_create(user: UserCreate, db: Session = Depends(get_db)):
    cpf_clean = ''.join(filter(str.isdigit, user.cpf))

    if not is_valid_cpf(cpf_clean):
        raise HTTPException(status_code=400, detail="CPF inválido")

    if db.query(UniqueEmail).filter_by(email=user.email).first():
        raise HTTPException(status_code=400, detail="Email já cadastrado")

    if db.query(User).filter(User.cpf == cpf_clean).first():
        raise HTTPException(status_code=400, detail="CPF já cadastrado")

    db_user = User(
        name=user.name,
        email=user.email,
        password_hash=hash_password(user.password),
        cpf=cpf_clean,
        role=user.role,
        phone=user.phone
    )

    db.add(db_user)
    db.commit()

    db.add(UniqueEmail(
        email=user.email,
        entity_type="paciente",
        entity_id=str(db_user.id)
    ))
    db.commit()

    return {"message": "Usuário criado com sucesso"}

# =========================
# CRIAR CLÍNICA
# =========================
@router.post("/createAccountClinic")
async def clinic_create(clinic: ClinicCreate, db: Session = Depends(get_db)):
    cnpj_clean = ''.join(filter(str.isdigit, clinic.cnpj))

    if not is_valid_cnpj(cnpj_clean):
        raise HTTPException(status_code=400, detail="CNPJ inválido")

    if db.query(UniqueEmail).filter_by(email = clinic.email).first():
        raise HTTPException(status_code=400, detail="Email já cadastrado")

    if db.query(Clinic).filter(Clinic.cnpj == cnpj_clean).first():
        raise HTTPException(status_code=400, detail="CNPJ já cadastrado")

    if not await verify_cnpj(cnpj_clean):
        raise HTTPException(status_code=400, detail="CNPJ não encontrado na Receita")
    
    address = clinic.address

    if clinic.street and clinic.number and clinic.neighborhood:
        address = f"{clinic.street}, {clinic.number} - {clinic.neighborhood}, {clinic.city} - {clinic.state}"

    db_clinic = Clinic(
        name=clinic.name,
        email=clinic.email,
        password_hash=hash_password(clinic.password),
        role=clinic.role,
        cnpj=cnpj_clean,
        address=address,
        phone=clinic.phone,
        street=clinic.street,
        number=clinic.number,
        neighborhood=clinic.neighborhood,
        city=clinic.city,
        state=clinic.state,
        zip_code=clinic.zip_code,
        latitude=clinic.latitude,
        longitude=clinic.longitude,
    )

    db.add(db_clinic)
    db.commit()

    db.add(UniqueEmail(
        email=clinic.email,
        entity_type="clinica",
        entity_id=str(db_clinic.id)
    ))
    db.commit()

    return {"message": "Clínica criada com sucesso"}

# =========================
# LOGIN + 2FA
# =========================
@router.post("/acessAccount")
def acess_account(user: UserAcess, background_tasks: BackgroundTasks ,db: Session = Depends(get_db)):
    account = db.query(User).filter(User.email == user.email).first()
    entity_type = "paciente"

    if not account:
        account = db.query(Clinic).filter(Clinic.email == user.email).first()
        entity_type = "clinica"

    if not account:
        raise HTTPException(status_code=400, detail="Email ou senha inválidos")

    attempts = get_or_create_attempts(db, str(account.id), entity_type)
    check_and_increment_attempt(attempts, "login")
    db.commit()

    if not verify_password(user.password, account.password_hash):
        raise HTTPException(status_code=400, detail="Email ou senha inválidos")

    db.query(TwoFactorAuth).filter(
        TwoFactorAuth.entity_id == str(account.id)
    ).delete()
    db.commit()

    code, expire = code_generator(user.email)

    db.add(TwoFactorAuth(
        entity_id=str(account.id),
        entity_type=entity_type,
        code=code,
        expires_at=expire
    ))
    db.commit()

    background_tasks.add_task(
        seed_email_code,
        user.email,
        code,
        10
    )
   

    temp_token = create_access_token(
        data={"sub": str(account.id), "type": entity_type},
        expires_delta=timedelta(minutes=15)
    )

    return {
        "message": "Código de verificação enviado",
        "temp_token": temp_token
    }

# =========================
# REENVIAR CÓDIGO 2FA
# =========================
@router.post("/reseedVerifyCode")
def reseed_verify_code(data: ResetCode, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    account = db.query(User).filter(User.email == data.email).first()
    entity_type = "paciente"

    if not account:
        account = db.query(Clinic).filter(Clinic.email == data.email).first()
        entity_type = "clinica"

    if not account:
        return {"message": "Se o email estiver cadastrado, um novo código será enviado"}

    attempts = get_or_create_attempts(db, str(account.id), entity_type)
    check_and_increment_attempt(attempts, "resend_code")
    db.commit()

    db.query(TwoFactorAuth).filter(
        TwoFactorAuth.entity_id == str(account.id),
        TwoFactorAuth.entity_type == entity_type
    ).delete()
    db.commit()

    code, expire = code_generator(data.email)

    db.add(TwoFactorAuth(
        entity_id=str(account.id),
        entity_type=entity_type,
        code=code,
        expires_at=expire
    ))
    db.commit()

    background_tasks.add_task(
        seed_email_code,
        data.email,
        code,
        10
    )

    return {"message": "Se o email estiver cadastrado, um novo código será enviado"}

# =========================
# VERIFICAR CÓDIGO
# =========================
@router.post("/verifyCode")
def verify_code(data: VerifyCode, db: Session = Depends(get_db)):
    account = db.query(User).filter(User.email == data.email).first()
    entity_type = "paciente"

    if not account:
        account = db.query(Clinic).filter(Clinic.email == data.email).first()
        entity_type = "clinica"

    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada")

    two_factor = db.query(TwoFactorAuth).filter(
        TwoFactorAuth.entity_id == str(account.id),
        TwoFactorAuth.entity_type == entity_type
    ).order_by(TwoFactorAuth.expires_at.desc()).first()

    if not two_factor:
        raise HTTPException(status_code=400, detail="Código não encontrado")

    if two_factor.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Código expirado")

    if two_factor.code != data.code:
        raise HTTPException(status_code=400, detail="Código inválido")
    
    token_expiration = (
    timedelta(days=30)
    if data.remember_me
    else timedelta(hours=8)
    )

    access_token = create_access_token(
        data={
            "sub": str(account.id),
            "email": account.email,
            "name": account.name,
            "phone": account.phone,
            "type": account.role,
            "remember_me": data.remember_me,
            "avatar_url": account.avatar_url,
            "avatar_public_id": account.avatar_public_id
        },
        expires_delta=token_expiration
    )

    db.delete(two_factor)
    db.commit()

    return {
        "message": "Login concluído com sucesso",
        "access_token": access_token
    }

# =========================
# RESET - ENVIAR CÓDIGO
# =========================
@router.post("/seedResetCode")
def seed_reset_code(data: ResetCode, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    account = db.query(User).filter(User.email == data.email).first()
    entity_type = "paciente"

    if not account:
        account = db.query(Clinic).filter(Clinic.email == data.email).first()
        entity_type = "clinica"

    if not account:
        return {"message": "Código enviado com sucesso"}

    attempts = get_or_create_attempts(db, str(account.id), entity_type)
    check_and_increment_attempt(attempts, "reset_password")
    db.commit()

    db.query(ResetPasswordWithCode).filter(
        ResetPasswordWithCode.entity_id == str(account.id)
    ).delete()
    db.commit()

    code, expire = code_generator(data.email)

    db.add(ResetPasswordWithCode(
        entity_id=str(account.id),
        entity_type=entity_type,
        code=code,
        expires_at=expire
    ))
    db.commit()

    background_tasks.add_task(
        seed_email_code,
        data.email,
        code,
        10
    )

    return {"message": "Código enviado com sucesso"}

# =========================
# RESET - CONFIRMAR SENHA
# =========================
@router.post("/resetPassword")
def reset_password(data: ResetPasswordCode, db: Session = Depends(get_db)):
    account = db.query(User).filter(User.email == data.email).first()
    entity_type = "paciente"

    if not account:
        account = db.query(Clinic).filter(Clinic.email == data.email).first()
        entity_type = "clinica"

    if not account:
        raise HTTPException(status_code=404, detail="Conta não encontrada")

    reset_code = db.query(ResetPasswordWithCode).filter(
        ResetPasswordWithCode.entity_id == str(account.id),
        ResetPasswordWithCode.entity_type == entity_type
    ).order_by(ResetPasswordWithCode.expires_at.desc()).first()

    if not reset_code:
        raise HTTPException(status_code=400, detail="Código não encontrado")

    if reset_code.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Código expirado")

    if reset_code.code != data.code:
        raise HTTPException(status_code=400, detail="Código inválido")

    account.password_hash = hash_password(data.password)

    db.delete(reset_code)
    db.commit()

    return {"message": "Senha redefinida com sucesso"}
