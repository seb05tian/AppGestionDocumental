import csv
import io
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from admisiones.models import (
    Applicant, Application, Call, DocumentVersion, Notification, PrevalidationResult,
    Program, Review, StateHistory, User,
)
from admisiones.services.metrics import compute_indicators
from admisiones.services.workflow import create_application


class ReviewerQueueAndIndicatorTests(TestCase):
    def setUp(self):
        self.reviewer = User.objects.create_user(email="revisor@example.test", password="clave-segura-2026", role=User.Role.REVIEWER)
        self.applicant = User.objects.create_user(email="ana@example.test", password="clave-segura-2026", first_name="Ana", last_name="Pérez")
        Applicant.objects.create(
            user=self.applicant, email=self.applicant.email, first_name="Ana", last_name="Pérez",
            identification_type="CC", identification_number="1234567890", phone="3005550101",
        )
        self.program = Program.objects.create(name="=HYPERLINK(\"x\")")
        self.call = Call.objects.create(name="2027-1")
        self.application = create_application(self.applicant, self.program, self.call)
        self.application.state = Application.State.SUBMITTED
        self.application.submitted_at = timezone.now()
        self.application.save(update_fields=("state", "submitted_at"))
        self.client.force_login(self.reviewer)

    def test_queue_filters_by_name_program_call_assignment_and_alert(self):
        slot = self.application.requirements.get(code="identity")
        version = DocumentVersion.objects.create(
            requirement=slot, version_number=1, storage_key="x/file.pdf", original_name="file.pdf",
            content_type="application/pdf", byte_size=20, sha256="a" * 64, uploaded_by=self.applicant,
        )
        slot.current_version = version
        slot.save(update_fields=("current_version",))
        PrevalidationResult.objects.create(
            version=version, status=PrevalidationResult.Status.REVIEW,
            alerts=[{"code": "mismatch", "message": "No coincide."}],
        )
        base = reverse("reviewer_inbox")
        self.assertContains(self.client.get(base + "?q=123456"), "Ana Pérez")
        self.assertEqual(self.client.get(base, {"q": "Ana Pérez"}).context["total_count"], 1)
        self.assertEqual(self.client.get(base + f"?program={self.program.pk}").context["total_count"], 1)
        self.assertEqual(self.client.get(base + f"?call={self.call.pk}&alerts=yes").context["total_count"], 1)
        self.assertEqual(self.client.get(base + "?alerts=no").context["total_count"], 0)
        self.assertEqual(self.client.get(base + "?assignment=unassigned").context["total_count"], 1)
        invalid_program = self.client.get(base + "?program=%C2%B2&state=submitted")
        self.assertEqual(invalid_program.context["total_count"], 1)
        self.assertContains(invalid_program, 'aria-invalid="true"')
        Review.objects.create(version=version, reviewer=self.reviewer, decision=Review.Decision.APPROVE, review_round=1)
        row = self.client.get(base).context["applications"][0]
        self.assertEqual(row.approved_count, 1)
        self.application.assigned_reviewer = self.reviewer
        self.application.save(update_fields=("assigned_reviewer",))
        self.assertEqual(self.client.get(base + "?assignment=mine").context["total_count"], 1)

    def test_queue_is_paginated_and_orders_oldest_submission_first(self):
        for index in range(25):
            applicant = User.objects.create_user(email=f"persona{index}@example.test", password="clave-segura-2026")
            Applicant.objects.create(user=applicant, email=applicant.email, identification_type="CC", identification_number=f"{index:010d}", phone="3005550101")
            call = Call.objects.create(name=f"Convocatoria {index}")
            application = create_application(applicant, self.program, call)
            application.state = Application.State.SUBMITTED
            application.submitted_at = timezone.now() - timedelta(days=index + 1)
            application.save(update_fields=("state", "submitted_at"))
        response = self.client.get(reverse("reviewer_inbox"))
        self.assertEqual(response.context["page_obj"].paginator.count, 26)
        self.assertEqual(response.context["page_obj"].paginator.per_page, 25)
        first = response.context["applications"][0]
        self.assertEqual(first.submitted_at.date(), (timezone.now() - timedelta(days=25)).date())
        self.assertEqual(first.applicant.email, "persona24@example.test")

    def test_csv_uses_same_filters_and_neutralizes_spreadsheet_formulas(self):
        self.application.assigned_reviewer = self.reviewer
        self.application.save(update_fields=("assigned_reviewer",))
        response = self.client.get(reverse("reviewer_export"), {"program": self.program.pk, "q": "Ana Pérez"})
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][3], "'=HYPERLINK(\"x\")")
        self.assertEqual(rows[1][-1], self.reviewer.email)

    def test_non_reviewer_cannot_export_and_notification_ids_are_scoped(self):
        other = User.objects.create_user(email="otro@example.test", password="clave-segura-2026")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("reviewer_export")).status_code, 403)
        notice = Notification.objects.create(user=self.applicant, application=self.application, kind="received", title="Recibido", message="Listo")
        self.assertEqual(self.client.post(reverse("notification_read", kwargs={"notification_id": notice.pk})).status_code, 404)

    def test_indicators_are_derived_from_history_and_current_versions(self):
        sent = timezone.now() - timedelta(days=2)
        first_review = sent + timedelta(hours=4)
        approved = sent + timedelta(days=1)
        self.application.submitted_at = sent
        self.application.save(update_fields=("submitted_at",))
        StateHistory.objects.create(application=self.application, from_state=Application.State.SUBMITTED, to_state=Application.State.IN_REVIEW, created_at=first_review)
        StateHistory.objects.create(application=self.application, from_state=Application.State.IN_REVIEW, to_state=Application.State.APPROVED, created_at=approved)
        StateHistory.objects.create(application=self.application, from_state=Application.State.IN_REVIEW, to_state=Application.State.CORRECTION)
        slot = self.application.requirements.get(code="identity")
        version = DocumentVersion.objects.create(
            requirement=slot, version_number=1, storage_key="x/file.pdf", original_name="file.pdf",
            content_type="application/pdf", byte_size=20, sha256="b" * 64, uploaded_by=self.applicant,
        )
        slot.current_version = version
        slot.save(update_fields=("current_version",))
        PrevalidationResult.objects.create(version=version, status=PrevalidationResult.Status.ERROR)
        data = compute_indicators()
        self.assertEqual(data["pending_review_count"], 1)
        self.assertEqual(data["first_review_average"], timedelta(hours=4))
        self.assertEqual(data["approval_average"], timedelta(days=1))
        self.assertEqual(data["correction_percent"], 100)
        self.assertEqual(data["automatic_issue_count"], 1)
        response = self.client.get(reverse("reviewer_indicators"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tiempo hasta primera revisión")

    def test_user_can_read_and_mark_only_their_own_notifications(self):
        notice = Notification.objects.create(
            user=self.applicant, application=self.application, kind=Notification.Kind.RECEIVED,
            title="Expediente recibido", message="Tu expediente se recibió.",
        )
        self.client.force_login(self.applicant)
        response = self.client.get(reverse("notification_list"))
        self.assertContains(response, "Expediente recibido")
        self.assertEqual(
            self.client.post(reverse("notification_read", kwargs={"notification_id": notice.pk})).status_code,
            302,
        )
        notice.refresh_from_db()
        self.assertIsNotNone(notice.read_at)

