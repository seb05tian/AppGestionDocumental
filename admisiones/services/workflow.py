from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from admisiones.models import (
    Applicant,
    Application,
    ApplicationRequirement,
    Call,
    Observation,
    RequirementDefinition,
    Review,
    StateHistory,
    User,
)
from .state import ReviewConflict, transition_application


class DuplicateApplication(ValidationError):
    def __init__(self, application):
        self.application = application
        super().__init__("Ya existe una solicitud para este programa y convocatoria.")


class ApplicantDataConflict(ValidationError):
    def __init__(self, applicant, message, field="identification_number"):
        self.applicant = applicant
        self.field = field
        super().__init__(message)


def inspect_staff_application(details, program, call):
    key = Applicant.normalized_identity(details["identification_type"], details["identification_number"])
    person = Applicant.objects.filter(identity_key=key).first() if key else None
    if person is None:
        collision = Applicant.objects.filter(email__iexact=details["email"]).first()
        if collision:
            raise ApplicantDataConflict(collision, "El correo ya está registrado con otra identificación.", field="email")
    else:
        fields = ("first_name", "second_name", "last_name", "second_last_name", "email", "phone")
        if any(getattr(person, field).casefold() != details[field].casefold() for field in fields):
            raise ApplicantDataConflict(person, "La identificación ya existe con otros datos. Consulta el registro existente.")
    existing = Application.objects.filter(applicant=person, program=program, call=call).first() if person else None
    return person, existing


@transaction.atomic
def create_staff_application(
    actor, details, program, call, *, origin=Application.Origin.STAFF, applicant=None, return_person_created=False,
):
    if not actor.can_manage_admissions or origin not in {Application.Origin.STAFF, Application.Origin.CSV}:
        raise PermissionDenied
    created = False
    if applicant is None:
        applicant, existing = inspect_staff_application(details, program, call)
        if existing:
            raise DuplicateApplication(existing)
        if applicant is None:
            try:
                with transaction.atomic():
                    applicant = Applicant.objects.create(**details)
                    created = True
            except IntegrityError:
                applicant, existing = inspect_staff_application(details, program, call)
                if existing:
                    raise DuplicateApplication(existing)
                if applicant is None:
                    raise
    application = _create_application(applicant, program, call, actor, origin)
    return (application, created) if return_person_created else application


@transaction.atomic
def create_application(actor, program, call):
    if actor.role != User.Role.APPLICANT:
        raise PermissionDenied("Solo un aspirante puede crear una solicitud.")
    try:
        applicant = actor.profile
    except Applicant.DoesNotExist as error:
        raise ValidationError("Completa tus datos personales antes de crear una solicitud.") from error
    return _create_application(applicant, program, call, actor, Application.Origin.PORTAL)


def requirements_for_call(call):
    call_definitions = RequirementDefinition.objects.filter(call=call)
    definitions = list(call_definitions.filter(active=True).order_by("position", "id"))
    if not call_definitions.exists():
        definitions = list(RequirementDefinition.objects.filter(call__isnull=True, active=True).order_by("position", "id"))
    if not definitions:
        raise ValidationError("La convocatoria todavía no tiene requisitos documentales configurados.")
    return definitions


def _create_application(applicant, program, call, actor, origin):
    program.refresh_from_db(fields=("active",))
    if not program.active or not Call.objects.accepting(timezone.localdate()).filter(pk=call.pk).exists():
        raise ValidationError("El programa o la convocatoria no están disponibles para nuevas solicitudes hoy.")
    definitions = requirements_for_call(call)
    try:
        with transaction.atomic():
            application = Application.objects.create(
                applicant=applicant, program=program, call=call, created_by=actor, origin=origin,
            )
            ApplicationRequirement.objects.bulk_create([
                ApplicationRequirement(
                    application=application,
                    source_definition=item,
                    code=item.code,
                    name=item.name,
                    required=item.required,
                    position=item.position,
                )
                for item in definitions
            ])
            StateHistory.objects.create(application=application, actor=actor, from_state="", to_state=Application.State.DRAFT)
    except IntegrityError as error:
        existing = Application.objects.filter(applicant=applicant, program=program, call=call).first()
        if existing:
            raise DuplicateApplication(existing) from error
        raise
    return application


