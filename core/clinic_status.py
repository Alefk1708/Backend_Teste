from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from database import get_db
from models.models import Clinic

def require_clinic_online(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Dependência que verifica se a clínica está online.
    Use em TODAS as rotas que clínicas acessam e precisam estar online.
    """
    user = current_user["user"]
    user_type = current_user["payload"]["type"]
    
    # Se não for clínica, ignora (pacientes e admins podem acessar)
    if user_type != "clinica":
        return current_user
    
    # Buscar clínica atualizada no banco
    clinic = db.query(Clinic).filter(Clinic.id == user.id).first()
    
    if not clinic:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Clínica não encontrada"
        )
    
    if not clinic.is_online:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clínica está offline. Altere seu status para online para continuar."
        )
    
    if not clinic.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Clínica está suspensa. Entre em contato com o suporte."
        )
    
    return current_user