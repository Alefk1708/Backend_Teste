import sys
import os

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import SessionLocal, engine
from models.models import (
    Base, Procedure, Clinic, ClinicProcedure,
    User, UniqueEmail, PlatformEmergencyPrice,
    Appointment, Payment, TreatmentSuggestion,
    ClinicFinancialAccount, Notification,
    WorkSchedule, AppointmentSlot,
)
from core.security import hash_password
import uuid
from datetime import datetime, timedelta, date

# Criar tabelas se não existirem
Base.metadata.create_all(bind=engine)

DEFAULT_PASSWORD = "17085422Km."
ADMIN_EMAIL    = "nalucard76@gmail.com"
CLINIC_EMAIL   = "kaiquealef44@gmail.com"
PATIENT_EMAIL  = "kaiquealef42@gmail.com"
PATIENT2_EMAIL = "maria.silva@example.com"


def seed_platform_settings():
    db = SessionLocal()
    if not db.query(PlatformEmergencyPrice).first():
        db.add(PlatformEmergencyPrice(id=str(uuid.uuid4()), price=99.99, updated_by="system"))
        db.commit()
        print("+ Preco global de emergencia configurado (R$ 99,99)")
    db.close()


def seed_procedures():
    db = SessionLocal()
    procedures = [
        ("Consulta",             "prevencao",     "Consulta de avaliacao odontologica completa",                    99.99,   30,  None, None),
        ("Limpeza",              "prevencao",     "Limpeza, profilaxia e remocao de tartaro",                      150.00,  45,  None, None),
        ("Clareamento",          "estetica",      "Clareamento dental a laser ou com moldeira",                    800.00,  60,  None, None),
        ("Urgencia",             "urgencia",      "Atendimento de urgencia odontologica",                          120.00,  30,  None, None),
        ("Canal",                "tratamento",    "Tratamento endodontico (canal)",                                600.00,  90,  None, None),
        ("Extracao",             "cirurgia",      "Extracao dentaria simples ou complexa",                         200.00,  40,  None, None),
        ("Lente de Contato",     "lentes_contato","Lentes de contato dental (por dente)",                        1200.00,  90,  16,   16),
        ("Implante",             "cirurgia",      "Implante dentario osseointegrado",                            2500.00, 120,  None, None),
        ("Aparelho Ortodontico", "ortodontia",    "Instalacao de aparelho fixo metalico ou estetico",            1800.00,  60,  None, None),
        ("Protese Dentaria",     "protese",       "Protese total ou parcial removivel",                          1500.00,  90,  None, None),
        ("Restauracao",          "tratamento",    "Restauracao com resina composta",                               250.00,  45,  None, None),
        ("Cirurgia de Gengiva",  "cirurgia",      "Gengivoplastia e tratamento periodontal cirurgico",             900.00,  90,  None, None),
        ("Raio-X Panoramico",    "diagnostico",   "Radiografia panoramica digital",                                120.00,  15,  None, None),
        ("Placa de Bruxismo",    "tratamento",    "Placa de mordida para bruxismo e DTM",                          450.00,  30,  None, None),
        ("Selante",              "prevencao",     "Selante de fissura para protecao dos dentes",                    80.00,  20,  None, None),
    ]
    created = 0
    for name, cat, desc, price, dur, mu, ml in procedures:
        if not db.query(Procedure).filter(Procedure.name == name).first():
            db.add(Procedure(
                id=str(uuid.uuid4()), name=name, category=cat, description=desc,
                price=price, default_duration_minutes=dur, is_active=True,
                max_upper_teeth=mu, max_lower_teeth=ml,
            ))
            created += 1
            print(f"  + Procedimento: {name} (R$ {price:.2f})")
    db.commit()
    db.close()
    print(f"OK {created} procedimento(s) criado(s).")


def seed_admin():
    db = SessionLocal()
    if db.query(User).filter(User.email == ADMIN_EMAIL).first():
        print("OK Administrador ja existe.")
        db.close()
        return
    admin = User(
        id=str(uuid.uuid4()), name="Administrador do Sistema",
        email=ADMIN_EMAIL, password_hash=hash_password(DEFAULT_PASSWORD),
        cpf="00000000000", phone="(00) 00000-0000",
        role="admin", is_admin=True, is_active=True,
    )
    db.add(admin)
    db.add(UniqueEmail(email=ADMIN_EMAIL, entity_type="admin", entity_id=admin.id))
    db.commit()
    print(f"OK Administrador criado: {admin.email}")
    db.close()


