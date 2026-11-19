import io
import json
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless
from unittest.mock import patch

import pymupdf
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from jsonschema import ValidationError as SchemaError
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

    @override_settings(AI_PREVALIDATION_ENABLED=True)
    def test_reviewer_analysis_fragment_reports_extracted_values_and_is_private(self):
        version = self.upload()
        admin = User.objects.create_superuser(email="admin", password="Clave-prueba-2026!")
        self.client.force_login(admin)
        url = reverse("reviewer_application", kwargs={"public_id": self.application.public_id}) + "?analisis=1"
        pending = self.client.get(url)
        self.assertContains(pending, "data-ai-pending")
        self.assertContains(pending, 'class="ai-progress-ring"')
        version.processing_jobs.filter(kind=ProcessingJob.Kind.AI).update(status=ProcessingJob.Status.DONE)
        PrevalidationResult.objects.filter(version=version).update(ai_result={
            "es_legible": True, "tipo_documento_correcto": True,
            "identificacion_detectada": "0012345678", "nombre_detectado": "Persona Encontrada",
            "diagnostico": "Sin problemas visibles", "completo": True, "modelo": "test",
        })
        response = self.client.get(url)
        self.assertContains(response, "0012345678")
        self.assertContains(response, "Persona Encontrada")
        self.assertContains(response, "No coincide")
        self.assertNotContains(response, "data-ai-pending")
        self.assertNotContains(response, 'class="ai-progress-ring"')
        self.assertIn("no-store", response["Cache-Control"])
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.get(url).status_code, 403)

    @override_settings(AI_PREVALIDATION_ENABLED=True)
    def test_ai_is_queued_separately_for_each_version(self):
        from admisiones.services.processing import process_next_job

        first = self.upload()
        second = self.upload()
        with patch("admisiones.services.processing.verify_document", return_value={"test": True}):
            self.assertTrue(process_next_job(ProcessingJob.Kind.AI))
        self.assertEqual(PrevalidationResult.objects.get(version=first).ai_result, {"test": True})
        self.assertIsNone(PrevalidationResult.objects.get(version=second).ai_result)
        self.assertEqual(first.processing_jobs.get(kind=ProcessingJob.Kind.OCR).status, ProcessingJob.Status.QUEUED)

    @override_settings(AI_PREVALIDATION_ENABLED=True)
    def test_ai_transient_and_permanent_failures_leave_ocr_available(self):
        from admisiones.services.processing import process_next_job

        version = self.upload()
        kind = ProcessingJob.Kind.AI
        with patch("admisiones.services.processing.verify_document", side_effect=TimeoutError):
            self.assertTrue(process_next_job(kind))
            self.assertFalse(process_next_job(kind))
        job = version.processing_jobs.get(kind=kind)
        self.assertEqual(job.attempts, 1)
        version.processing_jobs.filter(kind=kind).update(available_at=timezone.now())
        with patch("admisiones.services.processing.verify_document", side_effect=ValueError):
            self.assertTrue(process_next_job(kind))
        job.refresh_from_db()
        self.assertEqual(job.status, ProcessingJob.Status.FAILED)
        self.assertTrue(process_next_job())
        result = PrevalidationResult.objects.get(version=version)
        self.assertEqual(result.status, PrevalidationResult.Status.CLEAR)
        self.assertIsNone(result.ai_result)

    @override_settings(AI_PREVALIDATION_ENABLED=True)
    def test_ai_recovery_only_recovers_the_ai_lease(self):
        from admisiones.services.processing import recover_stale_jobs

        version = self.upload()
        version.processing_jobs.update(
            status=ProcessingJob.Status.RUNNING, locked_at=timezone.now() - timedelta(hours=1), attempts=1,
        )
        self.assertEqual(recover_stale_jobs(ProcessingJob.Kind.AI), 1)
        self.assertEqual(version.processing_jobs.get(kind=ProcessingJob.Kind.AI).status, ProcessingJob.Status.QUEUED)
        self.assertEqual(version.processing_jobs.get(kind=ProcessingJob.Kind.OCR).status, ProcessingJob.Status.RUNNING)

    @override_settings(AI_PREVALIDATION_ENABLED=True)
    def test_vision_compares_identity_and_preserves_partial_coverage(self):
        from admisiones.services.ai_verifier import reviewer_summary, verify_document

        document = pymupdf.open()
        document.new_page()
        document.new_page()
        version = self.upload(data=document.tobytes())
        document.close()
        found = {"es_legible": True, "tipo_documento_correcto": True,
                 "identificacion_detectada": "0012345678", "nombre_detectado": "",
                 "diagnostico": "Sin problemas visibles", "nivel_confianza": 0.8}
        with patch("admisiones.services.ai_verifier._request_page", side_effect=[found, TimeoutError]):
            result = verify_document(version)
        job = version.processing_jobs.get(kind=ProcessingJob.Kind.AI)
        summary = reviewer_summary(job, result, self.application.applicant)
        self.assertEqual(summary["label"], "No concluyente")
        self.assertFalse(summary["identification_matches"])
        self.assertEqual(summary["identificacion_detectada"], "0012345678")
        self.assertEqual(result["paginas_revisadas"], 1)
        self.assertEqual(result["identificacion_detectada"], "0012345678")
        with patch("admisiones.services.ai_verifier._request_page", side_effect=[
            found, {**found, "identificacion_detectada": "0012345600"},
        ]):
            conflict = verify_document(version)
        self.assertEqual(conflict["identificacion_detectada"], "")
        self.assertTrue(conflict["identidad_ambigua"])
        self.assertEqual(reviewer_summary(job, conflict, self.application.applicant)["label"], "Requiere revisión")

    def test_vision_prepares_jpg_and_png_and_rejects_invalid_output(self):
        from admisiones.services.ai_verifier import _pages, _request_page

        for format_name, content_type in (("JPEG", "image/jpeg"), ("PNG", "image/png")):
            with self.subTest(format_name=format_name):
                data = io.BytesIO()
                fixture = Image.new("RGBA", (300, 200), (0, 0, 0, 0)) if format_name == "PNG" else Image.new("RGB", (300, 200), "white")
                fixture.save(data, format=format_name)
                version = store_version(self.slot, SimpleUploadedFile(
                    f"fixture.{format_name.lower()}", data.getvalue(), content_type,
                ), self.applicant)
                page, = _pages(version, data.getvalue())
                with Image.open(io.BytesIO(page)) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.size, (300, 200))
                    self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
                with patch("admisiones.services.ai_verifier.build_opener") as opener:
                    response = opener.return_value.open.return_value.__enter__.return_value
                    response.read.return_value = json.dumps({
                        "done_reason": "stop", "message": {"content": '{"es_legible": "sí"}'},
                    }).encode()
                    with self.assertRaises(SchemaError):
                        _request_page(page, self.slot, 1, 10)
                    response.read.return_value = json.dumps({
                        "done_reason": "stop", "message": {"content": json.dumps({
                            "es_legible": None, "tipo_documento_correcto": None,
                            "identificacion_detectada": "\0", "nombre_detectado": "",
                            "diagnostico": "Evidencia insuficiente", "nivel_confianza": 0,
                        })},
                    }).encode()
                    with self.assertRaisesRegex(ValueError, "invalid_json_text"):
                        _request_page(page, self.slot, 1, 10)

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

