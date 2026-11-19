import io
import tempfile
from pathlib import Path

import pymupdf
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from admisiones.models import Applicant, Application, Call, EmailOutbox, Notification, Observation, Program, RequirementDefinition, User
from admisiones.services.files import store_version
from admisiones.services.state import transition_application
from admisiones.services.workflow import create_application


def synthetic_pdf(text="DOCUMENTO DE IDENTIDAD 1234567890"):
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


class ApplicantAndReviewerPortalTests(TransactionTestCase):
    def setUp(self):
        for code, name, position in (
            ("identity", "Documento de identidad", 1),
            ("saber11", "Resultado de pruebas Saber 11", 2),
            ("graduation", "Acta de grado o diploma", 3),
        ):
            RequirementDefinition.objects.get_or_create(
                call=None,
                code=code,
                defaults={"name": name, "required": True, "position": position},
            )
        self.temp_storage = tempfile.TemporaryDirectory()
        self.storage_override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.temp_storage.name))
        self.storage_override.enable()
        self.addCleanup(self.storage_override.disable)
        self.addCleanup(self.temp_storage.cleanup)
        self.program = Program.objects.create(name="Ingeniería de Sistemas")
        self.call = Call.objects.create(name="Convocatoria 2027-1")
        self.applicant = User.objects.create_user(email="aspirante@example.test", password="Fuerte-2026-Clave!")
        Applicant.objects.create(user=self.applicant, email=self.applicant.email, identification_type="CC", identification_number="1234567890", phone="3005550101")
        self.other = User.objects.create_user(email="otro@example.test", password="Fuerte-2026-Clave!")
        Applicant.objects.create(user=self.other, email=self.other.email, identification_type="CC", identification_number="0987654321", phone="3005550102")
        self.reviewer = User.objects.create_user(email="revisor@example.test", password="Fuerte-2026-Clave!", role=User.Role.REVIEWER)
        self.client.force_login(self.applicant)

    def application_with_files(self, applicant=None, submitted=False):
        application = create_application(applicant or self.applicant, self.program, self.call)
        for slot in application.requirements.all():
            store_version(slot, SimpleUploadedFile(f"{slot.code}.pdf", synthetic_pdf(), "application/pdf"), applicant or self.applicant)
        if submitted:
            transition_application(application, Application.State.SUBMITTED, applicant or self.applicant)
            application.refresh_from_db()
        return application

    def test_applicant_creates_request_and_duplicate_is_rejected(self):
        url = reverse("application_new")
        payload = {"program": self.program.pk, "call": self.call.pk}
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Application.objects.filter(applicant__user=self.applicant).count(), 1)
        duplicate = self.client.post(url, payload)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(Application.objects.filter(applicant__user=self.applicant).count(), 1)
        self.assertContains(duplicate, "Ya existe una solicitud")

    def test_guessed_application_and_file_ids_do_not_grant_access(self):
        application = self.application_with_files(self.other)
        version = application.requirements.get(code="identity").current_version
        detail = reverse("applicant_application", kwargs={"public_id": application.public_id})
        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(self.client.get(reverse("document_file", kwargs={"version_id": version.pk})).status_code, 404)
        slot = application.requirements.get(code="identity")
        response = self.client.post(
            reverse("applicant_upload", kwargs={"public_id": application.public_id, "slot_id": slot.pk}),
            {"file": SimpleUploadedFile("nuevo.pdf", synthetic_pdf(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 404)

    def test_applicant_can_upload_and_submit_all_required_files(self):
        response = self.client.post(reverse("application_new"), {"program": self.program.pk, "call": self.call.pk})
        self.assertEqual(response.status_code, 302)
        application = Application.objects.get(applicant__user=self.applicant)
        for slot in application.requirements.all():
            response = self.client.post(
                reverse("applicant_upload", kwargs={"public_id": application.public_id, "slot_id": slot.pk}),
                {"file": SimpleUploadedFile(f"{slot.code}.pdf", synthetic_pdf(), "application/pdf")},
            )
            self.assertEqual(response.status_code, 302)
        response = self.client.post(reverse("applicant_submit", kwargs={"public_id": application.public_id}))
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.state, Application.State.SUBMITTED)
        notification = Notification.objects.get(application=application, kind=Notification.Kind.RECEIVED)
        self.assertTrue(EmailOutbox.objects.filter(notification=notification).exists())

    def test_reviewer_requests_correction_visible_to_applicant_but_internal_note_is_not(self):
        application = self.application_with_files(submitted=True)
        self.client.force_login(self.reviewer)
        self.assertEqual(self.client.get(reverse("reviewer_inbox")).status_code, 200)
        self.client.post(reverse("reviewer_assign", kwargs={"public_id": application.public_id}))
        reviewer_page = self.client.get(reverse("reviewer_application", kwargs={"public_id": application.public_id}))
        self.assertContains(reviewer_page, "Aprobar documento")
        self.assertNotContains(reviewer_page, 'name="internal_note"')
        Observation.objects.create(
            application=application, author=self.reviewer,
            audience=Observation.Audience.INTERNAL, text="Llamar al equipo de soporte.",
        )
        slot = application.requirements.get(code="identity")
        version = slot.current_version
        response = self.client.post(
            reverse("review_document", kwargs={"public_id": application.public_id, "slot_id": slot.pk}),
            {"version_id": version.pk, "decision": "correction", "reason": "La imagen no muestra el número completo."},
        )
        self.assertEqual(response.status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.state, Application.State.IN_REVIEW)
        finalize = reverse("reviewer_send_corrections", kwargs={"public_id": application.public_id})
        self.assertEqual(self.client.post(finalize).status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.state, Application.State.IN_REVIEW)
        for other_slot in application.requirements.exclude(pk=slot.pk):
            response = self.client.post(
                reverse("review_document", kwargs={"public_id": application.public_id, "slot_id": other_slot.pk}),
                {"version_id": other_slot.current_version_id, "decision": "approve"},
            )
            self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.post(finalize).status_code, 302)
        application.refresh_from_db()
        self.assertEqual(application.state, Application.State.CORRECTION)
        self.assertTrue(Observation.objects.filter(application=application, audience=Observation.Audience.INTERNAL).exists())
        self.client.force_login(self.applicant)
        applicant_page = self.client.get(reverse("applicant_application", kwargs={"public_id": application.public_id}))
        self.assertContains(applicant_page, "La imagen no muestra el número completo.")
        self.assertNotContains(applicant_page, "equipo de soporte")
        self.assertContains(applicant_page, "Requiere corrección")

    def test_reviewer_can_download_submitted_file_but_applicant_cannot_read_draft_of_another(self):
        application = self.application_with_files(submitted=True)
        version = application.requirements.get(code="identity").current_version
        self.client.force_login(self.reviewer)
        response = self.client.get(reverse("document_file", kwargs={"version_id": version.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"%PDF-", b"".join(response.streaming_content))
        response.close()
        draft = create_application(self.other, self.program, Call.objects.create(name="Convocatoria alterna"))
        private_version = store_version(
            draft.requirements.get(code="identity"),
            SimpleUploadedFile("id.pdf", synthetic_pdf(), "application/pdf"),
            self.other,
        )
        self.assertEqual(self.client.get(reverse("document_file", kwargs={"version_id": private_version.pk})).status_code, 404)
