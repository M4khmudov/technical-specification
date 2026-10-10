"""Все модели импортируются здесь, чтобы Base.metadata видела полную схему (Alembic)."""

from app.models.audit import AuditLog
from app.models.catalog import Material, Recipe, RecipeItem, Supplier
from app.models.chat import ChatMessage, Conversation
from app.models.marking import MarkingAttempt, MarkingReport
from app.models.production import JobMaterial, ProductionJob, Reservation
from app.models.stock import Batch, BatchMovement, Receipt
from app.models.user import User

__all__ = [
    "AuditLog",
    "Batch",
    "BatchMovement",
    "ChatMessage",
    "Conversation",
    "JobMaterial",
    "MarkingAttempt",
    "MarkingReport",
    "Material",
    "ProductionJob",
    "Receipt",
    "Recipe",
    "RecipeItem",
    "Reservation",
    "Supplier",
    "User",
]
