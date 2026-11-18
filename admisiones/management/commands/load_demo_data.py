import pymupdf
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError

from admisiones.models import Applicant, Application, Call, PrivacyAcceptance, PrivacyNotice, Program, User
from admisiones.services.files import store_version
from admisiones.services.state import transition_application
from admisiones.services.workflow import assign_to_self, create_application, review_document, send_corrections


class Command(BaseCommand):
    help = "Crea cuentas y expedientes de demostración con documentos sintéticos."

    def handle(self, *args, **options):
        applicant, applicant_created = User.objects.get_or_create(
            email="aspirante.demo@example.test",
            defaults={"first_name": "Ana", "last_name": "Ejemplo", "role": User.Role.APPLICANT},
        )
        reviewer, reviewer_created = User.objects.get_or_create(
            email="revisor.demo@example.test",
            defaults={"first_name": "Rosa", "last_name": "Revisión", "role": User.Role.REVIEWER},
        )
        for user, password, role, created in (
            (applicant, "Demo-Aspirante-2026!", User.Role.APPLICANT, applicant_created),
            (reviewer, "Demo-Revisor-2026!", User.Role.REVIEWER, reviewer_created),
        ):
            if created:
                user.set_password(password)
                user.save(update_fields=("password",))
            if user.role != role:
                raise CommandError(f"La cuenta reservada {user.email} ya existe con otro rol.")
        Applicant.objects.get_or_create(
            user=applicant,
            defaults={
                "first_name": applicant.first_name, "last_name": applicant.last_name, "email": applicant.email,
                "identification_type": Applicant.IdentificationType.CC, "identification_number": "000000000001", "phone": "3000000000",
            },
        )
        notice = PrivacyNotice.objects.filter(active=True).order_by("-created_at").first()
        if notice:
            PrivacyAcceptance.objects.get_or_create(user=applicant, notice=notice)
        program, _ = Program.objects.get_or_create(name="Ingeniería de demostración")
        calls = []
        for name in ("Convocatoria demostrativa 2027-1", "Convocatoria demostrativa 2027-2"):
            call, _ = Call.objects.get_or_create(name=name, defaults={"active": True})
            calls.append(call)

        self._application(applicant, program, calls[0], "demo")
        second = self._application(applicant, program, calls[1], "correccion")
        if second.state == Application.State.SUBMITTED:
            second = assign_to_self(second, reviewer)
            identity = second.requirements.get(code="identity")
            review_document(identity, identity.current_version_id, reviewer, "correction", reason="Documento sintético de ejemplo: reemplázalo por una imagen legible durante la demostración.")
            for slot in second.requirements.exclude(pk=identity.pk):
                review_document(slot, slot.current_version_id, reviewer, "approve")
            send_corrections(second, reviewer)
        self.stdout.write(self.style.SUCCESS(
            "Datos ficticios listos. Contraseñas iniciales: Demo-Aspirante-2026! y Demo-Revisor-2026! "
            "(las ejecuciones posteriores conservan las contraseñas existentes). No uses estas cuentas fuera de desarrollo."
        ))

    def _application(self, applicant, program, call, kind):
        existing = Application.objects.filter(applicant__user=applicant, program=program, call=call).first()
        if existing:
            return existing
        application = create_application(applicant, program, call)
        for slot in application.requirements.all():
            if slot.code == "identity":
                text = "DOCUMENTO SINTETICO DE DEMOSTRACION - NO VALIDO - CEDULA 000000000001"
            elif slot.code == "saber11":
                text = "RESULTADO SINTETICO SABER 11 - SOLO DEMOSTRACION - 000000000001"
            else:
                text = "ACTA SINTETICA DE GRADO - SOLO DEMOSTRACION - 000000000001"
            document = pymupdf.open()
            page = document.new_page()
            page.insert_text((55, 70), text, fontsize=12)
            page.insert_text((55, 100), "ARCHIVO FICTICIO DE DEMOSTRACION; NO ACREDITA IDENTIDAD NI ESTUDIOS.", fontsize=9)
            data = document.tobytes()
            document.close()
            store_version(
                slot,
                SimpleUploadedFile(f"{kind}-{slot.code}.pdf", data, content_type="application/pdf"),
                applicant,
            )
        transition_application(application, Application.State.SUBMITTED, applicant)
        application.refresh_from_db()
        return application
