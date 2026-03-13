import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal, engine
from models.models import (
    Base, Procedure, Clinic, ClinicProcedure,
    User, UniqueEmail, PlatformEmergencyPrice,
    Appointment, Payment, TreatmentSuggestion,
    ClinicFinancialAccount, Notification,
)
from core.security import hash_password
import uuid
from datetime import datetime, timedelta

# Criar tabelas se não existirem
Base.metadata.create_all(bind=engine)

# ═══════════════════════════════════════════════════════
# CREDENCIAIS PADRÃO
# ═══════════════════════════════════════════════════════
DEFAULT_PASSWORD = "17085422Km."

ADMIN_EMAIL    = "nalucard76@gmail.com"
CLINIC_EMAIL   = "kaiquealef44@gmail.com"
PATIENT_EMAIL  = "kaiquealef42@gmail.com"
PATIENT2_EMAIL = "maria.silva@example.com"


# ═══════════════════════════════════════════════════════
# PLATAFORMA
# ═══════════════════════════════════════════════════════

def seed_platform_settings():
    """Preço global de urgência."""
    db = SessionLocal()
    if not db.query(PlatformEmergencyPrice).first():
        db.add(PlatformEmergencyPrice(
            id=str(uuid.uuid4()),
            price=99.99,
            updated_by="system",
        ))
        db.commit()
        print("✓ Preço global de emergência configurado (R$ 99,99)")
    db.close()


# ═══════════════════════════════════════════════════════
# PROCEDIMENTOS GLOBAIS
# ═══════════════════════════════════════════════════════

def seed_procedures():
    """Procedimentos globais definidos pelo admin."""
    db = SessionLocal()

    procedures = [
        # name, category, description, price, duration_min, max_upper, max_lower
        ("Consulta",              "prevencao",      "Consulta de avaliação odontológica completa",                     99.99,   30,  None, None),
        ("Limpeza",               "prevencao",      "Limpeza, profilaxia e remoção de tártaro",                       150.00,  45,  None, None),
        ("Clareamento",           "estetica",       "Clareamento dental a laser ou com moldeira",                     800.00,  60,  None, None),
        ("Urgência",              "urgencia",       "Atendimento de urgência odontológica",                           120.00,  30,  None, None),
        ("Canal",                 "tratamento",     "Tratamento endodôntico (canal)",                                  600.00,  90,  None, None),
        ("Extração",              "cirurgia",       "Extração dentária simples ou complexa",                          200.00,  40,  None, None),
        ("Lente de Contato",      "estetica",       "Lentes de contato dental (por dente)",                          1200.00,  90,  16,   16),
        ("Implante",              "cirurgia",       "Implante dentário osseointegrado",                              2500.00, 120,  None, None),
        ("Aparelho Ortodôntico",  "ortodontia",     "Instalação de aparelho fixo metálico ou estético",              1800.00,  60,  None, None),
        ("Prótese Dentária",      "protese",        "Prótese total ou parcial removível",                            1500.00,  90,  None, None),
        ("Restauração",           "tratamento",     "Restauração com resina composta",                                250.00,  45,  None, None),
        ("Cirurgia de Gengiva",   "cirurgia",       "Gengivoplastia e tratamento periodontal cirúrgico",              900.00,  90,  None, None),
        ("Raio-X Panorâmico",     "diagnostico",    "Radiografia panorâmica digital",                                 120.00,  15,  None, None),
        ("Placa de Bruxismo",     "tratamento",     "Placa de mordida para bruxismo e DTM",                           450.00,  30,  None, None),
        ("Selante",               "prevencao",      "Selante de fissura para proteção dos dentes",                     80.00,  20,  None, None),
    ]

    created = 0
    for name, cat, desc, price, dur, mu, ml in procedures:
        if not db.query(Procedure).filter(Procedure.name == name).first():
            db.add(Procedure(
                id=str(uuid.uuid4()),
                name=name,
                category=cat,
                description=desc,
                price=price,
                default_duration_minutes=dur,
                is_active=True,
                max_upper_teeth=mu,
                max_lower_teeth=ml,
            ))
            created += 1
            print(f"  + Procedimento: {name} (R$ {price:.2f})")

    db.commit()
    db.close()
    print(f"✓ {created} procedimento(s) criado(s).")


# ═══════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════

