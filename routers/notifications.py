"""
Router: /notifications
CRUD completo de notificações para paciente, clínica e admin.

GET    /notifications/               → lista notificações do usuário logado
GET    /notifications/unread-count   → badge count de não lidas
PATCH  /notifications/read-all       → marca todas como lidas
PATCH  /notifications/{id}/read      → marca uma como lida
DELETE /notifications/clear-all      → remove todas
DELETE /notifications/{id}           → remove uma
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from database import get_db
from core.security import get_current_user
from models.models import Notification

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Rótulos amigáveis por tipo
TYPE_LABELS = {
    "emergency":            "Urgência",
    "appointment":          "Consulta",
    "payment":              "Pagamento",
    "treatment_suggestion": "Sugestão de Tratamento",
    "suggestion_accepted":  "Sugestão Aceita",
    "clinic_approved":      "Clínica Aprovada",
    "clinic_suspended":     "Clínica Suspensa",
    "broadcast":            "Comunicado",
    "review":               "Avaliação",
    "review_report":        "Denúncia de Avaliação",
    "system":               "Sistema",
}


def _serialize(n: Notification) -> dict:
    return {
        "id": n.id,
        "title": n.title,
        "message": n.message,
        "type": n.type or "system",
        "type_label": TYPE_LABELS.get(n.type or "system", "Notificação"),
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


# ── Leitura ──────────────────────────────────────────────────────────────────

@router.get("/")
def list_notifications(
    only_unread: bool = Query(False, description="Retorna só as não lidas"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Lista notificações do usuário autenticado, mais recentes primeiro."""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]

    q = db.query(Notification).filter(
        Notification.user_id == str(user.id),
        Notification.user_type == user_type,
    )
    if only_unread:
        q = q.filter(Notification.is_read == False)

    items = q.order_by(Notification.created_at.desc()).offset(offset).limit(limit).all()
    return [_serialize(n) for n in items]


@router.get("/unread-count")
def get_unread_count(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Retorna o total de notificações não lidas. Usado para badges."""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]

    count = (
        db.query(Notification)
        .filter(
            Notification.user_id == str(user.id),
            Notification.user_type == user_type,
            Notification.is_read == False,
        )
        .count()
    )
    return {"count": count}


# ── Marcar como lida ─────────────────────────────────────────────────────────

@router.patch("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Marca todas as notificações do usuário como lidas."""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]

    updated = (
        db.query(Notification)
        .filter(
            Notification.user_id == str(user.id),
            Notification.user_type == user_type,
            Notification.is_read == False,
        )
        .update({"is_read": True})
    )
    db.commit()
    return {"message": "Todas marcadas como lidas", "updated": updated}


@router.patch("/{notification_id}/read")
def mark_one_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Marca uma notificação específica como lida."""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]

    n = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == str(user.id),
        Notification.user_type == user_type,
    ).first()

    if not n:
        raise HTTPException(status_code=404, detail="Notificação não encontrada")

    n.is_read = True
    db.commit()
    return {"message": "Marcada como lida"}


# ── Remoção ──────────────────────────────────────────────────────────────────

@router.delete("/clear-all")
def clear_all(
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Remove todas as notificações do usuário."""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]

    deleted = (
        db.query(Notification)
        .filter(
            Notification.user_id == str(user.id),
            Notification.user_type == user_type,
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"message": "Notificações removidas", "deleted": deleted}


@router.delete("/{notification_id}")
def delete_one(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Remove uma notificação específica."""
    user = current_user["user"]
    user_type = current_user["payload"]["type"]

    n = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == str(user.id),
        Notification.user_type == user_type,
    ).first()

    if not n:
        raise HTTPException(status_code=404, detail="Notificação não encontrada")

    db.delete(n)
    db.commit()
    return {"message": "Removida"}
