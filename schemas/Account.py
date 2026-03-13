from pydantic import BaseModel, ConfigDict, EmailStr

class EditCredentials(BaseModel):
    new_email: EmailStr | None = None
    new_password: str | None = None
    code : str

    model_config = ConfigDict(from_attributes=True)

class DeleteAccountRequest(BaseModel):
    """Schema para solicitar exclusão de conta"""
    pass  # Apenas precisa do token

class DeleteAccountConfirm(BaseModel):
    """Schema para confirmar exclusão com código"""
    code: str
    
    model_config = ConfigDict(from_attributes=True)