def seed_admin():
    db = SessionLocal()
    if db.query(User).filter(User.email == ADMIN_EMAIL).first():
        print("✓ Administrador já existe, pulando...")
        db.close()
        return

    admin = User(
        id=str(uuid.uuid4()),
        name="Administrador do Sistema",
        email=ADMIN_EMAIL,
        password_hash=hash_password(DEFAULT_PASSWORD),
        cpf="00000000000",
        phone="(00) 00000-0000",
        role="admin",
        is_admin=True,
        is_active=True,
    )
    db.add(admin)
    db.add(UniqueEmail(email=ADMIN_EMAIL, entity_type="admin", entity_id=admin.id))
    db.commit()
    print(f"✓ Administrador criado: {admin.email}")
    db.close()


# ═══════════════════════════════════════════════════════
# CLÍNICA DE EXEMPLO
# ═══════════════════════════════════════════════════════

def seed_sample_clinic():
    db = SessionLocal()
    if db.query(Clinic).filter(Clinic.email == CLINIC_EMAIL).first():
        print("✓ Clínica de exemplo já existe, pulando...")
        db.close()
        return

    clinic = Clinic(
        id=str(uuid.uuid4()),
        name="Clínica Sorriso Feliz",
        email=CLINIC_EMAIL,
        password_hash=hash_password(DEFAULT_PASSWORD),
        role="clinica",
        cnpj="12345678000195",
        phone="(11) 99999-9999",
        address="Av. Paulista, 1000 - Bela Vista, São Paulo, SP",
        street="Av. Paulista",
        number="1000",
        neighborhood="Bela Vista",
        city="São Paulo",
        state="SP",
        zip_code="01310-100",
        latitude=-16.737039,
        longitude=-49.205551,
        description="Clínica odontológica completa com atendimento de urgência e procedimentos estéticos.",
        is_online=True,
        is_active=True,
        emergency_enabled=True,
    )
    db.add(clinic)
    db.add(UniqueEmail(email=CLINIC_EMAIL, entity_type="clinica", entity_id=clinic.id))
    db.commit()

    # Conta financeira da clínica
    db.add(ClinicFinancialAccount(
        id=str(uuid.uuid4()),
        clinic_id=clinic.id,
        bank_code="001",
        agency="0001",
        account="12345-6",
        account_type="corrente",
        pix_key=CLINIC_EMAIL,
        available_balance=0.0,
        pending_balance=0.0,
    ))
    db.commit()
    print(f"✓ Clínica criada: {clinic.name} ({clinic.email})")
    db.close()


# ═══════════════════════════════════════════════════════
# PACIENTES DE EXEMPLO
# ═══════════════════════════════════════════════════════

def seed_patients():
    db = SessionLocal()

    patients = [
        {
            "name": "Kaique Alef",
            "email": PATIENT_EMAIL,
            "cpf": "14506276680",
            "phone": "(11) 98888-8888",
        },
        {
            "name": "Maria Silva",
            "email": PATIENT2_EMAIL,
            "cpf": "98765432100",
            "phone": "(11) 97777-7777",
        },
    ]

    for p in patients:
        if db.query(User).filter(User.email == p["email"]).first():
            print(f"✓ Paciente '{p['name']}' já existe, pulando...")
            continue

        user = User(
            id=str(uuid.uuid4()),
            name=p["name"],
            email=p["email"],
            password_hash=hash_password(DEFAULT_PASSWORD),
            cpf=p["cpf"],
            phone=p["phone"],
            role="paciente",
            is_admin=False,
            is_active=True,
        )
        db.add(user)
        db.add(UniqueEmail(email=p["email"], entity_type="paciente", entity_id=user.id))
        db.commit()
        print(f"✓ Paciente criado: {user.name} ({user.email})")

    db.close()


# ═══════════════════════════════════════════════════════
# VINCULAR PROCEDIMENTOS À CLÍNICA
# ═══════════════════════════════════════════════════════

