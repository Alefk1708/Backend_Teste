from fastapi import FastAPI
from database import Base, engine
from fastapi.middleware.cors import CORSMiddleware
from models import models
import asyncio

import routers.auth
import routers.account
import routers.clinics
import routers.appointments
import routers.payments
import routers.websocket
import routers.emergency
import routers.financial
import routers.reviews
import routers.admin
import routers.suggestions
import routers.notifications
import routers.support
import routers.payment_expiry
import routers.slots

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Dentista Fácil API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routers.auth.router)
app.include_router(routers.account.router)
app.include_router(routers.clinics.router)
app.include_router(routers.appointments.router)
app.include_router(routers.payments.router)
app.include_router(routers.websocket.router)
app.include_router(routers.emergency.router)
app.include_router(routers.financial.router)
app.include_router(routers.reviews.router)
app.include_router(routers.admin.router)
app.include_router(routers.suggestions.router)
app.include_router(routers.notifications.router)
app.include_router(routers.support.router)
app.include_router(routers.payment_expiry.router)
app.include_router(routers.slots.router)

@app.on_event("startup")
async def startup_event():
    """Inicia o loop de cancelamento automatico de pagamentos vencidos."""
    asyncio.create_task(routers.payment_expiry.start_expiry_loop())

@app.get("/")
async def root():
    return {
        "app": "Dentista Facil API",
        "version": "1.0.0",
        "status": "online",
        "endpoints": {
            "auth": "/auth",
            "account": "/account",
            "clinics": "/clinics",
            "appointments": "/appointments",
            "payments": "/payments",
            "suggestions": "/suggestions",
            "slots": "/slots",
        }
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}
