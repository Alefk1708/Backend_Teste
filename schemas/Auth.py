from pydantic import BaseModel, EmailStr, ConfigDict

class Acess(BaseModel):
    email: EmailStr
    code: str

    model_config = ConfigDict(from_attributes=True)

class VerifyCode(BaseModel):
    email: EmailStr
    code: str
    remember_me: bool = False

class ResetCode(BaseModel):
    email: EmailStr

class ResetPasswordCode(ResetCode):
    code: str
    password: str

