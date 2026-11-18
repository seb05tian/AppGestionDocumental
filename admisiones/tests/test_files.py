import io
import tempfile
from importlib import import_module
from pathlib import Path

import pymupdf
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from admisiones.models import Applicant, Call, Program, Review, User
from admisiones.services.workflow import create_application


def valid_pdf(text="DOCUMENTO DE IDENTIDAD C.C. 1234567890"):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


class PrivateDocumentVersionTests(TestCase):
    def setUp(self):
        self.applicant = User.objects.create_user(email="aspirante@example.test", password="Fuerte-2026-Clave!")
        self.other = User.objects.create_user(email="otro@example.test", password="Fuerte-2026-Clave!")
        self.reviewer = User.objects.create_user(
            email="revisor@example.test", password="Fuerte-2026-Clave!", role=User.Role.REVIEWER
        )
        self.program = Program.objects.create(name="Ingeniería de Sistemas")
        self.call = Call.objects.create(name="Convocatoria 2027-1")
        Applicant.objects.create(user=self.applicant, email=self.applicant.email)
        self.application = create_application(self.applicant, self.program, self.call)
        self.slot = self.application.requirements.get(code="identity")

    def upload(self, name="identidad.pdf", data=None):
        return SimpleUploadedFile(name, data if data is not None else valid_pdf(), content_type="application/pdf")

    def test_rejects_fake_pdf_signature(self):
        files = import_module("admisiones.services.files")
        store = getattr(files, "store_version", None)
        self.assertTrue(callable(store), "store_version must validate content before storing it")
        with self.assertRaises(ValidationError):
            store(self.slot, self.upload(data=b"not a pdf"), self.applicant)

    def test_rejects_encrypted_pdf(self):
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "private")
        output = io.BytesIO()
        doc.save(output, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
        doc.close()
        files = import_module("admisiones.services.files")
        store = getattr(files, "store_version", None)
        self.assertTrue(callable(store))
        with self.assertRaises(ValidationError):
            store(self.slot, self.upload(data=output.getvalue()), self.applicant)

    def test_rejects_image_disguised_as_pdf(self):
        output = io.BytesIO()
        Image.new("RGB", (40, 40), "white").save(output, format="PNG")
        files = import_module("admisiones.services.files")
        store = getattr(files, "store_version", None)
        self.assertTrue(callable(store))
        with self.assertRaises(ValidationError):
            store(self.slot, self.upload(data=output.getvalue()), self.applicant)

    def test_rejects_file_over_configured_size(self):
        files = import_module("admisiones.services.files")
        store = getattr(files, "store_version", None)
        self.assertTrue(callable(store))
        with override_settings(MAX_UPLOAD_MB=1):
            with self.assertRaises(ValidationError):
                store(self.slot, self.upload(data=b"%PDF-" + b"x" * (1024 * 1024)), self.applicant)

    def test_rejects_image_over_configured_pixel_limit(self):
        output = io.BytesIO()
        Image.new("RGB", (20, 20), "white").save(output, format="PNG")
        files = import_module("admisiones.services.files")
        with override_settings(OCR_MAX_IMAGE_PIXELS=100):
            with self.assertRaises(ValidationError):
                files.store_version(
                    self.slot,
                    SimpleUploadedFile("identidad.png", output.getvalue(), content_type="image/png"),
                    self.applicant,
                )

    def test_upload_rate_limit_is_enforced_across_requirements(self):
        files = import_module("admisiones.services.files")
        with override_settings(MAX_UPLOADS_PER_HOUR=1):
            files.store_version(self.slot, self.upload(), self.applicant)
            second_slot = self.application.requirements.get(code="saber11")
            with self.assertRaises(ValidationError):
                files.store_version(second_slot, self.upload("resultado.pdf"), self.applicant)

    def test_valid_file_is_private_versioned_and_replacement_preserves_decision(self):
        files = import_module("admisiones.services.files")
        store = getattr(files, "store_version", None)
        self.assertTrue(callable(store))
        with tempfile.TemporaryDirectory() as private_dir, override_settings(PRIVATE_MEDIA_ROOT=Path(private_dir)):
            first = store(self.slot, self.upload(), self.applicant)
            self.assertEqual(first.version_number, 1)
            self.assertEqual(first.processing_jobs.get().status, "queued")
            first_path = Path(private_dir, first.storage_key)
            self.assertTrue(first_path.is_file())
            Review.objects.create(version=first, reviewer=self.reviewer, decision=Review.Decision.APPROVE)
            second = store(self.slot, self.upload(data=valid_pdf("SABER 11 1234567890")), self.applicant)
            self.slot.refresh_from_db()
            self.assertEqual(second.version_number, 2)
            self.assertEqual(self.slot.current_version_id, second.pk)
            self.assertEqual(self.slot.document_state(), "pending_review")
            self.assertTrue(first_path.is_file())
            self.assertEqual(first.reviews.count(), 1)

    def test_another_applicant_cannot_replace_the_file(self):
        files = import_module("admisiones.services.files")
        store = getattr(files, "store_version", None)
        self.assertTrue(callable(store))
        with self.assertRaises(PermissionDenied):
            store(self.slot, self.upload(), self.other)
