from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from admisiones.models import Application, Notification, StateHistory
from .notifications import create_notification


class ReviewConflict(Exception):
    """A state change raced with another reviewer action."""


ALLOWED_TRANSITIONS = {
    Application.State.DRAFT: {Application.State.SUBMITTED},
    Application.State.SUBMITTED: {Application.State.IN_REVIEW},
    Application.State.IN_REVIEW: {Application.State.CORRECTION, Application.State.APPROVED},
    Application.State.CORRECTION: {Application.State.SUBMITTED},
    Application.State.APPROVED: {Application.State.IN_REVIEW, Application.State.CLOSED},
    Application.State.CLOSED: {Application.State.IN_REVIEW},
}


def _validate_complete(application, for_resubmission=False):
    if not application.requirements.exists():
        raise ValidationError("El expediente no tiene requisitos documentales configurados.")
    missing = list(application.requirements.filter(required=True, current_version__isnull=True).values_list("name", flat=True))
    if missing:
        raise ValidationError("Faltan documentos obligatorios: " + ", ".join(missing))
    if for_resubmission:
        observed = [
            item.name for item in application.requirements.filter(current_version__isnull=False).select_related("current_version")
            if item.document_state() == "correction"
        ]
        if observed:
            raise ValidationError("Reemplaza los documentos con corrección pendiente: " + ", ".join(observed))


@transaction.atomic
def transition_application(application, target, actor, reason=""):
    target = str(target)
    locked = Application.objects.select_for_update().get(pk=application.pk)
    if application.state != locked.state:
        raise ReviewConflict("El expediente cambió mientras se procesaba la acción.")
    previous = locked.state
    if target not in ALLOWED_TRANSITIONS.get(previous, set()):
        raise ValidationError("La transición solicitada no está permitida.")
    reason = reason.strip()
    can_review = actor and actor.can_manage_admissions
    if target == Application.State.SUBMITTED:
        if not locked.can_edit_documents(actor):
            raise PermissionDenied("No puedes enviar este expediente.")
        _validate_complete(locked, for_resubmission=previous == Application.State.CORRECTION)
        locked.submitted_at = timezone.now()
    elif target == Application.State.IN_REVIEW:
        if not can_review:
            raise PermissionDenied("Solo un revisor de admisiones puede abrir una revisión.")
        if previous in {Application.State.APPROVED, Application.State.CLOSED}:
            if not reason:
                raise ValidationError("Indica el motivo para reabrir el expediente.")
            if locked.assigned_reviewer_id not in (None, actor.pk):
                raise ReviewConflict("Este expediente está asignado a otro revisor.")
            locked.assigned_reviewer = actor
            locked.review_round += 1
        elif locked.assigned_reviewer_id != actor.pk:
            raise ReviewConflict("Asígnate el expediente antes de iniciar la revisión.")
    elif target in {Application.State.CORRECTION, Application.State.APPROVED, Application.State.CLOSED}:
        if not can_review:
            raise PermissionDenied("Solo un revisor de admisiones puede guardar esta decisión.")
        if locked.assigned_reviewer_id != actor.pk:
            raise ReviewConflict("El expediente ya no está asignado a este revisor.")
        if target == Application.State.CORRECTION and not reason:
            raise ValidationError("Indica qué debe corregir el aspirante.")
        if target in {Application.State.APPROVED, Application.State.CLOSED}:
            _validate_complete(locked)
            if locked.requirements.filter(
                current_version__reviews__decision="correction",
                current_version__reviews__review_round=locked.review_round,
            ).exists():
                raise ValidationError("Envía primero las correcciones registradas.")
            pending = [
                item.name for item in locked.requirements.filter(current_version__isnull=False).select_related("current_version")
                if item.document_state() != "approved"
            ]
            if pending:
                raise ValidationError("Aún hay documentos pendientes de revisión: " + ", ".join(pending))
    locked.state = target
    locked.updated_at = timezone.now()
    locked.save(update_fields=("state", "updated_at", "submitted_at", "assigned_reviewer", "review_round"))
    StateHistory.objects.create(application=locked, actor=actor, from_state=previous, to_state=target, reason=reason)
    if target == Application.State.SUBMITTED:
        create_notification(locked.applicant, locked, Notification.Kind.RECEIVED, "Expediente recibido", "Tu expediente se recibió y quedó en la bandeja de admisiones.")
    elif target == Application.State.CORRECTION:
        create_notification(locked.applicant, locked, Notification.Kind.CORRECTION, "Correcciones solicitadas", reason)
    elif target == Application.State.APPROVED:
        create_notification(locked.applicant, locked, Notification.Kind.APPROVED, "Documentación aprobada", "La documentación obligatoria fue revisada y aprobada.")
    elif target == Application.State.IN_REVIEW and previous in {Application.State.APPROVED, Application.State.CLOSED}:
        create_notification(locked.applicant, locked, Notification.Kind.REOPENED, "Expediente reabierto", "Admisiones reabrió la revisión de tu expediente.")
    return locked
