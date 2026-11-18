import io
import tempfile
from pathlib import Path

import pymupdf
from django.core.management import call_command
from django.core.files.storage import FileSystemStorage
from django.test import TestCase, override_settings

from admisiones.models import Application, DocumentVersion, EmailOutbox, Notification, User


class DemoDataCommandTests(TestCase):
    def test_command_is_idempotent_and_creates_only_marked_synthetic_files(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(PRIVATE_MEDIA_ROOT=Path(directory)):
            call_command("load_demo_data", stdout=io.StringIO())
            call_command("load_demo_data", stdout=io.StringIO())
            self.assertEqual(Application.objects.count(), 2)
            self.assertEqual(DocumentVersion.objects.count(), 6)
            self.assertEqual(Notification.objects.count(), 3)
            self.assertEqual(EmailOutbox.objects.count(), 3)
            self.assertEqual(
                set(Application.objects.values_list("state", flat=True)),
                {Application.State.SUBMITTED, Application.State.CORRECTION},
            )
            storage = FileSystemStorage(location=Path(directory))
            for version in DocumentVersion.objects.all():
                with storage.open(version.storage_key, "rb") as file:
                    with pymupdf.open(stream=file.read(), filetype="pdf") as document:
                        self.assertIn("SINTET", document[0].get_text())
        applicant = User.objects.get(email="aspirante.demo@example.test")
        reviewer = User.objects.get(email="revisor.demo@example.test")
        self.assertTrue(applicant.check_password("Demo-Aspirante-2026!"))
        self.assertTrue(reviewer.check_password("Demo-Revisor-2026!"))

