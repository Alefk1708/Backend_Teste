"""
Router: /support
Sistema de tickets de suporte com banco de dados real + FAQ estático.

Usuário (paciente / clínica):
  GET  /support/faq                          → perguntas frequentes
  POST /support/tickets                      → abre novo ticket
  GET  /support/tickets/my                   → lista meus tickets
  GET  /support/tickets/my/{id}              → detalhe + mensagens
  POST /support/tickets/my/{id}/message      → adiciona mensagem ao ticket

Admin:
  GET   /support/tickets                     → lista todos os tickets
  GET   /support/tickets/{id}               → detalhe completo
  POST  /support/tickets/{id}/reply         → admin responde
  PATCH /support/tickets/{id}/status        → atualiza status
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Session, relationship
from database import get_db, Base, engine
from core.security import get_current_user, require_admin
from models.models import Notification, User
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import uuid, json

router = APIRouter(prefix="/support", tags=["support"])

# ═══════════════════════════════════════════════════
# MODELOS DE BANCO
# ═══════════════════════════════════════════════════

class SupportTicket(Base):
    __tablename__ = "support_tickets"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id     = Column(String, nullable=False, index=True)
    user_type   = Column(String, nullable=False)        # paciente | clinica | admin
    user_name   = Column(String, nullable=False)
    user_email  = Column(String, nullable=False)
    subject     = Column(String, nullable=False)
    category    = Column(String, nullable=False)        # payment|appointment|technical|suggestion|other
    priority    = Column(String, default="medium")      # low|medium|high
    status      = Column(String, default="open")        # open|in_progress|resolved|closed
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    messages    = relationship(
        "SupportMessage",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="SupportMessage.created_at",
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    ticket_id   = Column(String, ForeignKey("support_tickets.id"), nullable=False)
    sender_id   = Column(String, nullable=False)
    sender_type = Column(String, nullable=False)        # user | admin
    sender_name = Column(String, nullable=False)
    message     = Column(Text, nullable=False)
    is_admin    = Column(Boolean, default=False)
    created_at  = Column(DateTime, default=datetime.utcnow)
    ticket      = relationship("SupportTicket", back_populates="messages")


# Cria tabelas ao importar o módulo
Base.metadata.create_all(
    bind=engine,
    tables=[SupportTicket.__table__, SupportMessage.__table__],
)

# ═══════════════════════════════════════════════════
# SCHEMAS PYDANTIC
# ═══════════════════════════════════════════════════

class TicketCreate(BaseModel):
    subject:  str
    category: str           # payment|appointment|technical|suggestion|other
    message:  str           # primeira mensagem
    priority: str = "medium"

class TicketMsg(BaseModel):
    message: str

class StatusUpdate(BaseModel):
    status: str             # open|in_progress|resolved|closed

# ═══════════════════════════════════════════════════
# CONSTANTES
# ═══════════════════════════════════════════════════

CAT_LABELS = {
    "payment":     "Pagamento",
    "appointment": "Consulta / Agendamento",
    "technical":   "Problema técnico",
    "suggestion":  "Sugestão",
    "other":       "Outro",
}
STATUS_LABELS = {
    "open":        "Aberto",
    "in_progress": "Em andamento",
    "resolved":    "Resolvido",
    "closed":      "Fechado",
}
PRIORITY_LABELS = {
    "low":    "Baixa",
    "medium": "Média",
    "high":   "Alta",
}

FAQ_ITEMS = [
    {
        "id": "faq_1", "category": "appointment",
        "question": "Como agendar uma consulta?",
        "answer": "Na tela inicial busque por clínicas próximas ou use a barra de pesquisa. Selecione a clínica, o procedimento, a data e o horário. Confirme e realize o pagamento pelo app.",
    },
    {
        "id": "faq_2", "category": "payment",
        "question": "Quais formas de pagamento são aceitas?",
        "answer": "Aceitamos PIX (aprovação imediata) e cartão de crédito em até 12 parcelas, processados com segurança pelo Mercado Pago.",
    },
    {
        "id": "faq_3", "category": "appointment",
        "question": "Como cancelar uma consulta?",
        "answer": "Acesse 'Minhas Consultas', selecione a consulta e toque em 'Cancelar'. Cancelamentos com mais de 24h de antecedência recebem reembolso integral.",
    },
    {
        "id": "faq_4", "category": "appointment",
        "question": "O que é o atendimento de urgência?",
        "answer": "O modo urgência conecta você a clínicas disponíveis em até 10km em tempo real. Após aceitar, a clínica confirma o atendimento imediato.",
    },
    {
        "id": "faq_5", "category": "payment",
        "question": "Como funciona o reembolso?",
        "answer": "Cancelamentos com mais de 24h recebem reembolso integral em até 5 dias úteis. Problemas no atendimento devem ser reportados em até 24h após a consulta.",
    },
    {
        "id": "faq_6", "category": "payment",
        "question": "Meu pagamento PIX não foi confirmado, o que fazer?",
        "answer": "PIX é confirmado automaticamente em segundos. Se após 5 minutos ainda estiver pendente, toque em 'Gerar novo PIX'. Se persistir, abra um ticket de suporte.",
    },
    {
        "id": "faq_7", "category": "appointment",
        "question": "Como avaliar uma clínica?",
        "answer": "Após a consulta aparecer como 'Concluída' em 'Minhas Consultas', toque em 'Avaliar' para dar nota de 1 a 5 estrelas e deixar um comentário.",
    },
    {
        "id": "faq_8", "category": "technical",
        "question": "Como alterar minha senha?",
        "answer": "Acesse Perfil → Alterar e-mail e senha. Informe a senha atual, a nova (mínimo 8 caracteres) e confirme via código enviado ao seu e-mail.",
    },
    {
        "id": "faq_9", "category": "appointment",
        "question": "O que são sugestões de tratamento?",
        "answer": "Após uma consulta, o dentista pode sugerir procedimentos adicionais. Você recebe uma notificação e pode aceitar (agendando e pagando pelo app) ou recusar.",
    },
    {
        "id": "faq_10", "category": "other",
        "question": "Como entrar em contato com o suporte?",
        "answer": "Abra um ticket nesta tela. Nossa equipe responde em até 24h nos dias úteis. Para urgências, use a categoria 'Problema técnico' com prioridade Alta.",
    },
]

# ═══════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════

def _user_info(cu):
    u = cu["user"]
    return str(u.id), cu["payload"]["type"], getattr(u, "name", ""), getattr(u, "email", "")


def _ser_ticket(t: SupportTicket, include_msgs: bool = False) -> dict:
    d = {
        "id": t.id,
        "subject": t.subject,
        "category": t.category,
        "category_label": CAT_LABELS.get(t.category, t.category),
        "priority": t.priority,
        "priority_label": PRIORITY_LABELS.get(t.priority, t.priority),
        "status": t.status,
        "status_label": STATUS_LABELS.get(t.status, t.status),
        "user_id": t.user_id,
        "user_type": t.user_type,
        "user_name": t.user_name,
        "user_email": t.user_email,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "message_count": len(t.messages),
    }
    if include_msgs:
        d["messages"] = [
            {
                "id": m.id,
                "sender_name": m.sender_name,
                "sender_type": m.sender_type,
                "message": m.message,
                "is_admin": m.is_admin,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in t.messages
        ]
    return d


# ═══════════════════════════════════════════════════
# FAQ
# ═══════════════════════════════════════════════════

@router.get("/faq")
def get_faq(category: Optional[str] = Query(None)):
    """Retorna perguntas frequentes, opcionalmente filtradas por categoria."""
    items = FAQ_ITEMS
    if category:
        items = [f for f in items if f["category"] == category]
    return items


# ═══════════════════════════════════════════════════
# ENDPOINTS DO USUÁRIO (paciente / clínica)
# ═══════════════════════════════════════════════════

@router.post("/tickets", status_code=201)
def create_ticket(
    data: TicketCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Abre novo ticket de suporte."""
    uid, utype, name, email = _user_info(current_user)

    if not data.subject.strip():
        raise HTTPException(400, "Informe o assunto")
    if not data.message.strip():
        raise HTTPException(400, "Informe a descrição do problema")
    if data.category not in CAT_LABELS:
        raise HTTPException(400, "Categoria inválida")
    if data.priority not in PRIORITY_LABELS:
        raise HTTPException(400, "Prioridade inválida")

    ticket = SupportTicket(
        user_id=uid, user_type=utype, user_name=name, user_email=email,
        subject=data.subject.strip(), category=data.category,
        priority=data.priority, status="open",
    )
    db.add(ticket)
    db.flush()  # gera ticket.id

    db.add(SupportMessage(
        ticket_id=ticket.id, sender_id=uid, sender_type="user",
        sender_name=name, message=data.message.strip(), is_admin=False,
    ))

    # Notificar todos os admins ativos
    admins = db.query(User).filter(User.role == "admin", User.is_active == True).all()
    for admin in admins:
        db.add(Notification(
            id=str(uuid.uuid4()), user_id=str(admin.id), user_type="admin",
            title=f"Novo ticket de suporte [{CAT_LABELS.get(data.category, '')}]",
            message=f"{name}: {data.subject}",
            type="system", is_read=False,
            data=json.dumps({"ticket_id": ticket.id}),
        ))

    db.commit()
    return {"message": "Ticket aberto! Responderemos em até 24h.", "ticket_id": ticket.id}


