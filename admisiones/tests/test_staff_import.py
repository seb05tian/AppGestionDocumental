import io

from openpyxl import Workbook, load_workbook
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from admisiones.models import Applicant, Application, Call, Program, RequirementDefinition, User


IMPORT_URL = "/admisiones/aspirantes/importar/"
TEMPLATE_URL = "/admisiones/aspirantes/plantilla.xlsx"
HEADER = "primer_nombre,segundo_nombre,primer_apellido,segundo_apellido,tipo_identificacion,numero_identificacion,correo,telefono"
ANA = "Ana,,López,,CC,00123456,ana@example.test,03005550101"
LUIS = "Luis,,Pérez,,CC,00123457,luis@example.test,03005550102"


class StaffImportTests(TestCase):
    def setUp(self):
        self.reviewer = User.objects.create_user(email="revision@example.test", role=User.Role.REVIEWER)
        self.program = Program.objects.create(name="Medicina")
        self.call = Call.objects.create(name="2027-1")
        self.client.force_login(self.reviewer)

    def preview(self, *rows):
        content = "\n".join((HEADER, *rows)).encode("utf-8-sig")
        response = self.client.post(IMPORT_URL, {
            "action": "preview", "program": self.program.pk, "call": self.call.pk,
            "file": SimpleUploadedFile("aspirantes.csv", content, content_type="text/csv"),
        })
        self.assertEqual(response.status_code, 200)
        return response

    def confirm(self, preview):
        return self.client.post(IMPORT_URL, {"action": "confirm", "nonce": preview.context["nonce"]})

    def test_mixed_rows_preserve_initial_zeros_and_reimport_without_duplicates(self):
        invalid_email = "Eva,,Ríos,,CC,00123458,correo-incorrecto,03005550103"
        preview = self.preview(ANA, ANA, invalid_email, LUIS)
        self.assertEqual(Application.objects.count(), 0)
        self.assertEqual([(row["number"], row["status"]) for row in preview.context["rows"]], [
            (2, "valid"), (3, "error"), (4, "error"), (5, "valid"),
        ])
        result = self.confirm(preview)
        self.assertContains(result, "Importación completada")
        self.assertEqual(result.context["counts"]["created"], 2)
        self.assertEqual(result.context["counts"]["people_created"], 2)
        self.assertEqual(result.context["counts"]["existing"], 0)
        self.assertEqual(result.context["counts"]["errors"], 2)
        self.assertEqual(set(Application.objects.values_list(
            "applicant__identification_number", "applicant__phone",
        )), {("00123456", "03005550101"), ("00123457", "03005550102")})
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(self.confirm(preview).status_code, 400)
        result = self.confirm(self.preview(ANA, ANA, invalid_email, LUIS))
        self.assertEqual(result.context["counts"]["created"], 0)
        self.assertEqual(result.context["counts"]["existing"], 2)
        self.assertEqual(result.context["counts"]["people_existing"], 2)
        self.assertEqual(Application.objects.count(), 2)

    def test_confirmation_detects_conflict_created_after_preview(self):
        preview = self.preview(ANA)
        person = Applicant.objects.create(
            first_name="Ana", last_name="López", identification_type="CC",
            identification_number="00123456", email="previous@example.test", phone="03005550109",
        )
        result = self.confirm(preview)
        self.assertEqual(result.context["counts"]["errors"], 1)
        self.assertEqual(Application.objects.count(), 0)
        person.refresh_from_db()
        self.assertEqual(person.email, "previous@example.test")
        self.assertEqual(person.phone, "03005550109")

    def test_confirmation_uses_current_call_availability(self):
        preview = self.preview(ANA)
        self.call.active = False
        self.call.save(update_fields=("active",))
        result = self.confirm(preview)
        self.assertContains(result, "convocatoria ya no está disponible", status_code=400)
        self.assertEqual(Applicant.objects.count(), 0)
        self.assertEqual(Application.objects.count(), 0)

    def test_preview_rejects_a_call_without_active_requirements(self):
        RequirementDefinition.objects.create(call=self.call, code="identity", name="Identidad", active=False)
        response = self.client.post(IMPORT_URL, {
            "program": self.program.pk, "call": self.call.pk,
            "file": SimpleUploadedFile("aspirantes.csv", f"{HEADER}\n{ANA}".encode("utf-8")),
        })
        self.assertContains(response, "todavía no tiene requisitos", status_code=400)
        self.assertIn("call", response.context["form"].errors)

    def test_preview_identifies_invalid_identification_and_phone_by_row(self):
        preview = self.preview(ANA, "Luis,,Pérez,,CC,#!?,luis@example.test,sin teléfono")
        first, second = preview.context["rows"]
        self.assertEqual(first["status"], "valid")
        self.assertEqual(second["status"], "error")
        self.assertIn("Número de identificación", second["message"])
        self.assertIn("Teléfono", second["message"])

    def test_template_download_and_invalid_columns(self):
        template = self.client.get(TEMPLATE_URL)
        self.assertEqual(template.status_code, 200)
        self.assertIn("attachment", template["Content-Disposition"])
        sheet = load_workbook(io.BytesIO(template.content))["Aspirantes"]
        self.assertEqual([cell.value for cell in sheet[1]], HEADER.split(","))
        self.assertEqual(sheet["A1"].fill.fgColor.rgb[-6:], "000000")
        self.assertEqual(sheet["A1"].font.color.rgb[-6:], "FFFFFF")
        response = self.client.post(IMPORT_URL, {
            "action": "preview", "program": self.program.pk, "call": self.call.pk,
            "file": SimpleUploadedFile("aspirantes.csv", b"nombre,identificacion\nAna,00123456"),
        })
        self.assertContains(response, "columnas", status_code=400)
        self.assertEqual(Application.objects.count(), 0)

    def test_applicant_cannot_import_aspirants(self):
        self.client.force_login(User.objects.create_user(email="persona@example.test", role=User.Role.APPLICANT))
        self.assertEqual(self.client.get(IMPORT_URL).status_code, 403)
        self.assertEqual(self.client.get(TEMPLATE_URL).status_code, 403)


class ExcelImportTests(TestCase):
    def test_excel_upload_is_previewed_like_csv_and_keeps_leading_zeros(self):
        from admisiones.import_views import _template_workbook
        workbook = _template_workbook()
        workbook["Aspirantes"].append(["Eva", "", "Ríos", "", "CC", "00998877", "eva@example.test", "3000000003"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        program = Program.objects.create(name="Programa Excel")
        call = Call.objects.create(name="Convocatoria Excel")
        administrator = User.objects.create_superuser(email="admin-excel", password="Clave-admin-de-prueba!")
        self.client.force_login(administrator)
        response = self.client.post(IMPORT_URL, {
            "action": "preview", "program": program.pk, "call": call.pk,
            "file": SimpleUploadedFile("aspirantes.xlsx", buffer.getvalue()),
        })
        row = response.context["rows"][0]
        self.assertEqual(row["status"], "valid")
        self.assertEqual(row["data"]["identification_number"], "00998877")
