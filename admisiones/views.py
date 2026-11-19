from functools import wraps
from pathlib import Path
import csv
import tempfile
import zipfile

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from django.utils import timezone

from .forms import ApplicationForm, ReopenForm, ReviewDecisionForm, UploadForm
from .models import (
    Application,
    ApplicationRequirement,
    DocumentVersion,
    applicant_file_key,
    Notification,
    Observation,
    PrevalidationResult,
    Review,
    User,
)
from .services.files import store_version
from .services.metrics import compute_indicators
from .services.notifications import notifications_for
from .services.queue import reviewer_queue
from .services.state import ReviewConflict, transition_application
from .services.workflow import assign_to_self, create_application, release_application, review_document, send_corrections


def role_required(*roles):
    def decorate(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.user.role not in roles:
                return HttpResponseForbidden()
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


admissions_required = role_required(*User.ADMISSIONS_ROLES)


_DOCUMENT_STATES = {
    "pending_upload": "Pendiente de carga",
    "pending_review": "Pendiente de revisión",
    "correction": "Requiere corrección",
    "approved": "Aprobado",
}


def _prepare_requirements(application):
    requirements = list(application.requirements.select_related("current_version__prevalidation").all())
    for slot in requirements:
        slot.document_status = slot.document_state()
        slot.document_status_label = _DOCUMENT_STATES[slot.document_status]
        slot.previous_versions = slot.versions.exclude(pk=slot.current_version_id).order_by("-version_number")
        slot.current_review = None
        slot.automatic_result = None
        if slot.current_version_id:
            slot.current_review = slot.current_version.reviews.filter(review_round=application.review_round).first()
            try:
                slot.automatic_result = slot.current_version.prevalidation
            except PrevalidationResult.DoesNotExist:
                pass
    return requirements


def _application_context(application, audience):
    requirements = _prepare_requirements(application)
    if audience == Observation.Audience.APPLICANT and application.state == Application.State.IN_REVIEW:
        for slot in requirements:
            if slot.document_status == "correction":
                slot.document_status = "pending_review"
                slot.document_status_label = _DOCUMENT_STATES["pending_review"]
                slot.current_review = None
    visible_observations = application.observations.filter(audience=audience)
    required = [slot for slot in requirements if slot.required]
    corrections = [slot for slot in requirements if slot.document_status == "correction"]
    can_submit = (
        application.state in {Application.State.DRAFT, Application.State.CORRECTION}
        and bool(requirements)
        and all(slot.current_version_id for slot in required)
        and not corrections
    )
    return {
        "application": application,
        "requirements": requirements,
        "observations": visible_observations,
        "history": application.history.select_related("actor"),
        "uploaded_count": sum(bool(slot.current_version_id) for slot in required),
        "required_count": len(required),
        "approved_count": sum(slot.document_status == "approved" for slot in required),
        "can_submit": can_submit,
        "can_approve": bool(requirements) and all(
            slot.document_status == "approved" for slot in requirements if slot.required or slot.current_version_id
        ),
        "correction_count": len(corrections),
        "can_send_corrections": bool(corrections) and all(
            slot.current_review for slot in requirements if slot.current_version_id
        ),
        "max_upload_mb": settings.MAX_UPLOAD_MB,
    }


@role_required(User.Role.APPLICANT)
@require_http_methods(["GET", "POST"])
def application_new(request):
    form = ApplicationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            application = create_application(request.user, form.cleaned_data["program"], form.cleaned_data["call"])
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Solicitud creada. Carga los documentos requeridos.")
            return redirect("applicant_application", public_id=application.public_id)
    return render(request, "admisiones/application_new.html", {"form": form})


@role_required(User.Role.APPLICANT)
@require_GET
def applicant_application(request, public_id):
    application = get_object_or_404(Application.for_applicant(request.user), public_id=public_id)
    return render(request, "admisiones/applicant_application.html", _application_context(application, Observation.Audience.APPLICANT))


@role_required(User.Role.APPLICANT)
@require_POST
def applicant_upload(request, public_id, slot_id):
    application = get_object_or_404(Application.for_applicant(request.user), public_id=public_id)
    slot = get_object_or_404(ApplicationRequirement, pk=slot_id, application=application)
    form = UploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Selecciona un archivo PDF, JPG o PNG.")
    else:
        try:
            version = store_version(slot, form.cleaned_data["file"], request.user)
        except (ValidationError, PermissionDenied) as error:
            for message in getattr(error, "messages", [str(error)]):
                messages.error(request, message)
        else:
            messages.success(request, f"Se cargó la versión {version.version_number} de {slot.name}.")
    return redirect("applicant_application", public_id=application.public_id)


@role_required(User.Role.APPLICANT)
@require_POST
def applicant_submit(request, public_id):
    application = get_object_or_404(Application.for_applicant(request.user), public_id=public_id)
    try:
        transition_application(application, Application.State.SUBMITTED, request.user)
    except (ValidationError, PermissionDenied, ReviewConflict) as error:
        messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
    else:
        messages.success(request, "El expediente se envió a admisiones.")
    return redirect("applicant_application", public_id=application.public_id)


def _can_see_version(user, version):
    application = version.requirement.application
    if user.role == User.Role.APPLICANT:
        return application.applicant.user_id == user.pk and (
            application.origin == Application.Origin.PORTAL or application.applicant.portal_verified_at is not None
        )
    return user.can_manage_admissions and application.visible_to_reviewer(user)


def _private_path(version):
    root = Path(settings.PRIVATE_MEDIA_ROOT).resolve()
    path = (root / version.storage_key).resolve()
    if root not in path.parents or not path.is_file():
        raise Http404
    return path


@login_required
@require_GET
def document_file(request, version_id):
    version = get_object_or_404(DocumentVersion.objects.select_related("requirement__application__applicant"), pk=version_id)
    if not _can_see_version(request.user, version):
        raise Http404
    path = _private_path(version)
    try:
        response = FileResponse(
            path.open("rb"),
            as_attachment=request.GET.get("download") == "1",
            filename=version.standard_name,
            content_type=version.content_type,
        )
    except (OSError, RuntimeError) as error:
        raise Http404 from error
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    if version.content_type == "application/pdf" and request.GET.get("download") != "1":
        response["Content-Security-Policy"] = "sandbox; default-src 'none'; frame-ancestors 'self'"
    return response


@admissions_required
@require_GET
def application_zip(request, public_id):
    application = _review_application(request.user, public_id)
    slots = application.requirements.filter(current_version__isnull=False).select_related("current_version__requirement__application__applicant")
    versions = [slot.current_version for slot in slots]
    if not versions:
        messages.error(request, "Este expediente todavía no tiene documentos para descargar.")
        return redirect("reviewer_application", public_id=public_id)
    buffer = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    # PDF and images are already compressed, so storing them is faster and the same size.
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for version in versions:
            archive.write(_private_path(version), arcname=version.standard_name)
    buffer.seek(0)
    response = FileResponse(
        buffer,
        as_attachment=True,
        filename=f"expediente-{applicant_file_key(application.applicant)}.zip",
        content_type="application/zip",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


def _review_application(actor, public_id):
    application = get_object_or_404(Application.objects.select_related("applicant", "created_by", "assigned_reviewer", "program", "call"), public_id=public_id)
    if not application.visible_to_reviewer(actor):
        raise Http404
    return application


@admissions_required
@require_GET
def reviewer_inbox(request):
    applications, filters, page = reviewer_queue(request)
    return render(request, "admisiones/reviewer_inbox.html", {
        "applications": applications, "filter_form": filters, "filter_query": filters.query_string(),
        "page_obj": page, "total_count": page.paginator.count,
    })


def _csv_cell(value):
    value = "" if value is None else str(value)
    if value.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@", "\t", "\r", "\n"}:
        return "'" + value
    return value


@admissions_required
@require_GET
def reviewer_export(request):
    applications, _, _ = reviewer_queue(request, paginate=False)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="expedientes-admisiones.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(("Aspirante", "Identificación", "Correo", "Programa", "Convocatoria", "Estado", "Documentos aprobados", "Requisitos", "Alertas", "Enviado", "Revisor asignado"))
    for application in applications:
        writer.writerow(_csv_cell(value) for value in (
            application.applicant.get_full_name(),
            application.applicant.identification_number,
            application.applicant.email,
            application.program.name,
            application.call.name,
            application.get_state_display(),
            application.approved_count,
            application.required_count,
            application.alert_count,
            application.submitted_at.isoformat() if application.submitted_at else "",
            str(application.assigned_reviewer) if application.assigned_reviewer else "",
        ))
    return response


@admissions_required
@require_GET
def reviewer_indicators(request):
    return render(request, "admisiones/reviewer_indicators.html", compute_indicators())


@login_required
@require_GET
def notification_list(request):
    return render(request, "admisiones/notifications.html", {
        "notifications": notifications_for(request.user).select_related("application")
    })


@login_required
@require_POST
def notification_read(request, notification_id):
    notification = get_object_or_404(notifications_for(request.user), pk=notification_id)
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=("read_at",))
    return redirect("notification_list")


@admissions_required
@require_GET
@never_cache
def reviewer_application(request, public_id):
    application = _review_application(request.user, public_id)
    if request.GET.get("analisis") == "1":
        slots = list(application.requirements.filter(current_version__isnull=False).select_related("current_version__prevalidation"))
        _attach_ai_summaries(slots, application.applicant)
        return render(request, "admisiones/_reviewer_ai_results.html", {"requirements": slots})
    return _render_reviewer_application(request, application)


def _attach_ai_summaries(slots, applicant):
    from admisiones.models import ProcessingJob
    from admisiones.services.ai_verifier import reviewer_summary

    version_ids = [slot.current_version_id for slot in slots if slot.current_version_id]
    jobs = {job.version_id: job for job in ProcessingJob.objects.filter(
        version_id__in=version_ids, kind=ProcessingJob.Kind.AI,
    )}
    for slot in slots:
        if slot.current_version_id:
            result = getattr(slot.current_version, "prevalidation", None)
            slot.ai_summary = reviewer_summary(
                jobs.get(slot.current_version_id), result.ai_result if result else None, applicant,
            )


def _render_reviewer_application(request, application, *, review_form=None, upload_form=None, slot_id=None, status=200):
    context = _application_context(application, Observation.Audience.INTERNAL)
    context["applicant_observations"] = application.observations.filter(audience=Observation.Audience.APPLICANT)
    context["can_manage_documents"] = application.can_edit_documents(request.user)
    context["can_review"] = application.state == Application.State.IN_REVIEW and application.can_review_as(request.user)
    unassigned = application.assigned_reviewer_id is None
    context["can_claim"] = (application.state == Application.State.IN_REVIEW and unassigned) or (
        application.state == Application.State.SUBMITTED and (unassigned or application.can_review_as(request.user))
    )
    context["can_release"] = request.user.role == User.Role.ADMINISTRATOR and not unassigned and application.state in {
        Application.State.SUBMITTED, Application.State.IN_REVIEW, Application.State.CORRECTION,
    }
    _attach_ai_summaries(context["requirements"], application.applicant)
    for slot in context["requirements"]:
        slot.can_upload = slot.can_upload_by(request.user, document_state=slot.document_status)
        if slot.pk == slot_id:
            slot.bound_review = review_form
            slot.bound_upload = upload_form
    return render(request, "admisiones/reviewer_application.html", context, status=status)


@admissions_required
@require_POST
def reviewer_upload(request, public_id, slot_id):
    application = _review_application(request.user, public_id)
    slot = get_object_or_404(ApplicationRequirement, pk=slot_id, application=application)
    if not application.can_edit_documents(request.user) and not (
        request.user.role == User.Role.ADMINISTRATOR and slot.current_version_id
    ):
        raise PermissionDenied
    form = UploadForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            version = store_version(slot, form.cleaned_data["file"], request.user)
        except ValidationError as error:
            form.add_error("file", error)
        else:
            messages.success(request, f"Se cargó la versión {version.version_number} de {slot.name}.")
            detail = reverse("reviewer_application", kwargs={"public_id": public_id})
            return redirect(f"{detail}#requirement-{slot.pk}")
    return _render_reviewer_application(request, application, upload_form=form, slot_id=slot.pk, status=400)


@admissions_required
@require_POST
def reviewer_submit(request, public_id):
    application = _review_application(request.user, public_id)
    try:
        transition_application(application, Application.State.SUBMITTED, request.user)
    except (ValidationError, ReviewConflict) as error:
        messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
    else:
        messages.success(request, "El expediente se envió a revisión.")
    return redirect("reviewer_application", public_id=public_id)


def _run_action(request, public_id, action, success):
    """Runs one reviewer action. Returns the redirect back to the file, or a 409 page when another action won the race."""
    try:
        action()
    except ReviewConflict as error:
        return render(request, "admisiones/conflict.html", {"message": str(error)}, status=409)
    except (PermissionDenied, ValidationError) as error:
        messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
    else:
        messages.success(request, success)
    return redirect("reviewer_application", public_id=public_id)


@admissions_required
@require_POST
def reviewer_assign(request, public_id):
    application = _review_application(request.user, public_id)
    message = "La revisión del expediente está abierta." if request.user.role == User.Role.ADMINISTRATOR else "El expediente quedó asignado a ti."
    return _run_action(request, public_id, lambda: assign_to_self(application, request.user), message)


@role_required(User.Role.ADMINISTRATOR)
@require_POST
def reviewer_release(request, public_id):
    application = _review_application(request.user, public_id)
    return _run_action(request, public_id, lambda: release_application(application, request.user), "El expediente quedó sin revisor asignado.")


@admissions_required
@require_POST
def reviewer_submit(request, public_id):
    application = _review_application(request.user, public_id)
    return _run_action(
        request, public_id, lambda: transition_application(application, Application.State.SUBMITTED, request.user),
        "El expediente se envió a revisión.",
    )


@admissions_required
@require_POST
def review_document_view(request, public_id, slot_id):
    application = _review_application(request.user, public_id)
    slot = get_object_or_404(ApplicationRequirement, pk=slot_id, application=application)
    form = ReviewDecisionForm(request.POST)
    if not form.is_valid():
        return _render_reviewer_application(request, application, review_form=form, slot_id=slot.pk, status=400)
    return _run_action(
        request, public_id,
        lambda: review_document(
            slot, form.cleaned_data["version_id"], request.user, form.cleaned_data["decision"],
            reason=form.cleaned_data["reason"],
        ),
        "La revisión del documento quedó guardada.",
    )


@admissions_required
@require_POST
def reviewer_send_corrections(request, public_id):
    application = _review_application(request.user, public_id)
    return _run_action(
        request, public_id, lambda: send_corrections(application, request.user),
        "Se envió una solicitud con todas las correcciones.",
    )


@admissions_required
@require_POST
def reviewer_approve(request, public_id):
    application = _review_application(request.user, public_id)
    return _run_action(
        request, public_id, lambda: transition_application(application, Application.State.APPROVED, request.user),
        "La documentación obligatoria quedó aprobada.",
    )


@admissions_required
@require_POST
def reviewer_close(request, public_id):
    application = _review_application(request.user, public_id)
    return _run_action(
        request, public_id, lambda: transition_application(application, Application.State.CLOSED, request.user),
        "El expediente quedó cerrado.",
    )


@admissions_required
@require_POST
def reviewer_reopen(request, public_id):
    application = get_object_or_404(Application, public_id=public_id, state__in=(Application.State.APPROVED, Application.State.CLOSED))
    form = ReopenForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Indica el motivo para reabrir el expediente.")
        return redirect("reviewer_application", public_id=public_id)
    return _run_action(
        request, public_id,
        lambda: transition_application(application, Application.State.IN_REVIEW, request.user, reason=form.cleaned_data["reason"]),
        "El expediente se reabrió y quedó en revisión.",
    )