def seed_clinic_procedures():
    db = SessionLocal()

    clinics    = db.query(Clinic).all()
    procedures = db.query(Procedure).all()
    created    = 0

    for clinic in clinics:
        for procedure in procedures:
            exists = db.query(ClinicProcedure).filter(
                ClinicProcedure.clinic_id    == clinic.id,
                ClinicProcedure.procedure_id == procedure.id,
            ).first()
            if not exists:
                db.add(ClinicProcedure(
                    id=str(uuid.uuid4()),
                    clinic_id=clinic.id,
                    procedure_id=procedure.id,
                    is_active=True,
                    price=procedure.price,
                    duration_minutes=procedure.default_duration_minutes,
                ))
                created += 1
        print(f"  + Procedimentos vinculados: {clinic.name}")

    db.commit()
    db.close()
    print(f"✓ {created} vínculo(s) clínica↔procedimento criado(s).")


# ═══════════════════════════════════════════════════════
# CONSULTAS DE DEMONSTRAÇÃO
# ═══════════════════════════════════════════════════════

def seed_sample_appointments():
    """
    Cria consultas de demonstração em vários estados para facilitar
    o teste de todos os fluxos do app.
    """
    db = SessionLocal()

    clinic  = db.query(Clinic).filter(Clinic.email == CLINIC_EMAIL).first()
    patient = db.query(User).filter(User.email == PATIENT_EMAIL).first()

    if not clinic or not patient:
        print("⚠ Clínica ou paciente não encontrados, pulando consultas de demonstração.")
        db.close()
        return

    if db.query(Appointment).filter(Appointment.patient_id == patient.id).first():
        print("✓ Consultas de demonstração já existem, pulando...")
        db.close()
        return

    consulta_proc  = db.query(Procedure).filter(Procedure.name == "Consulta").first()
    limpeza_proc   = db.query(Procedure).filter(Procedure.name == "Limpeza").first()
    canal_proc     = db.query(Procedure).filter(Procedure.name == "Canal").first()
    restauracao_p  = db.query(Procedure).filter(Procedure.name == "Restauração").first()

    now = datetime.utcnow()

    appointments_data = [
        # (procedure, status, type, scheduled_at_delta_days, total, platform_fee, clinic_amount)
        (consulta_proc,  "completed",        "scheduled",  -30, 99.99,   0.00,   99.99),  # 1ª consulta → sem taxa
        (limpeza_proc,   "completed",        "scheduled",  -15, 150.00,  22.50,  127.50),
        (canal_proc,     "confirmed",        "scheduled",    5, 600.00,  90.00,  510.00),
        (restauracao_p,  "awaiting_payment", "scheduled",    7, 250.00,  37.50,  212.50),
    ]

    created_appts = []
    for proc, status, atype, delta, total, pfee, camount in appointments_data:
        if not proc:
            continue
        appt = Appointment(
            id=str(uuid.uuid4()),
            patient_id=patient.id,
            clinic_id=clinic.id,
            procedure_id=proc.id,
            service_type="procedure",
            status=status,
            type=atype,
            scheduled_at=now + timedelta(days=delta),
            patient_latitude=-16.737039,
            patient_longitude=-49.205551,
            total_amount=total,
            platform_fee=pfee,
            clinic_amount=camount,
            completed_at=now + timedelta(days=delta) if status == "completed" else None,
        )
        db.add(appt)
        created_appts.append((appt, proc, status))
        print(f"  + Consulta: {proc.name} — {status}")

    db.commit()
    print(f"✓ {len(created_appts)} consulta(s) de demonstração criada(s).")

    # Pagamentos para as consultas concluídas
    for appt, proc, status in created_appts:
        if status == "completed":
            db.add(Payment(
                id=str(uuid.uuid4()),
                appointment_id=appt.id,
                amount=appt.total_amount,
                platform_fee=appt.platform_fee,
                clinic_amount=appt.clinic_amount,
                payment_method="pix",
                status="approved",
                external_id=f"demo_{appt.id[:8]}",
                paid_at=appt.completed_at,
            ))
    db.commit()

    db.close()


# ═══════════════════════════════════════════════════════
# SUGESTÕES DE TRATAMENTO DE DEMONSTRAÇÃO
# ═══════════════════════════════════════════════════════