def seed_sample_clinic():
    db = SessionLocal()
    if db.query(Clinic).filter(Clinic.email == CLINIC_EMAIL).first():
        print("OK Clinica de exemplo ja existe.")
        db.close()
        return
    clinic = Clinic(
        id=str(uuid.uuid4()), name="Clinica Sorriso Feliz",
        email=CLINIC_EMAIL, password_hash=hash_password(DEFAULT_PASSWORD),
        role="clinica", cnpj="12345678000195", phone="(11) 99999-9999",
        address="Av. Paulista, 1000 - Bela Vista, Sao Paulo, SP",
        street="Av. Paulista", number="1000", neighborhood="Bela Vista",
        city="Sao Paulo", state="SP", zip_code="01310-100",
        latitude=-16.737039, longitude=-49.205551,
        description="Clinica odontologica completa com atendimento de urgencia e procedimentos esteticos.",
        is_online=True, is_active=True, emergency_enabled=True,
    )
    db.add(clinic)
    db.add(UniqueEmail(email=CLINIC_EMAIL, entity_type="clinica", entity_id=clinic.id))
    db.commit()
    db.add(ClinicFinancialAccount(
        id=str(uuid.uuid4()), clinic_id=clinic.id,
        bank_code="001", agency="0001", account="12345-6",
        account_type="corrente", pix_key=CLINIC_EMAIL,
        available_balance=0.0, pending_balance=0.0,
    ))
    db.commit()
    print(f"OK Clinica criada: {clinic.name} ({clinic.email})")
    db.close()


def seed_patients():
    db = SessionLocal()
    patients = [
        {"name": "Kaique Alef", "email": PATIENT_EMAIL,  "cpf": "14506276680", "phone": "(11) 98888-8888"},
        {"name": "Maria Silva", "email": PATIENT2_EMAIL, "cpf": "98765432100", "phone": "(11) 97777-7777"},
    ]
    for p in patients:
        if db.query(User).filter(User.email == p["email"]).first():
            print(f"OK Paciente '{p['name']}' ja existe.")
            continue
        user = User(
            id=str(uuid.uuid4()), name=p["name"], email=p["email"],
            password_hash=hash_password(DEFAULT_PASSWORD),
            cpf=p["cpf"], phone=p["phone"],
            role="paciente", is_admin=False, is_active=True,
        )
        db.add(user)
        db.add(UniqueEmail(email=p["email"], entity_type="paciente", entity_id=user.id))
        db.commit()
        print(f"OK Paciente criado: {user.name} ({user.email})")
    db.close()


def seed_clinic_procedures():
    db = SessionLocal()
    clinics, procedures, created = db.query(Clinic).all(), db.query(Procedure).all(), 0
    for clinic in clinics:
        for procedure in procedures:
            exists = db.query(ClinicProcedure).filter(
                ClinicProcedure.clinic_id == clinic.id,
                ClinicProcedure.procedure_id == procedure.id,
            ).first()
            if not exists:
                db.add(ClinicProcedure(
                    id=str(uuid.uuid4()), clinic_id=clinic.id,
                    procedure_id=procedure.id, is_active=True,
                    price=procedure.price, duration_minutes=procedure.default_duration_minutes,
                ))
                created += 1
        print(f"  + Procedimentos vinculados: {clinic.name}")
    db.commit()
    db.close()
    print(f"OK {created} vinculo(s) clinica<->procedimento criado(s).")


# ───────────────────────────────────────────────────────────────
# WORK SCHEDULES
# ───────────────────────────────────────────────────────────────

