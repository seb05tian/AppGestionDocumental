import csv
import io
import secrets
from collections import Counter

from django import forms
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from django.contrib.sessions.models import Session
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .forms import ApplicantDetailsForm, ApplicationForm
from .management_forms import is_management_dialog
from .models import Applicant, Application
from .services.workflow import (
    ApplicantDataConflict,
    DuplicateApplication,
    create_staff_application,
    inspect_staff_application,
    requirements_for_call,
)
from .views import admissions_required


CSV_FIELDS = {
    "primer_nombre": "first_name",
    "segundo_nombre": "second_name",
    "primer_apellido": "last_name",
    "segundo_apellido": "second_last_name",
    "tipo_identificacion": "identification_type",
    "numero_identificacion": "identification_number",
    "correo": "email",
    "telefono": "phone",
}
MAX_BYTES = 256 * 1024
MAX_ROWS = 500
PREVIEW_SECONDS = 15 * 60
SESSION_KEY = "applicant_import"


class ApplicantImportForm(ApplicationForm):
    file = forms.FileField(
        label="Archivo CSV",
        help_text=(
            "Excel (.xlsx) o CSV UTF-8, de hasta 500 filas y 256 KB. Identificación y teléfono se guardan como texto. "
            f"Tipos de identificación: {', '.join(Applicant.IdentificationType.values)}."
        ),
        widget=forms.FileInput(attrs={"accept": ".xlsx,.csv,text/csv"}),
    )

    def clean_call(self):
        call = self.cleaned_data["call"]
        requirements_for_call(call)
        return call

    def clean_file(self):
        upload = self.cleaned_data["file"]
        name = upload.name.lower()
        if not name.endswith((".csv", ".xlsx")):
            raise ValidationError("Selecciona un archivo Excel (.xlsx) o CSV.")
        content = upload.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValidationError("El archivo supera 256 KB. Divídelo en lotes más pequeños.")
        if name.endswith(".xlsx"):
            return _xlsx_as_csv(content)
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValidationError("Guarda el archivo como CSV UTF-8 e inténtalo de nuevo.") from error


def _read_csv(content):
    first_line = content.partition("\n")[0]
    delimiter = ";" if ";" in first_line else ","
    reader = csv.reader(io.StringIO(content, newline=""), delimiter=delimiter, strict=True)
    try:
        headers = [header.strip() for header in next(reader, [])]
        if len(headers) != len(CSV_FIELDS) or set(headers) != set(CSV_FIELDS):
            columns = ", ".join(CSV_FIELDS)
            raise ValidationError(f"Usa estas {len(CSV_FIELDS)} columnas del archivo de plantilla: {columns}.")
        rows = []
        while True:
            number = reader.line_num + 1
            values = next(reader, None)
            if values is None:
                break
            if not values or (len(values) == 1 and not values[0].strip()):
                continue
            if len(rows) == MAX_ROWS:
                raise ValidationError("El archivo supera 500 filas de datos. Divídelo en lotes más pequeños.")
            data = {CSV_FIELDS[name]: value for name, value in zip(headers, values)}
            row = {"number": number, "data": data}
            if len(values) != len(headers):
                row.update(status="error", message=f"La fila debe tener {len(CSV_FIELDS)} columnas.")
            rows.append(row)
    except csv.Error as error:
        raise ValidationError(f"Revisa la estructura del CSV cerca de la línea {reader.line_num}.") from error
    if not rows:
        raise ValidationError("El archivo tiene encabezados, pero ninguna fila de aspirante.")
    return rows


