from sqlalchemy import Column, String, DateTime, Integer, Float, Boolean, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base
import uuid

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, index=True, unique=True)
    password_hash = Column(String, nullable=False)
    cpf = Column(String, nullable=False, index=True, unique=True)
    phone = Column(String, nullable=False)
    role = Column(String, nullable=False, index=True)
    avatar_url = Column(String)
    avatar_public_id = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    appointments = relationship("Appointment", back_populates="patient", cascade="all, delete-orphan")
    reviews = relationship("ClinicReview", back_populates="patient", cascade="all, delete-orphan")

class Clinic(Base):
    __tablename__ = "clinics"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    email = Column(String, nullable=False, index=True, unique=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, index=True)
    phone = Column(String, nullable=False)
    cnpj = Column(String, nullable=False, index=True, unique=True)
    street = Column(String, nullable=False)
    number = Column(String, nullable=False)
    neighborhood = Column(String, nullable=False)
    city = Column(String, nullable=False)
    state = Column(String, nullable=False)
    zip_code = Column(String)
    address = Column(String, nullable=False)
    latitude = Column(Float)
    longitude = Column(Float)
    description = Column(String)
    avatar_url = Column(String)
    avatar_public_id = Column(String)
    is_online = Column(Boolean, default=True)
    is_active = Column(Boolean, default=True)
    emergency_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    appointments = relationship("Appointment", back_populates="clinic", cascade="all, delete-orphan")
    procedures = relationship("ClinicProcedure", back_populates="clinic", cascade="all, delete-orphan")
    emergency_price = relationship("ClinicEmergencyPrice", back_populates="clinic", uselist=False, cascade="all, delete-orphan")
    financial_account = relationship("ClinicFinancialAccount", back_populates="clinic", uselist=False, cascade="all, delete-orphan")
    withdrawal_requests = relationship("WithdrawalRequest", back_populates="clinic", cascade="all, delete-orphan")
    treatment_suggestions = relationship("TreatmentSuggestion", back_populates="clinic", cascade="all, delete-orphan")

class UniqueEmail(Base):
    __tablename__ = "unique_emails"
    email = Column(String, primary_key=True, index=True)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class TwoFactorAuth(Base):
    __tablename__ = "two_factor_auth"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    entity_id = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False)
    code = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)

class ResetPasswordWithCode(Base):
    __tablename__ = "reset_password_with_code"
    id = Column(String, primary_key=True, index=True, default=lambda: str(uuid.uuid4()))
    entity_id = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False)
    code = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)

class ActionAttempts(Base):
    __tablename__ = "action_attempts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_id = Column(String, nullable=False, unique=True, index=True)
    entity_type = Column(String, nullable=False)
    login_attempts = Column(Integer, default=0)
    login_last = Column(DateTime)
    resend_code_attempts = Column(Integer, default=0)
    resend_code_last = Column(DateTime)
    reset_password_attempts = Column(Integer, default=0)
    reset_password_last = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

class Procedure(Base):
    """Procedimento global. Apenas admin cria/edita/exclui."""
    __tablename__ = "procedures"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    description = Column(Text)
    category = Column(String, nullable=False)
    price = Column(Float, nullable=False, default=0.0)
    default_duration_minutes = Column(Integer, default=30)
    is_active = Column(Boolean, default=True)
    max_upper_teeth = Column(Integer, nullable=True)
    max_lower_teeth = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    clinic_procedures = relationship("ClinicProcedure", back_populates="procedure", cascade="all, delete-orphan")