def seed_work_schedules():
    """
    Seg-Sex: 09:00-18:00 | almoco 12:00-13:00 | 30 min
    Sabado:  09:00-13:00 | sem almoco         | 30 min
    """
    db = SessionLocal()
    clinic = db.query(Clinic).filter(Clinic.email == CLINIC_EMAIL).first()
    if not clinic:
        print("AVISO Clinica nao encontrada, pulando WorkSchedules.")
        db.close()
        return
    if db.query(WorkSchedule).filter(WorkSchedule.clinic_id == clinic.id).first():
        print("OK Regras de trabalho ja existem.")
        db.close()
        return

    day_names = ["Segunda","Terca","Quarta","Quinta","Sexta","Sabado"]

    # Seg(0)–Sex(4)
    for d in range(5):
        db.add(WorkSchedule(
            id=str(uuid.uuid4()), clinic_id=clinic.id,
            day_of_week=d,
            start_time="09:00", end_time="18:00",
            lunch_start="12:00", lunch_end="13:00",
            slot_duration_minutes=30, is_active=True,
        ))
        print(f"  + Regra: {day_names[d]} 09:00-18:00 (almoco 12-13) | 30 min")

    # Sabado(5)
    db.add(WorkSchedule(
        id=str(uuid.uuid4()), clinic_id=clinic.id,
        day_of_week=5,
        start_time="09:00", end_time="13:00",
        lunch_start=None, lunch_end=None,
        slot_duration_minutes=30, is_active=True,
    ))
    print(f"  + Regra: Sabado 09:00-13:00 | 30 min")

    db.commit()
    db.close()
    print("OK Regras de trabalho criadas.")


# ───────────────────────────────────────────────────────────────
# APPOINTMENT SLOTS
# ───────────────────────────────────────────────────────────────

def seed_appointment_slots():
    """
    Gera slots para os proximos 14 dias.
    Aplica cenarios de demo nos slots de HOJE.
    """
    db = SessionLocal()
    clinic = db.query(Clinic).filter(Clinic.email == CLINIC_EMAIL).first()
    if not clinic:
        print("AVISO Clinica nao encontrada, pulando slots.")
        db.close()
        return
    if db.query(AppointmentSlot).filter(AppointmentSlot.clinic_id == clinic.id).first():
        print("OK Slots ja existem.")
        db.close()
        return

    schedules = db.query(WorkSchedule).filter(
        WorkSchedule.clinic_id == clinic.id,
        WorkSchedule.is_active == True,
    ).all()
    if not schedules:
        print("AVISO Nenhuma WorkSchedule encontrada, pulando slots.")
        db.close()
        return

    sched_map = {s.day_of_week: s for s in schedules}
    today = date.today()
    total = 0

    for delta in range(-1, 15):  # -1 = ontem (demos passados), 0..14 = futuro
        target = today + timedelta(days=delta)
        weekday = target.weekday()  # 0=Mon..6=Sun
        if weekday not in sched_map:
            continue

        s = sched_map[weekday]
        sh, sm = map(int, s.start_time.split(":"))
        eh, em = map(int, s.end_time.split(":"))
        dur = timedelta(minutes=s.slot_duration_minutes)

        lunch_s = lunch_e = None
        if s.lunch_start and s.lunch_end:
            lsh, lsm = map(int, s.lunch_start.split(":"))
            leh, lem = map(int, s.lunch_end.split(":"))
            lunch_s = datetime(target.year, target.month, target.day, lsh, lsm)
            lunch_e = datetime(target.year, target.month, target.day, leh, lem)

        cur     = datetime(target.year, target.month, target.day, sh, sm)
        day_end = datetime(target.year, target.month, target.day, eh, em)

        while cur + dur <= day_end:
            slot_end = cur + dur
            if lunch_s and lunch_e and cur < lunch_e and slot_end > lunch_s:
                cur = lunch_e
                continue
            db.add(AppointmentSlot(
                id=str(uuid.uuid4()), clinic_id=clinic.id,
                slot_date=target, start_time=cur, end_time=slot_end,
                status="available",
            ))
            total += 1
            cur += dur

    db.commit()
    print(f"OK {total} slot(s) gerado(s) para os proximos 14 dias.")

    _seed_demo_slot_scenarios(db, clinic, today)
    db.close()


