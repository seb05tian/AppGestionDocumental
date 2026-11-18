from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from admisiones.models import Applicant, Call, EmailOutbox, Notification, Program, User
from admisiones.services.outbox import process_next_email, recover_stale_emails
from admisiones.services.workflow import create_application


class EmailOutboxTests(TestCase):
    def setUp(self):
        self.applicant = User.objects.create_user(email="aspirante@example.test", password="clave-segura-2026")
        Applicant.objects.create(user=self.applicant, email=self.applicant.email)
        application = create_application(
            self.applicant, Program.objects.create(name="Sistemas"), Call.objects.create(name="2027-1"),
        )
        self.notification = Notification.objects.create(
            user=self.applicant,
            application=application,
            kind=Notification.Kind.RECEIVED,
            title="Expediente recibido",
            message="Tu expediente se recibió.",
        )
        self.outbox = EmailOutbox.objects.create(notification=self.notification, recipient=self.applicant.email)

    def test_successful_send_is_recorded_once(self):
        with patch("admisiones.services.outbox.send_mail", return_value=1) as send:
            self.assertTrue(process_next_email())
            self.assertFalse(process_next_email())
        self.outbox.refresh_from_db()
        self.assertEqual(self.outbox.status, EmailOutbox.Status.SENT)
        self.assertEqual(self.outbox.attempts, 1)
        self.assertIsNotNone(self.outbox.sent_at)
        send.assert_called_once()

    def test_failure_is_queued_for_retry_then_fails_without_blocking_notification(self):
        with override_settings(EMAIL_MAX_ATTEMPTS=2):
            with patch("admisiones.services.outbox.send_mail", side_effect=OSError("SMTP secret")):
                process_next_email()
                self.outbox.refresh_from_db()
                self.assertEqual(self.outbox.status, EmailOutbox.Status.QUEUED)
                self.assertNotIn("SMTP secret", self.outbox.last_error)
                self.outbox.available_at = timezone.now()
                self.outbox.save(update_fields=("available_at",))
                process_next_email()
        self.outbox.refresh_from_db()
        self.assertEqual(self.outbox.status, EmailOutbox.Status.FAILED)
        self.assertEqual(self.notification.title, "Expediente recibido")
        self.assertEqual(self.notification.user.notifications.count(), 1)

    def test_interrupted_send_is_recovered(self):
        self.outbox.status = EmailOutbox.Status.SENDING
        self.outbox.attempts = 1
        self.outbox.locked_at = timezone.now() - timedelta(minutes=10)
        self.outbox.save(update_fields=("status", "attempts", "locked_at"))
        self.assertEqual(recover_stale_emails(), 1)
        self.outbox.refresh_from_db()
        self.assertEqual(self.outbox.status, EmailOutbox.Status.QUEUED)
        self.assertIsNone(self.outbox.locked_at)

