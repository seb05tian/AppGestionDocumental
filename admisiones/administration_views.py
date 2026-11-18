import json

from django.contrib import messages
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError, Q
from django.core.serializers.json import DjangoJSONEncoder
from django.forms.models import model_to_dict
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .filter_forms import CatalogFilters
from .forms import CallSettingsForm, ProgramSettingsForm, RequirementSettingsForm
from .management_forms import build_management_form, complete_management_form, render_management_form
from .models import User
from .views import reviewer_inbox, role_required


administration_required = role_required(User.Role.ADMINISTRATOR)
CATALOG_FORMS = {
    "programas": ProgramSettingsForm,
    "convocatorias": CallSettingsForm,
    "requisitos": RequirementSettingsForm,
}


def _catalog_form(catalog):
    try:
        return CATALOG_FORMS[catalog]
    except KeyError as error:
        raise Http404 from error


def _record_version(record, fields):
    state = [record._meta.label_lower, record.pk, model_to_dict(record, fields=fields)]
    return salted_hmac("catalog-edit", json.dumps(state, cls=DjangoJSONEncoder, sort_keys=True)).hexdigest()


@administration_required
def administration_home(request):
    return reviewer_inbox(request)


@administration_required
@require_GET
def catalog_list(request, catalog):
    model = _catalog_form(catalog).Meta.model
    records = model.objects.all()
    filters = CatalogFilters(
        request.GET, search_placeholder="Nombre, código o convocatoria" if catalog == "requisitos" else "Nombre",
    )
    filters.is_valid()
    if catalog == "requisitos":
        records = records.select_related("call").order_by("call__name", "position", "pk")
    else:
        records = records.order_by(*model._meta.ordering, "pk")
    if query := filters.cleaned_data.get("q"):
        matches = Q(name__icontains=query)
        if catalog == "requisitos":
            matches |= Q(code__icontains=query) | Q(call__name__icontains=query)
        records = records.filter(matches)
    if status := filters.cleaned_data.get("status"):
        records = records.filter(active=(status == "active"))
    return render(request, "admisiones/catalog_list.html", {
        "catalog": catalog,
        "title": model._meta.verbose_name_plural.capitalize(),
        "singular": model._meta.verbose_name,
        "filter_form": filters,
        "filter_query": filters.query_string(),
        "records": Paginator(records, 25).get_page(request.GET.get("page")),
    })


@administration_required
@never_cache
@require_http_methods(["GET", "POST"])
@transaction.atomic
def catalog_edit(request, catalog, pk=None):
    form_class = _catalog_form(catalog)
    model = form_class.Meta.model
    submitted = request.method == "POST"
    records = model.objects.select_for_update() if submitted else model.objects.all()
    instance = get_object_or_404(records, pk=pk) if pk is not None else None
    form = build_management_form(request, form_class, instance=instance)
    catalog_url = reverse("administration_catalog", kwargs={"catalog": catalog})
    record_version = _record_version(instance, form_class.Meta.fields) if instance is not None else ""
    record_conflict = submitted and record_version and not constant_time_compare(
        request.POST.get("record_version", ""), record_version,
    )
    if record_conflict:
        form.add_error(None, "El registro cambió desde que lo abriste. Vuelve a abrir la edición para revisar los cambios.")
    if submitted and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error(None, "Ya existe un registro con esos datos. Revisa el catálogo antes de guardar.")
        else:
            messages.success(request, "Configuración guardada.")
            return complete_management_form(request, catalog_url)
    action = "Editar" if instance else "Crear"
    title = f"{action} {model._meta.verbose_name}"
    descriptions = {
        "convocatorias": "Las solicitudes solo se crean entre la apertura y el cierre. Deja una fecha vacía para no limitarla.",
    }
    return render_management_form(request, "admisiones/catalog_edit.html", {
        "catalog": catalog,
        "catalog_title": model._meta.verbose_name_plural.capitalize(),
        "title": title,
        "form": form,
        "form_title": title,
        "form_description": descriptions.get(catalog),
        "fields_template": "admisiones/_catalog_fields.html",
        "submit_label": "Guardar cambios",
        "cancel_url": catalog_url,
        "cancel_label": "Cancelar",
        "record_version": record_version,
        "record_conflict": record_conflict,
    })


@administration_required
@never_cache
@require_http_methods(["GET", "POST"])
def catalog_delete(request, catalog, pk):
    model = _catalog_form(catalog).Meta.model
    record = get_object_or_404(model, pk=pk)
    catalog_url = reverse("administration_catalog", kwargs={"catalog": catalog})
    if request.method == "POST":
        try:
            with transaction.atomic():
                record.delete()
        except ProtectedError:
            messages.error(request, f"No se puede eliminar «{record}» porque tiene expedientes asociados. Desactívalo en su lugar.")
        else:
            messages.success(request, "Registro eliminado.")
        return redirect(catalog_url)
    return render(request, "admisiones/catalog_delete.html", {
        "catalog": catalog,
        "title": model._meta.verbose_name_plural.capitalize(),
        "singular": model._meta.verbose_name,
        "record": record,
        "catalog_url": catalog_url,
    })