class ClinicProcedure(Base):
    """Clínica ativa/desativa procedimentos globais."""
    __tablename__ = "clinic_procedures"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False)
    procedure_id = Column(String, ForeignKey("procedures.id"), nullable=False)
    is_active = Column(Boolean, default=True)
    price = Column(Float, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    __table_args__ = (
        UniqueConstraint("clinic_id", "procedure_id", name="unique_clinic_procedure"),
    )
    clinic = relationship("Clinic", back_populates="procedures")
    procedure = relationship("Procedure", back_populates="clinic_procedures")

class Appointment(Base):
    __tablename__ = "appointments"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    patient_id = Column(String, ForeignKey("users.id"), nullable=False)
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False)
    procedure_id = Column(String, ForeignKey("procedures.id"))
    service_type = Column(String, nullable=False)
    status = Column(String, default="pending")
    type = Column(String, default="scheduled")
    scheduled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime)
    patient_latitude = Column(Float)
    patient_longitude = Column(Float)
    description = Column(Text)
    cancellation_reason = Column(Text)
    total_amount = Column(Float)
    platform_fee = Column(Float)
    clinic_amount = Column(Float)
    is_paid_to_clinic = Column(Boolean, default=False)
    paid_to_clinic_at = Column(DateTime)
    payment_deadline = Column(DateTime, nullable=True)   # limite de 1h para pagar
    lens_upper_count = Column(Integer, nullable=True)
    lens_lower_count = Column(Integer, nullable=True)
    lens_total_price = Column(Float, nullable=True)
    patient = relationship("User", back_populates="appointments")
    clinic = relationship("Clinic", back_populates="appointments")
    payments = relationship("Payment", back_populates="appointment", cascade="all, delete-orphan")
    reviews = relationship("ClinicReview", back_populates="appointment", cascade="all, delete-orphan")
    procedure = relationship("Procedure")
    treatment_suggestions = relationship(
        "TreatmentSuggestion", 
        back_populates="origin_appointment", 
        foreign_keys="[TreatmentSuggestion.origin_appointment_id]", 
        cascade="all, delete-orphan"
    )

class PlatformEmergencyPrice(Base):
    __tablename__ = "platform_emergency_price"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    price = Column(Float, nullable=False, default=99.99)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = Column(String, nullable=True)

class ClinicFinancialAccount(Base):
    __tablename__ = "clinic_financial_accounts"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    clinic_id = Column(String, ForeignKey("clinics.id"), unique=True)
    bank_code = Column(String)
    agency = Column(String)
    account = Column(String)
    account_type = Column(String)
    pix_key = Column(String)
    available_balance = Column(Float, default=0.0)
    pending_balance = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    clinic = relationship("Clinic", back_populates="financial_account")

class PlatformTransaction(Base):
    __tablename__ = "platform_transactions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    appointment_id = Column(String, ForeignKey("appointments.id"))
    transaction_type = Column(String)
    amount = Column(Float)
    platform_profit = Column(Float)
    clinic_amount = Column(Float)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class EmergencyRequest(Base):
    __tablename__ = "emergency_requests"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    patient_id = Column(String, ForeignKey("users.id"), nullable=False)
    clinic_id = Column(String, ForeignKey("clinics.id"))
    status = Column(String, default="pending")
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    procedure_type = Column(String, default="urgencia")
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    claimed_at = Column(DateTime)
    expires_at = Column(DateTime)
    declines = relationship("EmergencyDecline", back_populates="emergency_request", cascade="all, delete-orphan")

class Payment(Base):
    __tablename__ = "payments"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    appointment_id = Column(String, ForeignKey("appointments.id"), nullable=False)
    amount = Column(Float, nullable=False)
    platform_fee = Column(Float, nullable=False)
    clinic_amount = Column(Float, nullable=False)
    payment_method = Column(String, nullable=False)
    status = Column(String, default="pending")
    external_id = Column(String)
    pix_qr_code = Column(Text)
    pix_code = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    paid_at = Column(DateTime)
    refunded_at = Column(DateTime)
    appointment = relationship("Appointment", back_populates="payments")

    # Garante no banco que só existe 1 payment ativo por agendamento.
    # A constraint de aplicação (SELECT FOR UPDATE) é a primeira linha de defesa;
    # esta UniqueConstraint é a rede de segurança final no nível do banco.
    # Payments com status "failed" são excluídos via lógica de aplicação antes
    # de criar um novo (ver router de pagamentos).
    __table_args__ = (
        UniqueConstraint(
            "appointment_id",
            name="uq_payment_appointment_active",
        ),
    )


class PaymentIdempotency(Base):
    """
    Tabela de idempotência para pagamentos com cartão.

    O frontend gera um idempotency_key (UUID v4) antes de chamar POST /payments/card.
    Se a mesma key chegar duas vezes (duplo clique, retry automático), o segundo
    pedido recebe o mesmo resultado do primeiro sem chamar o Mercado Pago novamente.

    TTL: 30 minutos.
    """
    __tablename__ = "payment_idempotency"
    key        = Column(String, primary_key=True)           # UUID enviado pelo frontend
    payment_id = Column(String, nullable=True)              # preenchido após criação
    status     = Column(String, nullable=False)             # "processing" | "done" | "failed"
    response   = Column(Text)                               # JSON do response serializado
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)           # created_at + 30 min