def seed_sample_suggestions():
    """
    Cria sugestões de tratamento de demonstração para que o paciente
    já veja o fluxo ao abrir o app.
    """
    db = SessionLocal()

    clinic  = db.query(Clinic).filter(Clinic.email == CLINIC_EMAIL).first()
    patient = db.query(User).filter(User.email == PATIENT_EMAIL).first()

    if not clinic or not patient:
        print("⚠ Clínica ou paciente não encontrados, pulando sugestões de demonstração.")
        db.close()
        return

    if db.query(TreatmentSuggestion).filter(TreatmentSuggestion.patient_id == patient.id).first():
        print("✓ Sugestões de demonstração já existem, pulando...")
        db.close()
        return

    # Pega a consulta concluída mais recente como origem
    origin = db.query(Appointment).filter(
        Appointment.patient_id == patient.id,
        Appointment.status == "completed",
    ).order_by(Appointment.completed_at.desc()).first()

    if not origin:
        print("⚠ Nenhuma consulta concluída encontrada, pulando sugestões.")
        db.close()
        return

    canal_proc    = db.query(Procedure).filter(Procedure.name == "Canal").first()
    raio_x_proc   = db.query(Procedure).filter(Procedure.name == "Raio-X Panorâmico").first()
    clareamento_p = db.query(Procedure).filter(Procedure.name == "Clareamento").first()

    suggestions_data = [
        # (procedure, dentist_name, notes, priority, price)
        (
            canal_proc,
            "Dr. Carlos Mendes",
            "Detectamos cárie profunda no dente 36 com possível comprometimento pulpar. "
            "Recomendo avaliação e início do tratamento em até 30 dias para evitar abscesso.",
            "urgent",
            600.00,
        ),
        (
            raio_x_proc,
            "Dra. Ana Paula Souza",
            "Radiografia panorâmica de controle após limpeza para avaliar a saúde periodontal geral.",
            "routine",
            120.00,
        ),
        (
            clareamento_p,
            "Dr. Carlos Mendes",
            "Após a limpeza, seus dentes estão em ótimas condições para o clareamento. "
            "Aproveite o momento para potencializar o resultado estético!",
            "soon",
            800.00,
        ),
    ]

    now = datetime.utcnow()
    created = 0
    for proc, dentist, notes, priority, price in suggestions_data:
        if not proc:
            continue
        suggestion = TreatmentSuggestion(
            id=str(uuid.uuid4()),
            origin_appointment_id=origin.id,
            clinic_id=clinic.id,
            patient_id=patient.id,
            procedure_id=proc.id,
            dentist_name=dentist,
            notes=notes,
            priority=priority,
            suggested_price=price,
            status="pending",
            expires_at=now + timedelta(days=30),
        )
        db.add(suggestion)

        # Notificação para o paciente
        db.add(Notification(
            id=str(uuid.uuid4()),
            user_id=patient.id,
            user_type="paciente",
            title="Nova sugestão de tratamento! 🦷",
            message=f"Dr(a). {dentist} de {clinic.name} sugeriu: {proc.name}",
            type="treatment_suggestion",
            is_read=False,
            data=str({
                "suggestion_id": suggestion.id,
                "procedure_name": proc.name,
                "priority": priority,
                "clinic_name": clinic.name,
            }),
        ))
        created += 1
        print(f"  + Sugestão [{priority}]: {proc.name} — Dr(a). {dentist}")

    db.commit()
    db.close()
    print(f"✓ {created} sugestão(ões) de demonstração criada(s).")


# ═══════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n🌱 Iniciando seed do banco de dados...\n")
    print("━" * 50)

    print("\n[1/7] Configurações da plataforma")
    seed_platform_settings()

    print("\n[2/7] Procedimentos globais")
    seed_procedures()

    print("\n[3/7] Administrador")
    seed_admin()

    print("\n[4/7] Clínica de exemplo")
    seed_sample_clinic()

    print("\n[5/7] Pacientes de exemplo")
    seed_patients()

    print("\n[6/7] Vínculos clínica ↔ procedimentos")
    seed_clinic_procedures()

    print("\n[7/7] Dados de demonstração (consultas + sugestões)")
    seed_sample_appointments()
    seed_sample_suggestions()

    print("\n" + "━" * 50)
    print("✅ Seed concluído com sucesso!\n")
    print("👤 Credenciais para login:")
    print(f"   Administrador : {ADMIN_EMAIL}   / {DEFAULT_PASSWORD}")
    print(f"   Clínica       : {CLINIC_EMAIL}  / {DEFAULT_PASSWORD}")
    print(f"   Paciente 1    : {PATIENT_EMAIL} / {DEFAULT_PASSWORD}")
    print(f"   Paciente 2    : {PATIENT2_EMAIL} / {DEFAULT_PASSWORD}")
    print("━" * 50 + "\n")