def _seed_demo_slot_scenarios(db, clinic, today):
    """Coloca slots de HOJE em estados diferentes para demo completa."""
    today_slots = db.query(AppointmentSlot).filter(
        AppointmentSlot.clinic_id == clinic.id,
        AppointmentSlot.slot_date == today,
        AppointmentSlot.status    == "available",
    ).order_by(AppointmentSlot.start_time).all()

    if len(today_slots) < 5:
        print("AVISO Poucos slots hoje — pulando cenarios de demo.")
        return

    patient  = db.query(User).filter(User.email == PATIENT_EMAIL).first()
    patient2 = db.query(User).filter(User.email == PATIENT2_EMAIL).first()
    consulta = db.query(Procedure).filter(Procedure.name == "Consulta").first()
    limpeza  = db.query(Procedure).filter(Procedure.name == "Limpeza").first()
    now = datetime.utcnow()

    def make_appointment(patient_u, proc, status, amount, fee, camp, svc):
        appt = Appointment(
            id=str(uuid.uuid4()),
            patient_id=patient_u.id, clinic_id=clinic.id,
            procedure_id=proc.id if proc else None,
            service_type=svc, status=status, type="scheduled",
            scheduled_at=today_slots[0].start_time,
            total_amount=amount, platform_fee=fee, clinic_amount=camp,
            patient_latitude=clinic.latitude, patient_longitude=clinic.longitude,
        )
        db.add(appt)
        db.flush()
        return appt

    # Slot 0 — completed
    s0 = today_slots[0]
    a0 = make_appointment(patient, consulta, "completed", 99.99, 0.00, 99.99, "first_consultation")
    a0.completed_at = now
    db.add(Payment(id=str(uuid.uuid4()), appointment_id=a0.id,
        amount=99.99, platform_fee=0.00, clinic_amount=99.99,
        payment_method="pix", status="completed",
        external_id=f"demo_{a0.id[:8]}", paid_at=now))
    s0.status = "completed"; s0.appointment_id = a0.id

    # Slot 1 — in_progress
    s1 = today_slots[1]
    p2 = patient2 if patient2 else patient
    a1 = make_appointment(p2, limpeza, "in_progress", 150.00, 22.50, 127.50, "procedure")
    db.add(Payment(id=str(uuid.uuid4()), appointment_id=a1.id,
        amount=150.00, platform_fee=22.50, clinic_amount=127.50,
        payment_method="credit_card", status="completed",
        external_id=f"demo_{a1.id[:8]}", paid_at=now))
    s1.status = "in_progress"; s1.appointment_id = a1.id

    # Slot 2 — waiting (sala de espera)
    s2 = today_slots[2]
    a2 = make_appointment(patient, consulta, "confirmed", 99.99, 15.00, 84.99, "procedure")
    db.add(Payment(id=str(uuid.uuid4()), appointment_id=a2.id,
        amount=99.99, platform_fee=15.00, clinic_amount=84.99,
        payment_method="pix", status="completed",
        external_id=f"demo_{a2.id[:8]}", paid_at=now))
    s2.status = "waiting"; s2.appointment_id = a2.id

    # Slot 3 — confirmed (pago, aguardando horario)
    s3 = today_slots[3]
    a3 = make_appointment(p2, limpeza, "confirmed", 150.00, 22.50, 127.50, "procedure")
    db.add(Payment(id=str(uuid.uuid4()), appointment_id=a3.id,
        amount=150.00, platform_fee=22.50, clinic_amount=127.50,
        payment_method="credit_card", status="completed",
        external_id=f"demo_{a3.id[:8]}", paid_at=now))
    s3.status = "confirmed"; s3.appointment_id = a3.id

    # Slot 4 — occupied (encaixe presencial)
    s4 = today_slots[4]
    s4.status = "occupied"; s4.walk_in_patient_name = "Joao (balcao)"

    # Slot 5 — reserved (reserva temporaria ativa ~8 min)
    if len(today_slots) > 5:
        s5 = today_slots[5]
        s5.status = "reserved"
        s5.reserved_by = patient.id if patient else None
        s5.reserved_at = now
        s5.reservation_expires_at = now + timedelta(minutes=8)

    db.commit()
    print("  Cenarios de hoje aplicados:")
    print("    [0] CONCLUIDO    — Kaique | Consulta")
    print("    [1] EM ATENDIMENTO — Maria | Limpeza")
    print("    [2] SALA DE ESPERA — Kaique | Consulta")
    print("    [3] CONFIRMADO (pago) — Maria | Limpeza")
    print("    [4] PRESENCIAL (balcao) — Joao")
    if len(today_slots) > 5:
        print("    [5] RESERVADO (~8 min) — Kaique")


