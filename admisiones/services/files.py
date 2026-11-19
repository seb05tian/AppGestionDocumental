import hashlib
import io
import re
import uuid
import warnings
from datetime import timedelta
from pathlib import PurePosixPath

import pymupdf
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from admisiones.models import (
    Application,
    ApplicationRequirement,
    DocumentVersion,
    PrevalidationResult,
    ProcessingJob,
    User,
)


def _validated_content(upload):
    maximum = settings.MAX_UPLOAD_MB * 1024 * 1024
    if upload.size > maximum:
        raise ValidationError(f"El archivo supera el límite de {settings.MAX_UPLOAD_MB} MB.")
    data = upload.read(maximum + 1)
    upload.seek(0)
    if len(data) > maximum:
        raise ValidationError(f"El archivo supera el límite de {settings.MAX_UPLOAD_MB} MB.")
    name = PurePosixPath(str(upload.name).replace("\\", "/")).name
    extension = PurePosixPath(name.lower()).suffix
    try:
        if extension == ".pdf":
            if b"%PDF-" not in data[:1024]:
                raise ValidationError("El archivo no contiene un encabezado PDF válido.")
            with pymupdf.open(stream=data, filetype="pdf") as document:
                if document.needs_pass or document.is_encrypted:
                    raise ValidationError("El PDF está protegido y no se puede procesar.")
                if document.page_count < 1:
                    raise ValidationError("El PDF no contiene páginas.")
                if document.page_count > settings.OCR_MAX_PAGES:
                    raise ValidationError(f"El PDF supera el límite de {settings.OCR_MAX_PAGES} páginas.")
            content_type, suffix = "application/pdf", ".pdf"
        elif extension in {".jpg", ".jpeg", ".png"}:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    detected = image.format
                    width, height = image.size
                    if width * height > settings.OCR_MAX_IMAGE_PIXELS:
                        raise ValidationError("La resolución de la imagen supera el límite configurado.")
                    image.verify()
            if detected not in {"JPEG", "PNG"}:
                raise ValidationError("El archivo debe ser una imagen JPG o PNG válida.")
            if width * height > settings.OCR_MAX_IMAGE_PIXELS:
                raise ValidationError("La resolución de la imagen supera el límite configurado.")
            content_type, suffix = ("image/jpeg", ".jpg") if detected == "JPEG" else ("image/png", ".png")
        else:
            raise ValidationError("Carga un archivo PDF, JPG o PNG.")
    except ValidationError:
        raise
    except (pymupdf.FileDataError, RuntimeError, ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValidationError("El archivo está dañado o su contenido no coincide con el formato permitido.") from error
    if (extension == ".pdf" and content_type != "application/pdf") or (extension in {".jpg", ".jpeg"} and content_type != "image/jpeg") or (extension == ".png" and content_type != "image/png"):
        raise ValidationError("La extensión del archivo no coincide con su contenido.")
    safe_name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()[:255] or f"documento{suffix}"
    return data, content_type, suffix, safe_name, hashlib.sha256(data).hexdigest()


@transaction.atomic
def store_version(slot, upload, actor):
    application = Application.objects.select_for_update().get(pk=slot.application_id)
    slot = ApplicationRequirement.objects.select_for_update().get(pk=slot.pk, application=application)
    actor = User.objects.select_for_update().get(pk=actor.pk)
    if not actor.can_manage_admissions and application.applicant.user_id != actor.pk:
        raise PermissionDenied("No puedes modificar este expediente.")
    if actor.role == User.Role.ADMINISTRATOR and slot.current_version_id:
        if not slot.can_upload_by(actor):
            raise ValidationError("El documento o el expediente ya fue aprobado o cerrado.")
    else:
        if actor.can_manage_admissions and application.origin == Application.Origin.PORTAL:
            raise PermissionDenied("Solo el aspirante puede cargar documentos de una solicitud del portal.")
        if application.state not in {Application.State.DRAFT, Application.State.CORRECTION}:
            raise ValidationError("Los documentos solo se pueden cargar en un borrador o cuando hay correcciones pendientes.")
        if not slot.can_upload_by(actor):
            raise PermissionDenied("No puedes modificar este expediente.")
    uploaded_recently = DocumentVersion.objects.filter(
        uploaded_by=actor, created_at__gte=timezone.now() - timedelta(hours=1)
    ).count()
    if uploaded_recently >= settings.MAX_UPLOADS_PER_HOUR:
        raise ValidationError("Alcanzaste el límite temporal de cargas. Inténtalo más tarde.")
    data, content_type, suffix, safe_name, checksum = _validated_content(upload)
    previous = slot.versions.order_by("-version_number").values_list("version_number", flat=True).first() or 0
    key = f"applications/{application.public_id}/{slot.pk}/{uuid.uuid4().hex}{suffix}"
    storage = FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT, base_url="/private/")
    try:
        saved_key = storage.save(key, ContentFile(data))
    except OSError as error:
        raise ValidationError("No se pudo guardar el archivo. Inténtalo de nuevo más tarde.") from error
    try:
        version = DocumentVersion.objects.create(
            requirement=slot,
            version_number=previous + 1,
            storage_key=saved_key,
            original_name=safe_name,
            content_type=content_type,
            byte_size=len(data),
            sha256=checksum,
            uploaded_by=actor,
        )
        ProcessingJob.objects.create(version=version)
        if settings.AI_PREVALIDATION_ENABLED:
            ProcessingJob.objects.create(version=version, kind=ProcessingJob.Kind.AI)
        PrevalidationResult.objects.create(version=version)
        slot.current_version = version
        slot.save(update_fields=("current_version",))
        application.updated_at = timezone.now()
        application.save(update_fields=("updated_at",))
    except Exception:
        storage.delete(saved_key)
        raise
    return version
