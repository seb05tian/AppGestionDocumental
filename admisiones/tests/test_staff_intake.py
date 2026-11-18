import tempfile
from pathlib import Path

import pymupdf
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from admisiones.models import Applicant, Application, Call, Program, User
from admisiones.services import workflow
from admisiones.services.files import store_version


class StaffIntakeTests(TestCase):
    def setUp(self):
        self.reviewer = User.objects.create_user(
            email="staff@example.test", password="Clave-segura-2026!", role=User.Role.REVIEWER
        )
        self.program = Program.objects.create(name="Medicina")
        self.call = Call.objects.create(name="2027-1")
        self.details = {
            "first_name": "Laura", "last_name": "Ríos", "identification_type": "CC",
            "identification_number": "0012345678", "email": "laura@example.test", "phone": "03005550101",
        }

    def test_reviewer_creates_person_and_draft_without_account(self):
        self.assertTrue(hasattr(workflow, "create_staff_application"))
        application = workflow.create_staff_application(self.reviewer, self.details, self.program, self.call)
        self.assertEqual(application.state, Application.State.DRAFT)
        self.assertIsNone(application.applicant.user_id)
        self.assertEqual(application.applicant.identification_number, "0012345678")
        self.assertEqual(application.applicant.phone, "03005550101")
        self.assertEqual(application.created_by, self.reviewer)
        self.assertEqual(application.requirements.count(), 3)
        self.assertEqual(Applicant.objects.count(), 1)

    def test_reviewer_upload_uses_existing_version_service(self):
        self.assertTrue(hasattr(workflow, "create_staff_application"))
        application = workflow.create_staff_application(self.reviewer, self.details, self.program, self.call)
        slot = application.requirements.get(code="identity")
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "DOCUMENTO DE IDENTIDAD 0012345678")
        data = document.tobytes()
        document.close()
        with tempfile.TemporaryDirectory() as folder, override_settings(PRIVATE_MEDIA_ROOT=Path(folder)):
            version = store_version(slot, SimpleUploadedFile("identidad.pdf", data), self.reviewer)
            self.assertEqual(version.uploaded_by, self.reviewer)
            self.assertTrue(Path(folder, version.storage_key).exists())

    def test_list_can_distinguish_applicants_with_and_without_accounts(self):
        person = Applicant.objects.create(**self.details)
        user = User.objects.create_user(email="luis@example.test")
        linked = Applicant.objects.create(
            **{**self.details, "email": user.email, "identification_number": "0012345679", "user": user},
        )
        self.client.force_login(self.reviewer)
        response = self.client.get("/admisiones/aspirantes/", {"account": "unlinked", "q": "Laura Ríos"})
        self.assertEqual(list(response.context["people"]), [person])
        response = self.client.get("/admisiones/aspirantes/?account=linked")
        self.assertEqual(list(response.context["people"]), [linked])