@router.get("/tickets/my")
def my_tickets(
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Lista todos os tickets do usuário autenticado."""
    uid, utype, _, _ = _user_info(current_user)
    q = db.query(SupportTicket).filter(
        SupportTicket.user_id == uid,
        SupportTicket.user_type == utype,
    )
    if status:
        q = q.filter(SupportTicket.status == status)
    tickets = q.order_by(SupportTicket.created_at.desc()).all()
    return [_ser_ticket(t, include_msgs=True) for t in tickets]


@router.get("/tickets/my/{ticket_id}")
def my_ticket_detail(
    ticket_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Detalhe de um ticket do usuário com todas as mensagens."""
    uid, _, _, _ = _user_info(current_user)
    t = db.query(SupportTicket).filter(
        SupportTicket.id == ticket_id,
        SupportTicket.user_id == uid,
    ).first()
    if not t:
        raise HTTPException(404, "Ticket não encontrado")
    return _ser_ticket(t, include_msgs=True)


@router.post("/tickets/my/{ticket_id}/message")
def user_message(
    ticket_id: str,
    data: TicketMsg,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Usuário envia mensagem adicional em um ticket existente."""
    uid, _, name, _ = _user_info(current_user)
    t = db.query(SupportTicket).filter(
        SupportTicket.id == ticket_id,
        SupportTicket.user_id == uid,
    ).first()
    if not t:
        raise HTTPException(404, "Ticket não encontrado")
    if t.status in ("resolved", "closed"):
        raise HTTPException(400, "Ticket encerrado. Abra um novo para novas dúvidas.")
    if not data.message.strip():
        raise HTTPException(400, "Mensagem vazia")

    db.add(SupportMessage(
        ticket_id=ticket_id, sender_id=uid, sender_type="user",
        sender_name=name, message=data.message.strip(), is_admin=False,
    ))
    t.status = "open"   # reabre se estava aguardando
    db.commit()
    return {"message": "Mensagem enviada"}


# ═══════════════════════════════════════════════════
# ENDPOINTS DO ADMIN
# ═══════════════════════════════════════════════════

@router.get("/tickets")
def admin_list_tickets(
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Admin lista todos os tickets com filtros."""
    from sqlalchemy import or_
    q = db.query(SupportTicket)
    if status:    q = q.filter(SupportTicket.status == status)
    if priority:  q = q.filter(SupportTicket.priority == priority)
    if category:  q = q.filter(SupportTicket.category == category)
    if search:
        q = q.filter(or_(
            SupportTicket.subject.ilike(f"%{search}%"),
            SupportTicket.user_name.ilike(f"%{search}%"),
            SupportTicket.user_email.ilike(f"%{search}%"),
        ))
    tickets = q.order_by(SupportTicket.updated_at.desc()).offset(offset).limit(limit).all()
    return [_ser_ticket(t) for t in tickets]


@router.get("/tickets/{ticket_id}")
def admin_ticket_detail(
    ticket_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Admin lê ticket completo com todas as mensagens."""
    t = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(404, "Ticket não encontrado")
    return _ser_ticket(t, include_msgs=True)


@router.post("/tickets/{ticket_id}/reply")
def admin_reply(
    ticket_id: str,
    data: TicketMsg,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Admin responde a um ticket. Notifica o usuário que abriu."""
    admin = current_user["user"]
    t = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(404, "Ticket não encontrado")
    if not data.message.strip():
        raise HTTPException(400, "Mensagem vazia")

    db.add(SupportMessage(
        ticket_id=ticket_id, sender_id=str(admin.id), sender_type="admin",
        sender_name=admin.name, message=data.message.strip(), is_admin=True,
    ))
    t.status = "in_progress"

    # Notificar o usuário
    db.add(Notification(
        id=str(uuid.uuid4()), user_id=t.user_id, user_type=t.user_type,
        title="Resposta do suporte 💬",
        message=f"Sua solicitação '{t.subject}' recebeu uma resposta.",
        type="system", is_read=False,
        data=json.dumps({"ticket_id": ticket_id}),
    ))
    db.commit()
    return {"message": "Resposta enviada"}


@router.patch("/tickets/{ticket_id}/status")
def admin_update_status(
    ticket_id: str,
    data: StatusUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(require_admin),
):
    """Admin atualiza o status de um ticket."""
    if data.status not in STATUS_LABELS:
        raise HTTPException(400, "Status inválido")

    t = db.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(404, "Ticket não encontrado")

    t.status = data.status
    if data.status == "resolved":
        t.resolved_at = datetime.utcnow()
        db.add(Notification(
            id=str(uuid.uuid4()), user_id=t.user_id, user_type=t.user_type,
            title="Ticket resolvido ✓",
            message=f"Sua solicitação '{t.subject}' foi marcada como resolvida.",
            type="system", is_read=False,
            data=json.dumps({"ticket_id": ticket_id}),
        ))
    db.commit()
    return {"message": f"Status atualizado para '{STATUS_LABELS[data.status]}'"}
