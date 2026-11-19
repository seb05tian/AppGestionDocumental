import io
import logging
import re
import time
import unicodedata
from datetime import timedelta

import pymupdf
import pytesseract
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.storage import FileSystemStorage
from django.db import transaction
from django.utils import timezone
from PIL import Image

from admisiones.models import PrevalidationResult, ProcessingJob
from .ai_verifier import verify_document

logger = logging.getLogger(__name__)


def _plain(text):
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(character for character in normalized if not unicodedata.combining(character))


def _ocr(image, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Límite total de OCR excedido")
    return pytesseract.image_to_string(image, lang="spa+eng", config="--psm 6", timeout=remaining)


def _extract_text(version):
    storage = FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT)
    with storage.open(version.storage_key, "rb") as source:
        data = source.read()
    deadline = time.monotonic() + settings.OCR_TIMEOUT_SECONDS
    if version.content_type == "application/pdf":
        pieces = []
        with pymupdf.open(stream=data, filetype="pdf") as document:
            if document.needs_pass or document.is_encrypted:
                raise ValueError("PDF protegido")
            if document.page_count > settings.OCR_MAX_PAGES:
                raise ValueError("Límite de páginas excedido")
            for page in document:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Límite total de procesamiento excedido")
                text = page.get_text("text").strip()
                if time.monotonic() >= deadline:
                    raise TimeoutError("Límite total de procesamiento excedido")
                if not text:
                    raster_width = round(page.rect.width * settings.OCR_DPI / 72)
                    raster_height = round(page.rect.height * settings.OCR_DPI / 72)
                    if raster_width * raster_height > settings.OCR_MAX_IMAGE_PIXELS:
                        raise ValueError("Límite de resolución excedido")
                    pixmap = page.get_pixmap(dpi=settings.OCR_DPI, alpha=False)
                    with Image.open(io.BytesIO(pixmap.tobytes("png"))) as image:
                        text = _ocr(image, deadline).strip()
                if text:
                    pieces.append(text)
        return "\n".join(pieces)
    if time.monotonic() >= deadline:
        raise TimeoutError("Límite total de procesamiento excedido")
    with Image.open(io.BytesIO(data)) as image:
        return _ocr(image, deadline).strip()


def _analyze(version, text):
    result = {"status": PrevalidationResult.Status.CLEAR, "text": text, "kind": "", "identification": "", "matches": None, "alerts": []}
    plain = _plain(text)
    detected = next((item for item in settings.OCR_TYPE_RULES if any(_plain(term) in plain for term in item[1])), None)
    if detected:
        result["kind"] = detected[2]

    match = re.search(settings.OCR_IDENTIFICATION_PATTERN, text)
    number_found = False
    if match:
        number = re.sub(r"\D", "", match.group(1))
        if number:
            number_found = True
            result["identification"] = number
            try:
                expected = version.requirement.application.applicant.identification_number
            except ObjectDoesNotExist:
                expected = ""
            if expected:
                result["matches"] = number == re.sub(r"\D", "", expected)
                if not result["matches"]:
                    result["alerts"].append({"code": "identification_mismatch", "message": "El número encontrado no coincide con el registrado en la solicitud."})

    duplicate = version.requirement.application.requirements.exclude(pk=version.requirement_id).filter(
        current_version__sha256=version.sha256
    ).exists()
    if duplicate:
        result["alerts"].append({"code": "duplicate_file", "message": "El mismo archivo está cargado en otro requisito de este expediente."})

    if result["alerts"]:
        result["status"] = PrevalidationResult.Status.REVIEW
    elif len(re.sub(r"\s", "", text)) < 12 or (not detected and not number_found):
        result["status"] = PrevalidationResult.Status.INCONCLUSIVE
    return result


def _finish_success(job_id, version_id, claimed_at, analyzed):
    with transaction.atomic():
        job = ProcessingJob.objects.select_for_update().get(pk=job_id)
        if job.status != ProcessingJob.Status.RUNNING or job.locked_at != claimed_at:
            return False
        result = PrevalidationResult.objects.select_for_update().get(version_id=version_id)
        if job.kind == ProcessingJob.Kind.AI:
            result.ai_result = analyzed
            result.save(update_fields=("ai_result",))
        else:
            result.status = analyzed["status"]
            result.extracted_text = analyzed["text"][:500_000]
            result.detected_type = analyzed["kind"]
            result.extracted_identification = analyzed["identification"]
            result.identification_matches = analyzed["matches"]
            result.alerts = analyzed["alerts"]
            result.rules_version = settings.OCR_RULES_VERSION
            result.processed_at = timezone.now()
            result.save()
        job.status = ProcessingJob.Status.DONE
        job.locked_at = None
        job.error_message = ""
        job.save(update_fields=("status", "locked_at", "error_message"))
        return True


