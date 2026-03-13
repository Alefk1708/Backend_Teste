from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from jose import jwt, JWTError
from sqlalchemy.orm import Session
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext
from database import get_db
from models.models import User, Clinic
import os

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "420")
)

if not SECRET_KEY or not ALGORITHM:
    raise RuntimeError("SECRET_KEY ou ALGORITHM não definidos")

def hash_password(password: str):
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str):
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()

    expire = datetime.now(timezone.utc) + (
        expires_delta
        if expires_delta
        else timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str | None = payload.get("sub")
        entity_type: str | None = payload.get("type")

        if not user_id or not entity_type:
            raise credentials_exception

    except JWTError:
        raise credentials_exception

    # Buscar usuário baseado no tipo
    if entity_type == "paciente":
        account = db.query(User).filter(User.id == user_id).first()
    elif entity_type == "clinica":
        account = db.query(Clinic).filter(Clinic.id == user_id).first()
    elif entity_type == "admin":
        account = db.query(User).filter(User.id == user_id, User.role == "admin").first()
        if not account:
            raise credentials_exception
    else:
        raise credentials_exception

    if not account:
        raise credentials_exception

    # ========== VERIFICAÇÃO CRÍTICA: Usuário está ativo ==========
    if hasattr(account, 'is_active') and not account.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Conta suspensa. Entre em contato com o suporte.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "user": account,
        "payload": payload,
        "type": entity_type
    }

def require_admin(current_user = Depends(get_current_user)):
    """Dependência para endpoints que exigem admin"""
    if current_user["payload"].get("type") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso restrito a administradores"
        )
    return current_user

def require_clinic_or_admin(current_user = Depends(get_current_user)):
    """Permite acesso a clínicas e admins"""
    user_type = current_user["payload"].get("type")
    if user_type not in ["clinica", "admin"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso negado"
        )
    return current_user