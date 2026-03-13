from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class EmergencyRequestResponse(BaseModel):
    id: str
    patient_name: str
    patient_phone: str
    procedure_type: str
    description: Optional[str]
    distance: float
    latitude: float
    longitude: float
    created_at: Optional[str]
    expires_at: Optional[str]

    class Config:
        from_attributes = True