def _describe_row(row, program, call, *, actor, approved_numbers):
    details = row["data"]
    try:
        person, application = inspect_staff_application(details, program, call)
        if application:
            return dict(status="existing", person=person, application=application, message="El expediente ya existe.")
        if actor is None:
            message = "Se creará un expediente para el aspirante existente." if person else "Se crearán el aspirante y su expediente."
            return dict(status="valid", person=person, message=message)
        if row["number"] not in approved_numbers:
            return dict(status="error", message="La fila cambió desde la vista previa. Vuelve a importar para revisarla.")
        application, person_created = create_staff_application(
            actor, details, program, call, origin=Application.Origin.CSV, return_person_created=True,
        )
        return dict(status="created", person=application.applicant, application=application, person_created=person_created)
    except DuplicateApplication as error:
        application = error.application
        return dict(status="existing", person=application.applicant, application=application, message="El expediente ya existe.")
    except ApplicantDataConflict as error:
        return dict(status="error", person=error.applicant, message=" ".join(error.messages))
    except ValidationError as error:
        return dict(status="error", message=" ".join(error.messages))


def _evaluate_rows(rows, program, call, *, actor=None, approved_numbers=()):
    by_identity, by_email = {}, {}
    for row in rows:
        if row.get("status") == "error":
            continue
        raw = row["data"]
        details_form = ApplicantDetailsForm({**raw, "identification_type": raw["identification_type"].strip().upper()})
        if not details_form.is_valid():
            errors = [f"{details_form[field].label}: {' '.join(messages)}" for field, messages in details_form.errors.items() if field != "__all__"]
            errors.extend(details_form.non_field_errors())
            row.update(status="error", message=" ".join(errors))
            continue
        details = row["data"] = {name: details_form.cleaned_data[name] for name in CSV_FIELDS.values()}
        identity = Applicant.normalized_identity(details["identification_type"], details["identification_number"])
        email = details["email"].casefold()
        previous_identity = by_identity.get(identity)
        if previous_identity:
            other_data = {key: value.casefold() for key, value in details.items() if key != "identification_number"}
            previous_data = {key: value.casefold() for key, value in previous_identity["data"].items() if key != "identification_number"}
            if other_data == previous_data:
                row.update(status="error", message=f"Duplicada en el archivo. Se usa la fila {previous_identity['number']}.")
            else:
                row.update(status="error", message=f"La identificación coincide con la fila {previous_identity['number']}, pero sus datos difieren.")
        elif email in by_email:
            row.update(status="error", message=f"El correo ya se usa para otra identificación en la fila {by_email[email]}.")
        else:
            row.update(_describe_row(row, program, call, actor=actor, approved_numbers=approved_numbers))
            if row["status"] != "error":
                by_identity[identity] = row
                by_email[email] = row["number"]
    return rows


@transaction.atomic
def _consume_preview(request, nonce):
    session = Session.objects.select_for_update().filter(
        session_key=request.session.session_key, expire_date__gt=timezone.now(),
    ).first()
    if session is None:
        return None
    data = session.get_decoded()
    preview = data.get(SESSION_KEY)
    if not preview or preview["actor_id"] != request.user.pk or not constant_time_compare(preview["nonce"], nonce):
        return None
    # Consume under the session row lock so simultaneous confirmations cannot reuse it.
    data.pop(SESSION_KEY)
    session.session_data = request.session.encode(data)
    session.save(update_fields=("session_data",))
    return preview if preview["expires_at"] > timezone.now().timestamp() else None


def _render(request, **context):
    status = context.pop("status", 200)
    rows = context.get("rows", [])
    in_dialog = is_management_dialog(request)
    counts = Counter(row["status"] for row in rows)
    people = set()
    created_people = set()
    for row in rows:
        if row["status"] == "error":
            continue
        if person := row.get("person"):
            people.add(person.pk)
            if row.get("person_created"):
                created_people.add(person.pk)
    context = {
        **context,
        "as_dialog": in_dialog,
        "counts": {
            "valid": counts["valid"], "created": counts["created"], "existing": counts["existing"],
            "errors": counts["error"], "people_created": len(created_people), "people_existing": len(people - created_people),
        },
    }
    if in_dialog:
        return render(request, "admisiones/_applicant_import_content.html", context, status=422 if status == 400 else status)
    return render(request, "admisiones/applicant_import.html", context, status=status)