def _finish_error(job_id, version_id, claimed_at, *, retry=True):
    with transaction.atomic():
        job = ProcessingJob.objects.select_for_update().get(pk=job_id)
        if job.status != ProcessingJob.Status.RUNNING or job.locked_at != claimed_at:
            return False
        if retry and job.attempts < _max_attempts(job.kind):
            job.status = ProcessingJob.Status.QUEUED
            job.available_at = timezone.now() + timedelta(seconds=min(300, 15 * (2 ** (job.attempts - 1))))
            job.error_message = "El procesamiento local falló; se reintentará."
        else:
            job.status = ProcessingJob.Status.FAILED
            job.error_message = "No se pudo completar el procesamiento local."
            if job.kind == ProcessingJob.Kind.OCR:
                result = PrevalidationResult.objects.select_for_update().get(version_id=version_id)
                result.status = PrevalidationResult.Status.ERROR
                result.extracted_text = ""
                result.detected_type = ""
                result.extracted_identification = ""
                result.identification_matches = None
                result.alerts = [{"code": "processing_error", "message": "El procesamiento tuvo un error técnico. Continúa con la revisión manual."}]
                result.rules_version = settings.OCR_RULES_VERSION
                result.processed_at = timezone.now()
                result.save()
        job.locked_at = None
        job.save(update_fields=("status", "available_at", "error_message", "locked_at"))
        return True


def _max_attempts(kind):
    return 3 if kind == ProcessingJob.Kind.AI else settings.OCR_MAX_ATTEMPTS


def process_next_job(kind=ProcessingJob.Kind.OCR):
    with transaction.atomic():
        now = timezone.now()
        job = ProcessingJob.objects.select_for_update(skip_locked=True).filter(
            kind=kind, status=ProcessingJob.Status.QUEUED, available_at__lte=now
        ).order_by("created_at", "pk").first()
        if not job:
            return False
        job.status = ProcessingJob.Status.RUNNING
        job.locked_at = now
        job.attempts += 1
        job.save(update_fields=("status", "locked_at", "attempts"))
        claim = job.pk, job.version_id, now
    job_id, version_id, claimed_at = claim
    started = time.monotonic()
    try:
        version = ProcessingJob.objects.select_related("version__requirement__application__applicant").get(pk=job_id).version
        analysis = verify_document(version) if kind == ProcessingJob.Kind.AI else _analyze(version, _extract_text(version))
    except Exception as error:
        retry = kind == ProcessingJob.Kind.OCR or isinstance(error, OSError)
        _finish_error(job_id, version_id, claimed_at, retry=retry)
        if kind == ProcessingJob.Kind.AI:
            logger.warning("ai_prevalidation %s duration_seconds=%.2f",
                           "temporary_error" if retry else "invalid_analysis", time.monotonic() - started)
    else:
        saved = _finish_success(job_id, version_id, claimed_at, analysis)
        if saved and kind == ProcessingJob.Kind.AI:
            logger.info("ai_prevalidation done duration_seconds=%.2f", time.monotonic() - started)
    return True


def recover_stale_jobs(kind=ProcessingJob.Kind.OCR):
    cutoff = timezone.now() - timedelta(minutes=settings.OCR_STALE_MINUTES)
    count = 0
    with transaction.atomic():
        jobs = ProcessingJob.objects.select_for_update(skip_locked=True).filter(
            kind=kind, status=ProcessingJob.Status.RUNNING, locked_at__lt=cutoff
        )
        for job in jobs:
            if job.attempts >= _max_attempts(kind):
                job.status = ProcessingJob.Status.FAILED
                job.error_message = "El trabajador se interrumpió durante el procesamiento."
                if kind == ProcessingJob.Kind.OCR:
                    result = PrevalidationResult.objects.select_for_update().get(version=job.version)
                    result.status = PrevalidationResult.Status.ERROR
                    result.extracted_text = ""
                    result.alerts = [{"code": "processing_error", "message": "El procesamiento tuvo un error técnico. Continúa con la revisión manual."}]
                    result.rules_version = settings.OCR_RULES_VERSION
                    result.processed_at = timezone.now()
                    result.save()
            else:
                job.status = ProcessingJob.Status.QUEUED
                job.available_at = timezone.now()
                job.error_message = ""
            job.locked_at = None
            job.save(update_fields=("status", "available_at", "error_message", "locked_at"))
            count += 1
    return count
