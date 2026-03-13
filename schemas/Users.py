from pydantic import BaseModel, Field, ConfigDict, EmailStr

class User(BaseModel):
    email: EmailStr
    password: str

    model_config = ConfigDict(from_attributes=True)

class UserCreate(User):
    name: str
    cpf: str
    role: str
    phone: str

class UserAcess(User):
    pass