# ───────────────────────────────────────────────────────────────
# CONSULTAS HISTORICAS DE DEMO
# ───────────────────────────────────────────────────────────────

def seed_sample_appointments():
    db = SessionLocal()
    clinic  = db.query(Clinic).filter(Clinic.email == CLINIC_EMAIL).first()
    patient = db.query(User).filter(User.email == PATIENT_EMAIL).first()

    if not clinic or not patient:
        print("AVISO Clinica ou paciente nao encontrados.")
        db.close()
        return
    if db.query(Appointment).filter(
        Appointment.patient_id == patient.id,
        Appointment.type == "scheduled",
    ).count() > 3:
        print("OK Consultas de demonstracao ja existem.")
        db.close()
        return

    consulta = db.query(Procedure).filter(Procedure.name == "Consulta").first()
    limpeza  = db.query(Procedure).filter(Procedure.name == "Limpeza").first()
    canal    = db.query(Procedure).filter(Procedure.name == "Canal").first()
    rest     = db.query(Procedure).filter(Procedure.name == "Restauracao").first()
    now = datetime.utcnow()

    demos = [
        (consulta, "completed",        -30, 99.99,  0.00,  99.99,  "first_consultation"),
        (limpeza,  "completed",        -15, 150.00, 22.50, 127.50, "procedure"),
        (canal,    "confirmed",          5, 600.00, 90.00, 510.00, "procedure"),
        (rest,     "awaiting_payment",   7, 250.00, 37.50, 212.50, "procedure"),
    ]
    created = 0
    for proc, status, delta, total, pfee, camp, svc in demos:
        if not proc:
            continue
        appt = Appointment(
            id=str(uuid.uuid4()),
            patient_id=patient.id, clinic_id=clinic.id,
            procedure_id=proc.id, service_type=svc,
            status=status, type="scheduled",
            scheduled_at=now + timedelta(days=delta),
            patient_latitude=clinic.latitude, patient_longitude=clinic.longitude,
            total_amount=total, platform_fee=pfee, clinic_amount=camp,
            completed_at=(now + timedelta(days=delta)) if status == "completed" else None,
        )
        db.add(appt)
        created += 1
        print(f"  + Consulta: {proc.name} — {status}")
    db.commit()
    print(f"OK {created} consulta(s) historica(s) criada(s).")

    for appt in db.query(Appointment).filter(
        Appointment.patient_id == patient.id,
        Appointment.status == "completed",
    ).all():
        if not db.query(Payment).filter(Payment.appointment_id == appt.id).first():
            db.add(Payment(
                id=str(uuid.uuid4()), appointment_id=appt.id,
                amount=appt.total_amount, platform_fee=appt.platform_fee,
                clinic_amount=appt.clinic_amount,
                payment_method="pix", status="completed",
                external_id=f"demo_{appt.id[:8]}", paid_at=appt.completed_at,
            ))
    db.commit()
    db.close()


# ───────────────────────────────────────────────────────────────
# SUGESTOES DE TRATAMENTO DE DEMO
# ───────────────────────────────────────────────────────────────

