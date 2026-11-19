import io
import tempfile
import zipfile
from importlib import import_module
from pathlib import Path

import pymupdf
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from admisiones.models import Applicant, Application, Call, Program, Review, User, applicant_file_key
from admisiones.services.files import store_version
from admisiones.services.workflow import create_application, create_staff_application


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


class StandardNamesAndArchiveTests(TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        storage = override_settings(PRIVATE_MEDIA_ROOT=Path(temp.name))
        storage.enable()
        self.addCleanup(storage.disable)
        self.admin = User.objects.create_superuser(email="admin-carpeta", password="Clave-admin-de-prueba!")
        self.applicant = Applicant.objects.create(
            email="carpeta@example.test", first_name="Ana", last_name="Acosta",
            identification_type="CC", identification_number="10.012-34567", phone="3000000000",
        )
        self.program = Program.objects.create(name="Carpeta")
        self.call = Call.objects.create(name="Convocatoria carpeta")
        self.application = create_staff_application(self.admin, {}, self.program, self.call, applicant=self.applicant)
        self.identity = self.application.requirements.get(code="identity")
        self.client.force_login(self.admin)

    def upload(self, data=None):
        return SimpleUploadedFile("identidad.pdf", data if data is not None else valid_pdf(), content_type="application/pdf")

    def archive_url(self):
        return reverse("application_zip", kwargs={"public_id": self.application.public_id})

    def test_file_key_removes_punctuation_and_falls_back_to_public_id(self):
        self.assertEqual(applicant_file_key(self.applicant), "1001234567")
        without_number = Applicant(identification_number="", phone="3000000000")
        self.assertEqual(applicant_file_key(without_number), str(without_number.public_id)[:8])

    def test_standard_name_uses_identification_code_version_and_extension(self):
        version = store_version(self.identity, self.upload(), self.admin)
        self.assertEqual(version.standard_name, "1001234567_identity_v1.pdf")

    def test_download_uses_the_standard_name(self):
        version = store_version(self.identity, self.upload(), self.admin)
        response = self.client.get(reverse("document_file", kwargs={"version_id": version.pk}), {"download": "1"})
        self.assertIn("1001234567_identity_v1.pdf", response["Content-Disposition"])
        b"".join(response.streaming_content)

    def test_archive_contains_only_current_versions_with_standard_names(self):
        store_version(self.identity, self.upload(), self.admin)
        store_version(self.identity, self.upload(valid_pdf("DOCUMENTO DE IDENTIDAD C.C. 1234567890 nuevo")), self.admin)
        response = self.client.get(self.archive_url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")
        archive = zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))
        self.assertEqual(archive.namelist(), ["1001234567_identity_v2.pdf"])

    def test_application_without_documents_does_not_send_an_empty_archive(self):
        response = self.client.get(self.archive_url())
        self.assertRedirects(response, reverse("reviewer_application", kwargs={"public_id": self.application.public_id}))

    def test_administrator_replaces_pending_file_in_review_but_cannot_replace_approval(self):
        original = store_version(self.identity, self.upload(), self.admin)
        Application.objects.filter(pk=self.application.pk).update(state=Application.State.IN_REVIEW)
        replacement = store_version(self.identity, self.upload(), self.admin)
        self.assertEqual(replacement.version_number, original.version_number + 1)
        self.assertEqual(self.identity.versions.count(), 2)
        Review.objects.create(version=replacement, reviewer=self.admin, decision=Review.Decision.APPROVE)
        with self.assertRaises(ValidationError):
            store_version(self.identity, self.upload(), self.admin)

    def test_archive_is_private_to_admissions(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.archive_url()).status_code, 302)
        applicant_user = User.objects.create_user(email="ana-carpeta@example.test", password="Fuerte-2026-Clave!", role=User.Role.APPLICANT)
        self.client.force_login(applicant_user)
        self.assertEqual(self.client.get(self.archive_url()).status_code, 403)

    def test_portal_draft_is_not_visible_to_reviewers(self):
        portal_user = User.objects.create_user(email="portal-carpeta@example.test", password="Fuerte-2026-Clave!", role=User.Role.APPLICANT)
        Applicant.objects.create(
            user=portal_user, email=portal_user.email, identification_type="CC", identification_number="5550001", phone="3000000001",
        )
        draft = create_application(portal_user, self.program, Call.objects.create(name="Convocatoria portal"))
        reviewer = User.objects.create_user(email="rev-carpeta@example.test", password="Fuerte-2026-Clave!", role=User.Role.REVIEWER)
        self.client.force_login(reviewer)
        url = reverse("application_zip", kwargs={"public_id": draft.public_id})
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.admin)
        detail_url = reverse("reviewer_application", kwargs={"public_id": draft.public_id})
        self.assertContains(self.client.get(detail_url), "Borrador")
        self.assertContains(self.client.get(reverse("administration_home")), detail_url)
