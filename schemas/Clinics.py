from pydantic import BaseModel, ConfigDict, EmailStr
from typing import Optional

class Clinic(BaseModel):
    email: EmailStr
    password: str

    model_config = ConfigDict(from_attributes=True)

class ClinicCreate(Clinic):
    name: str
    cnpj: str
    role: str
    phone: str

    address: Optional[str] = None

    street: Optional[str] = None
    number: Optional[str] = None
    neighborhood: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None

    latitude: Optional[float] = None
    longitude: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


