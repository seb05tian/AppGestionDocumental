import time

from django.core.management.base import BaseCommand, CommandError

from admisiones.services.auth import purge_login_attempts
from admisiones.services.outbox import process_next_email, recover_stale_emails
from admisiones.services.processing import process_next_job, recover_stale_jobs


class Command(BaseCommand):
    help = "Procesa trabajos OCR y correos pendientes."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Recupera trabajos y procesa una tarea de cada cola.")
        parser.add_argument("--poll-interval", type=float, default=2.0, help="Segundos entre consultas cuando no hay trabajo.")

    def handle(self, *args, **options):
        interval = options["poll_interval"]
        if interval < 0.2 or interval > 60:
            raise CommandError("poll-interval debe estar entre 0.2 y 60 segundos.")
        while True:
            recovered_jobs = recover_stale_jobs()
            recovered_emails = recover_stale_emails()
            purge_login_attempts()
            did_work = process_next_job()
            did_work = process_next_email() or did_work
            if options["once"]:
                self.stdout.write(f"Trabajos recuperados: OCR {recovered_jobs}, correo {recovered_emails}.")
                return
            if not did_work:
                time.sleep(interval)
