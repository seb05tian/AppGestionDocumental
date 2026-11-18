from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .filter_forms import ApplicantFilters
from .forms import ApplicantDeleteForm, ApplicantDetailsForm, ApplicantEditForm, ApplicationForm, StaffApplicationForm
from .management_forms import build_management_form, complete_management_form, is_management_dialog, render_management_form
from .models import Applicant, Application
from .services.workflow import ApplicantDataConflict, DuplicateApplication, create_staff_application
from .views import admissions_required


@admissions_required
@require_GET
def applicant_list(request):
    filters = ApplicantFilters(request.GET, search_placeholder="Nombre, identificación o correo")
    filters.is_valid()
    query = filters.cleaned_data.get("q")
    people = Applicant.matching_text(query) if query else Applicant.objects.all()
    if account := filters.cleaned_data.get("account"):
        people = people.filter(user__isnull=(account == "unlinked"))
    return render(request, "admisiones/applicant_list.html", {
        "people": Paginator(people.order_by("last_name", "first_name", "pk"), 25).get_page(request.GET.get("page")),
        "filter_form": filters, "filter_query": filters.query_string(),
    })


@admissions_required
@never_cache
@require_http_methods(["GET", "POST"])
def applicant_new(request):
    form = build_management_form(request, StaffApplicationForm)
    return _handle_intake(request, form)


@admissions_required
@never_cache
@require_http_methods(["GET", "POST"])
def applicant_detail(request, public_id):
    person = get_object_or_404(Applicant, public_id=public_id)
    if request.method == "GET" and is_management_dialog(request):
        return render(request, "admisiones/_applicant_detail_dialog.html", {"person": person, "applications": _person_applications(person)})
    form = build_management_form(request, ApplicationForm)
    return _handle_intake(request, form, person)


@admissions_required
@never_cache
@require_http_methods(["GET", "POST"])
def applicant_edit(request, public_id):
    person = get_object_or_404(Applicant, public_id=public_id)
    form = build_management_form(request, ApplicantEditForm, person=person)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error(None, "Otra persona ya tiene estos datos. Revisa la identificación o el correo.")
        else:
            messages.success(request, "Datos del aspirante actualizados.")
            return complete_management_form(request, reverse("applicant_list"))
    return _render_person_form(
        request, form, person, title=f"Editar {person.get_full_name()}",
        fields_template="admisiones/_intake_fields.html", submit_label="Guardar cambios",
    )


@admissions_required
@never_cache
@require_http_methods(["GET", "POST"])
def applicant_delete(request, public_id):
    person = get_object_or_404(Applicant, public_id=public_id)
    form = build_management_form(request, ApplicantDeleteForm, person=person)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                person.delete()
        except ProtectedError:
            form.add_error(None, "Este aspirante tiene expedientes. Para conservar el historial no se puede eliminar.")
        else:
            messages.success(request, "Aspirante eliminado.")
            return complete_management_form(request, reverse("applicant_list"))
    return _render_person_form(
        request, form, person, title=f"Eliminar {person.get_full_name()}",
        fields_template="admisiones/_applicant_delete_fields.html", submit_label="Eliminar",
        submit_class="button-danger",
    )


def _person_applications(person):
    return person.applications.exclude(
        state=Application.State.DRAFT, origin=Application.Origin.PORTAL,
    ).select_related("program", "call").order_by("-created_at", "-pk")


def _render_person_form(request, form, person, *, title, fields_template, submit_label, submit_class="button-primary"):
    return render_management_form(request, "admisiones/management_form_page.html", {
        "form": form, "person": person, "form_title": title, "form_description": "",
        "fields_template": fields_template, "submit_label": submit_label, "submit_class": submit_class,
        "cancel_url": reverse("applicant_list"), "cancel_label": "Cancelar",
    })


def _handle_intake(request, form, person=None):
    context = {
        "form": form,
        "person": person,
        "form_title": "Crear otro expediente" if person else "Nuevo aspirante",
        "form_description": "",
        "fields_template": "admisiones/_intake_fields.html",
        "submit_label": "Crear expediente",
        "cancel_url": reverse("applicant_list"),
        "cancel_label": "Volver a aspirantes",
    }
    if person:
        context["applications"] = _person_applications(person)
    if request.method == "POST" and form.is_valid():
        details = {name: form.cleaned_data[name] for name in ApplicantDetailsForm.base_fields} if person is None else {}
        try:
            application = create_staff_application(
                request.user, details, form.cleaned_data["program"], form.cleaned_data["call"], applicant=person,
            )
        except ApplicantDataConflict as error:
            form.add_error(error.field, error)
            context["existing_person"] = error.applicant
        except DuplicateApplication as error:
            form.add_error(None, error)
            context["existing_application"] = error.application if error.application.visible_to_reviewer() else None
            context["existing_person"] = error.application.applicant
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Expediente creado. Puedes cargar documentos ahora o volver más tarde.")
            destination = reverse("reviewer_application", kwargs={"public_id": application.public_id})
            return complete_management_form(request, destination)
    return render_management_form(request, "admisiones/applicant_intake.html", context)