@transaction.atomic
def assign_to_self(application, reviewer):
    if not reviewer.can_manage_admissions:
        raise PermissionDenied("Solo un revisor de admisiones puede asignarse expedientes.")
    locked = Application.objects.select_for_update().get(pk=application.pk)
    if locked.assigned_reviewer_id and not locked.can_review_as(reviewer):
        raise ReviewConflict("Este expediente ya está asignado a otro revisor.")
    if locked.state not in {Application.State.SUBMITTED, Application.State.IN_REVIEW}:
        raise ValidationError("Este expediente no está disponible para revisión.")
    if locked.assigned_reviewer_id is None:
        locked.assigned_reviewer = reviewer
        locked.save(update_fields=("assigned_reviewer", "updated_at"))
    if locked.state == Application.State.SUBMITTED:
        locked = transition_application(locked, Application.State.IN_REVIEW, reviewer)
    return locked


@transaction.atomic
def release_application(application, administrator):
    if administrator.role != User.Role.ADMINISTRATOR:
        raise PermissionDenied("Solo un administrador puede liberar un expediente.")
    locked = Application.objects.select_for_update().get(pk=application.pk)
    if locked.state not in {Application.State.SUBMITTED, Application.State.IN_REVIEW, Application.State.CORRECTION}:
        raise ValidationError("Solo se puede liberar un expediente enviado, en revisión o con correcciones.")
    if locked.assigned_reviewer_id is None:
        raise ValidationError("Este expediente no tiene revisor asignado.")
    previous = locked.assigned_reviewer
    locked.assigned_reviewer = None
    locked.updated_at = timezone.now()
    locked.save(update_fields=("assigned_reviewer", "updated_at"))
    StateHistory.objects.create(
        application=locked, actor=administrator, from_state=locked.state, to_state=locked.state,
        reason=f"Liberado por el administrador; estaba asignado a {previous}.",
    )
    return locked


@transaction.atomic
def review_document(slot, version_id, reviewer, decision, reason=""):
    if not reviewer.can_manage_admissions:
        raise PermissionDenied("Solo un revisor de admisiones puede revisar documentos.")
    application = Application.objects.select_for_update().get(pk=slot.application_id)
    locked_slot = ApplicationRequirement.objects.select_for_update().get(pk=slot.pk, application=application)
    if not application.can_review_as(reviewer):
        raise ReviewConflict("El expediente ya no está asignado a este revisor.")
    if application.state != Application.State.IN_REVIEW:
        raise ReviewConflict("El expediente cambió de estado y debe actualizarse antes de revisar.")
    if locked_slot.current_version_id != version_id:
        raise ReviewConflict("El documento fue reemplazado; recarga la revisión.")
    if decision not in Review.Decision.values:
        raise ValidationError("Selecciona una decisión válida.")
    reason = reason.strip()
    if decision == Review.Decision.CORRECTION and not reason:
        raise ValidationError({"reason": "Indica el motivo de la corrección solicitada."})
    version = locked_slot.current_version
    if Review.objects.filter(version=version, review_round=application.review_round).exists():
        raise ReviewConflict("Este documento ya tiene una decisión en la ronda actual.")
    review = Review.objects.create(
        version=version,
        reviewer=reviewer,
        decision=decision,
        reason=reason,
        review_round=application.review_round,
    )
    return review


@transaction.atomic
def send_corrections(application, reviewer):
    locked = Application.objects.select_for_update().get(pk=application.pk)
    if not reviewer.can_manage_admissions:
        raise PermissionDenied
    if locked.state != Application.State.IN_REVIEW or not locked.can_review_as(reviewer):
        raise ReviewConflict("El expediente ya no está disponible para enviar correcciones.")
    slots = list(locked.requirements.filter(current_version__isnull=False).select_related("current_version"))
    reviews = {
        review.version_id: review for review in Review.objects.filter(
            version_id__in=[slot.current_version_id for slot in slots], review_round=locked.review_round,
        )
    }
    if any(slot.current_version_id not in reviews for slot in slots):
        raise ValidationError("Revisa todos los documentos cargados antes de enviar las correcciones.")
    corrections = [
        (slot.name, reviews[slot.current_version_id].reason.strip())
        for slot in slots if reviews[slot.current_version_id].decision == Review.Decision.CORRECTION
    ]
    if not corrections or any(not reason for _, reason in corrections):
        raise ValidationError("Marca al menos un documento para corrección e indica el motivo de cada uno.")
    summary = "\n".join(f"{name}: {reason}" for name, reason in corrections)
    Observation.objects.create(
        application=locked, author=reviewer, audience=Observation.Audience.APPLICANT, text=summary,
    )
    return transition_application(locked, Application.State.CORRECTION, reviewer, reason=summary)