class ClinicReview(Base):
    __tablename__ = "clinic_reviews"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False)
    patient_id = Column(String, ForeignKey("users.id"), nullable=False)
    appointment_id = Column(String, ForeignKey("appointments.id"), nullable=False)
    rating = Column(Integer, nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    patient = relationship("User", back_populates="reviews")
    appointment = relationship("Appointment", back_populates="reviews")

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False)
    user_type = Column(String, nullable=False)
    title = Column(String, nullable=False)
    message = Column(Text, nullable=False)
    type = Column(String)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    data = Column(Text)

class ClinicEmergencyPrice(Base):
    """Mantido por compatibilidade."""
    __tablename__ = "clinic_emergency_prices"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False, unique=True)
    price = Column(Float, nullable=False, default=99.99)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    clinic = relationship("Clinic", back_populates="emergency_price")

class EmergencyDecline(Base):
    __tablename__ = "emergency_declines"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    emergency_request_id = Column(String, ForeignKey("emergency_requests.id"), nullable=False)
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False)
    declined_at = Column(DateTime, default=datetime.utcnow)
    emergency_request = relationship("EmergencyRequest", back_populates="declines")
    __table_args__ = (
        UniqueConstraint("emergency_request_id", "clinic_id", name="unique_decline_per_clinic"),
    )

class WithdrawalRequest(Base):
    __tablename__ = "withdrawal_requests"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False)
    amount = Column(Float, nullable=False)
    pix_key = Column(String, nullable=False)
    status = Column(String, default="pending")
    failure_reason = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)
    external_transaction_id = Column(String)
    receipt_url = Column(String)
    clinic = relationship("Clinic", back_populates="withdrawal_requests")

# ═══════════════════════════════════════════════════════════
# SUGESTÕES DE TRATAMENTO
# Dentista cria durante/após consulta → paciente aceita/recusa
# ═══════════════════════════════════════════════════════════

class TreatmentSuggestion(Base):
    """
    Sugestão de tratamento criada pela clínica/dentista durante ou após
    uma consulta. O paciente pode aceitar (gerando novo agendamento com
    pagamento) ou recusar.

    status:
      pending   → aguardando resposta do paciente
      accepted  → paciente aceitou e agendamento foi criado
      declined  → paciente recusou
      expired   → não respondeu dentro do prazo (30 dias)
      cancelled → clínica cancelou antes de o paciente responder
    """
    __tablename__ = "treatment_suggestions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # Consulta de origem (pode ser qualquer consulta concluída ou em andamento)
    origin_appointment_id = Column(String, ForeignKey("appointments.id"), nullable=False)

    # Clínica que fez a sugestão
    clinic_id = Column(String, ForeignKey("clinics.id"), nullable=False)

    # Paciente alvo
    patient_id = Column(String, ForeignKey("users.id"), nullable=False)

    # Procedimento sugerido
    procedure_id = Column(String, ForeignKey("procedures.id"), nullable=False)

    # Dentista responsável (nome livre — pode não ter login)
    dentist_name = Column(String, nullable=False)

    # Notas clínicas / motivo da sugestão (visível para o paciente)
    notes = Column(Text)

    # Urgência da sugestão: routine | soon | urgent
    priority = Column(String, default="routine")

    # Preço sugerido (da tabela do procedimento da clínica)
    suggested_price = Column(Float, nullable=False)

    # Status do fluxo
    status = Column(String, default="pending", index=True)

    # Agendamento gerado ao aceitar
    resulting_appointment_id = Column(String, ForeignKey("appointments.id"), nullable=True)

    # Datas
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)          # 30 dias após criação
    responded_at = Column(DateTime)        # quando o paciente respondeu

    # Relacionamentos
    origin_appointment = relationship(
        "Appointment",
        back_populates="treatment_suggestions",
        foreign_keys=[origin_appointment_id],
    )
    clinic = relationship("Clinic", back_populates="treatment_suggestions")
    procedure = relationship("Procedure")
    patient = relationship("User")
    