def seed_sample_suggestions():
    db = SessionLocal()
    clinic  = db.query(Clinic).filter(Clinic.email == CLINIC_EMAIL).first()
    patient = db.query(User).filter(User.email == PATIENT_EMAIL).first()
    if not clinic or not patient:
        print("AVISO Clinica ou paciente nao encontrados.")
        db.close()
        return
    if db.query(TreatmentSuggestion).filter(TreatmentSuggestion.patient_id == patient.id).first():
        print("OK Sugestoes de demonstracao ja existem.")
        db.close()
        return

    origin = db.query(Appointment).filter(
        Appointment.patient_id == patient.id,
        Appointment.status == "completed",
    ).order_by(Appointment.scheduled_at.desc()).first()
    if not origin:
        print("AVISO Nenhuma consulta concluida encontrada, pulando sugestoes.")
        db.close()
        return

    canal_p  = db.query(Procedure).filter(Procedure.name == "Canal").first()
    raio_p   = db.query(Procedure).filter(Procedure.name == "Raio-X Panoramico").first()
    clar_p   = db.query(Procedure).filter(Procedure.name == "Clareamento").first()

    now = datetime.utcnow()
    suggestions = [
        (canal_p,  "Dr. Carlos Mendes",
         "Detectamos carie profunda no dente 36. Recomendo inicio do tratamento em 30 dias.",
         "urgent", 600.00),
        (raio_p,   "Dra. Ana Paula Souza",
         "Radiografia de controle apos limpeza para avaliar saude periodontal.",
         "routine", 120.00),
        (clar_p,   "Dr. Carlos Mendes",
         "Apos a limpeza seus dentes estao prontos para o clareamento!",
         "soon", 800.00),
    ]
    created = 0
    for proc, dentist, notes, priority, price in suggestions:
        if not proc:
            continue
        sug = TreatmentSuggestion(
            id=str(uuid.uuid4()),
            origin_appointment_id=origin.id,
            clinic_id=clinic.id, patient_id=patient.id,
            procedure_id=proc.id, dentist_name=dentist,
            notes=notes, priority=priority,
            suggested_price=price, status="pending",
            expires_at=now + timedelta(days=30),
        )
        db.add(sug)
        db.add(Notification(
            id=str(uuid.uuid4()),
            user_id=patient.id, user_type="paciente",
            title="Nova sugestao de tratamento!",
            message=f"Dr(a). {dentist} de {clinic.name} sugeriu: {proc.name}",
            type="treatment_suggestion", is_read=False,
            data=str({"suggestion_id": sug.id, "procedure_name": proc.name,
                      "priority": priority, "clinic_name": clinic.name}),
        ))
        created += 1
        print(f"  + Sugestao [{priority}]: {proc.name} — Dr(a). {dentist}")

    db.commit()
    db.close()
    print(f"OK {created} sugestao(oes) criada(s).")


# ───────────────────────────────────────────────────────────────
# ENTRY POINT
# ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\nIniciando seed do banco de dados...\n")
    print("=" * 52)

    print("\n[1/9] Configuracoes da plataforma")
    seed_platform_settings()

    print("\n[2/9] Procedimentos globais")
    seed_procedures()

    print("\n[3/9] Administrador")
    seed_admin()

    print("\n[4/9] Clinica de exemplo")
    seed_sample_clinic()

    print("\n[5/9] Pacientes de exemplo")
    seed_patients()

    print("\n[6/9] Vinculos clinica <-> procedimentos")
    seed_clinic_procedures()

    print("\n[7/9] Regras de trabalho (WorkSchedule)")
    seed_work_schedules()

    print("\n[8/9] Slots de agendamento (AppointmentSlot)")
    seed_appointment_slots()

    print("\n[9/9] Dados historicos (consultas + sugestoes)")
    seed_sample_appointments()
    seed_sample_suggestions()

    print("\n" + "=" * 52)
    print("SEED CONCLUIDO COM SUCESSO!\n")
    print("Credenciais:")
    print(f"  Admin    : {ADMIN_EMAIL}   / {DEFAULT_PASSWORD}")
    print(f"  Clinica  : {CLINIC_EMAIL}  / {DEFAULT_PASSWORD}")
    print(f"  Paciente1: {PATIENT_EMAIL} / {DEFAULT_PASSWORD}")
    print(f"  Paciente2: {PATIENT2_EMAIL} / {DEFAULT_PASSWORD}")
    print("\nSlots gerados: proximos 14 dias (Seg-Sex + Sab)")
    print("  Seg-Sex: 09:00-18:00 | almoco 12-13 | 30 min")
    print("  Sabado:  09:00-13:00              | 30 min")
    print("\nCenarios de hoje para testar o painel da clinica:")
    print("  CONCLUIDO | EM ATENDIMENTO | SALA DE ESPERA")
    print("  CONFIRMADO | PRESENCIAL | RESERVADO")
    print("=" * 52 + "\n")