@admissions_required
@never_cache
@require_http_methods(["GET", "POST"])
def import_applicants(request):
    if request.method == "GET":
        return _render(request, form=ApplicantImportForm())
    if request.POST.get("action") == "confirm":
        preview = _consume_preview(request, request.POST.get("nonce", ""))
        if preview is not None:
            selection = ApplicationForm({"program": preview["program"], "call": preview["call"]})
            if selection.is_valid():
                program, call = selection.cleaned_data["program"], selection.cleaned_data["call"]
                rows = _evaluate_rows(
                    _read_csv(preview["content"]), program, call, actor=request.user,
                    approved_numbers=set(preview["approved_numbers"]),
                )
                return _render(request, rows=rows, program=program, call=call, completed=True)
            message = "El programa o la convocatoria ya no está disponible. Selecciona otra opción."
        else:
            message = "La vista previa ya no está disponible. Carga el archivo para revisarlo otra vez."
        return _render(request, form=ApplicantImportForm(), error=message, status=400)
    form = ApplicantImportForm(request.POST, request.FILES)
    if not form.is_valid():
        return _render(request, form=form, status=400)
    program, call, content = (form.cleaned_data[key] for key in ("program", "call", "file"))
    try:
        rows = _evaluate_rows(_read_csv(content), program, call)
    except ValidationError as error:
        form.add_error("file", error)
        return _render(request, form=form, status=400)
    nonce = secrets.token_urlsafe(24)
    request.session[SESSION_KEY] = {
        "nonce": nonce, "actor_id": request.user.pk, "expires_at": timezone.now().timestamp() + PREVIEW_SECONDS,
        "program": program.pk, "call": call.pk, "content": content,
        "approved_numbers": [row["number"] for row in rows if row["status"] == "valid"],
    }
    return _render(request, rows=rows, program=program, call=call, nonce=nonce)


def _cell_text(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _xlsx_as_csv(content):
    """Reads the first sheet of an Excel file as CSV text, so both formats share one parser."""
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as error:  # openpyxl raises several error types for damaged files
        raise ValidationError("No pudimos leer el archivo Excel. Guárdalo de nuevo como .xlsx.") from error
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    try:
        written = 0
        for row in workbook.worksheets[0].iter_rows(values_only=True):
            cells = ["" if value is None else _cell_text(value) for value in row]
            if not any(cells):
                continue  # formatted but empty cells are not rows
            written += 1
            if written > MAX_ROWS + 1:  # the header plus the data rows
                raise ValidationError("El archivo supera 500 filas de datos. Divídelo en lotes más pequeños.")
            writer.writerow(cells)
    finally:
        workbook.close()
    return buffer.getvalue()


def _template_workbook():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Aspirantes"
    sheet.append(list(CSV_FIELDS))
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="000000")
        cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 22
    sheet.freeze_panes = "A2"
    for column, width in zip("ABCDEFGH", (18, 18, 18, 18, 22, 22, 32, 16)):
        sheet.column_dimensions[column].width = width
    # Identification and phone stay text, so leading zeros survive.
    text_columns = [list(CSV_FIELDS).index(name) + 1 for name in ("numero_identificacion", "telefono")]
    for row in range(2, MAX_ROWS + 2):
        for column in text_columns:
            sheet.cell(row=row, column=column).number_format = "@"

    notes = workbook.create_sheet("Instrucciones")
    notes.append(["Columna", "Qué escribir"])
    for cell in notes[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="000000")
    for row in (
        ("primer_nombre", "Primer nombre (obligatorio)"),
        ("segundo_nombre", "Segundo nombre (opcional)"),
        ("primer_apellido", "Primer apellido (obligatorio)"),
        ("segundo_apellido", "Segundo apellido (opcional)"),
        ("tipo_identificacion", "CC, TI, CE o PASSPORT"),
        ("numero_identificacion", "Número sin puntos ni espacios"),
        ("correo", "Correo válido; no puede pertenecer a otra persona"),
        ("telefono", "Teléfono de contacto"),
    ):
        notes.append(list(row))
    notes.column_dimensions["A"].width = 24
    notes.column_dimensions["B"].width = 56
    return workbook


@admissions_required
@require_GET
def applicant_template(request):
    buffer = io.BytesIO()
    _template_workbook().save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="plantilla-aspirantes.xlsx"'
    return response
