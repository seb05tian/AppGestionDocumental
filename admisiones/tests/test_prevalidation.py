import io
import shutil
import tempfile
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import pymupdf
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image, ImageDraw, ImageFont

from admisiones.models import Applicant, Call, PrevalidationResult, Program, ProcessingJob, User
from admisiones.services.files import store_version
from admisiones.services.workflow import create_application


def pdf(text="DOCUMENTO DE IDENTIDAD 1234567890"):
    document = pymupdf.open()
    page = document.new_page()
    if text.strip():
        page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


class PrevalidationWorkerTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = override_settings(PRIVATE_MEDIA_ROOT=Path(self.temp.name))
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.applicant = User.objects.create_user(email="aspirante@example.test", password="clave-segura-2026")
        Applicant.objects.create(user=self.applicant, email=self.applicant.email, identification_type="CC", identification_number="1234567890", phone="3005550101")
        self.program = Program.objects.create(name="Sistemas")
        self.call = Call.objects.create(name="2027-1")
        self.application = create_application(self.applicant, self.program, self.call)
        self.slot = self.application.requirements.get(code="identity")

    def upload(self, text="DOCUMENTO DE IDENTIDAD 1234567890", data=None):
        return store_version(self.slot, SimpleUploadedFile("identidad.pdf", data or pdf(text), "application/pdf"), self.applicant)

    def test_text_pdf_is_prevalidated_and_identification_is_compared(self):
        version = self.upload()
        from admisiones.services.processing import process_next_job
        self.assertTrue(process_next_job())
        result = PrevalidationResult.objects.get(version=version)
        self.assertEqual(result.status, PrevalidationResult.Status.CLEAR)
        self.assertEqual(result.extracted_identification, "1234567890")
        self.assertTrue(result.identification_matches)
        self.assertEqual(result.rules_version, "2026-01")
        self.assertFalse(process_next_job())

    @skipUnless(shutil.which("tesseract"), "Tesseract local está instalado en la imagen de ejecución del trabajador")
    def test_scanned_pdf_uses_local_ocr_with_synthetic_content(self):
        canvas = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(canvas)
        draw.text((75, 280), "DOCUMENTO DE IDENTIDAD", fill="black", font=ImageFont.load_default(size=70))
        draw.text((75, 420), "1234567890", fill="black", font=ImageFont.load_default(size=80))
        image = io.BytesIO()
        canvas.save(image, format="PNG")
        document = pymupdf.open()
        page = document.new_page(width=595, height=842)
        page.insert_image(page.rect, stream=image.getvalue())
        scanned = document.tobytes()
        document.close()
        version = self.upload(data=scanned)
        from admisiones.services.processing import process_next_job
        process_next_job()
        result = PrevalidationResult.objects.get(version=version)
        self.assertEqual(result.status, PrevalidationResult.Status.CLEAR)
        self.assertEqual(result.extracted_identification, "1234567890")

    def test_weak_text_is_inconclusive_without_invalidating_manual_review(self):
        version = self.upload("\n")
        from admisiones.services import processing
        with patch.object(processing, "_ocr", return_value=""):
            processing.process_next_job()
        result = PrevalidationResult.objects.get(version=version)
        self.assertEqual(result.status, PrevalidationResult.Status.INCONCLUSIVE)
        self.assertTrue(version.requirement.current_version_id == version.id)
        self.assertIsNone(version.reviews.first())

    def test_generic_text_without_identifiable_fields_is_inconclusive(self):
        version = self.upload("Este es un documento impreso sin identificacion reconocible")
        from admisiones.services.processing import process_next_job
        process_next_job()
        self.assertEqual(
            PrevalidationResult.objects.get(version=version).status,
            PrevalidationResult.Status.INCONCLUSIVE,
        )

    def test_type_rule_keywords_can_be_changed_without_editing_the_worker(self):
        version = self.upload("Dato local especifico 1234567890")
        from admisiones.services.processing import process_next_job
        rules = (("identity", ("dato local especifico",), "Identidad según regla local"),)
        with override_settings(OCR_TYPE_RULES=rules, OCR_RULES_VERSION="prueba-2"):
            process_next_job()
        result = PrevalidationResult.objects.get(version=version)
        self.assertEqual(result.detected_type, "Identidad según regla local")
        self.assertEqual(result.rules_version, "prueba-2")

    def test_duplicate_file_across_requirements_creates_explicit_alert(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        data = pdf()
        self.upload(data=data)
        other_slot = self.application.requirements.get(code="saber11")
        second = store_version(other_slot, SimpleUploadedFile("resultado.pdf", data, "application/pdf"), self.applicant)
        from admisiones.services.processing import process_next_job
        process_next_job()
        process_next_job()
        result = PrevalidationResult.objects.get(version=second)
        self.assertEqual(result.status, PrevalidationResult.Status.REVIEW)
        self.assertTrue(any(item["code"] == "duplicate_file" for item in result.alerts))

    def test_failed_processing_retries_then_exposes_technical_error(self):
        version = self.upload()
        from admisiones.services import processing
        with override_settings(OCR_MAX_ATTEMPTS=2):
            with patch.object(processing, "_extract_text", side_effect=RuntimeError("private OCR detail")):
                processing.process_next_job()
                job = ProcessingJob.objects.get(version=version)
                self.assertEqual(job.status, ProcessingJob.Status.QUEUED)
                self.assertEqual(PrevalidationResult.objects.get(version=version).status, PrevalidationResult.Status.PENDING)
                job.available_at = processing.timezone.now()
                job.save(update_fields=("available_at",))
                processing.process_next_job()
        job.refresh_from_db()
        result = PrevalidationResult.objects.get(version=version)
        self.assertEqual(job.status, ProcessingJob.Status.FAILED)
        self.assertEqual(result.status, PrevalidationResult.Status.ERROR)
        self.assertNotIn("private OCR detail", result.extracted_text)
        self.assertEqual(result.extracted_text, "")

    def test_stale_running_job_is_recovered_without_creating_a_decision(self):
        from django.utils import timezone
        from datetime import timedelta
        version = self.upload()
        job = ProcessingJob.objects.get(version=version)
        job.status = ProcessingJob.Status.RUNNING
        job.attempts = 1
        job.locked_at = timezone.now() - timedelta(minutes=30)
        job.save(update_fields=("status", "attempts", "locked_at"))
        from admisiones.services.processing import recover_stale_jobs
        self.assertEqual(recover_stale_jobs(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, ProcessingJob.Status.QUEUED)
        self.assertIsNone(PrevalidationResult.objects.get(version=version).processed_at)

    def test_old_version_processing_does_not_replace_current_result(self):
        first = self.upload()
        second = store_version(self.slot, SimpleUploadedFile("nueva.pdf", pdf("SABER 11 9999999999"), "application/pdf"), self.applicant)
        from admisiones.services.processing import process_next_job
        process_next_job()
        self.slot.refresh_from_db()
        self.assertEqual(self.slot.current_version_id, second.id)
        self.assertEqual(PrevalidationResult.objects.get(version=first).status, PrevalidationResult.Status.CLEAR)
        self.assertEqual(PrevalidationResult.objects.get(version=second).status, PrevalidationResult.Status.PENDING)

