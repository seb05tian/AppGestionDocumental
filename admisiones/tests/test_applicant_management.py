from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from admisiones.models import Applicant, Application, Call, Program, User

DIALOG = {"HTTP_X_MANAGEMENT_DIALOG": "1"}
LIST_URL = "/admisiones/aspirantes/"


class ApplicantManagementTests(TestCase):
    def setUp(self):
        self.administrator = User.objects.create_superuser(email="admin-aspirantes", password="Clave-admin-de-prueba!")
        self.client.force_login(self.administrator)
        self.person = Applicant.objects.create(
            first_name="Ana", last_name="Acosta", email="ana@example.test",
            identification_type="CC", identification_number="1001", phone="3000000001",
        )
        self.other = Applicant.objects.create(
            first_name="Luis", last_name="Ruiz", email="luis@example.test",
            identification_type="CC", identification_number="2002", phone="3000000002",
        )
        self.program = Program.objects.create(name="Programa de prueba")
        self.call = Call.objects.create(name="Convocatoria de prueba")

    def url(self, person, action=""):
        return f"/admisiones/aspirantes/{person.public_id}/{action}"

    def test_detail_is_a_fragment_inside_the_dialog_and_a_page_otherwise(self):
        fragment = self.client.get(self.url(self.person), **DIALOG)
        self.assertContains(fragment, "data-management-form")
        self.assertContains(fragment, "1001")
        self.assertNotContains(fragment, "<html")
        self.assertContains(self.client.get(self.url(self.person)), "<html")

    def test_edit_saves_changes_from_the_dialog(self):
        data = {
            "first_name": "Ana María", "last_name": "Acosta", "identification_type": "CC",
            "identification_number": "1001", "email": "ana@example.test", "phone": "3000000001",
        }
        response = self.client.post(self.url(self.person, "editar/"), data, **DIALOG)
        self.assertEqual(response.json(), {"location": LIST_URL})
        self.person.refresh_from_db()
        self.assertEqual(self.person.first_name, "Ana María")

    def test_edit_refuses_an_identification_that_belongs_to_someone_else(self):
        data = {
            "first_name": "Ana", "last_name": "Acosta", "identification_type": "CC",
            "identification_number": "2002", "email": "ana@example.test", "phone": "3000000001",
        }
        response = self.client.post(self.url(self.person, "editar/"), data, **DIALOG)
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "Otra persona ya tiene esa identificación.", status_code=422)
        self.person.refresh_from_db()
        self.assertEqual(self.person.identification_number, "1001")

    def test_applicant_without_applications_is_deleted_after_confirmation(self):
        response = self.client.post(self.url(self.other, "eliminar/"), **DIALOG)
        self.assertEqual(response.json(), {"location": LIST_URL})
        self.assertFalse(Applicant.objects.filter(pk=self.other.pk).exists())

    def test_applicant_with_applications_is_kept_and_the_reason_is_shown(self):
        Application.objects.create(applicant=self.person, created_by=self.administrator, program=self.program, call=self.call)
        response = self.client.post(self.url(self.person, "eliminar/"), **DIALOG)
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "tiene expedientes", status_code=422)
        self.assertTrue(Applicant.objects.filter(pk=self.person.pk).exists())

    def test_edit_saves_second_names_and_search_finds_them(self):
        data = {
            "first_name": "Ana", "second_name": "María", "last_name": "Acosta", "second_last_name": "Gómez",
            "identification_type": "CC", "identification_number": "1001", "email": "ana@example.test", "phone": "3000000001",
        }
        self.client.post(self.url(self.person, "editar/"), data, **DIALOG)
        self.person.refresh_from_db()
        self.assertEqual(self.person.second_name, "María")
        self.assertEqual(self.person.second_last_name, "Gómez")
        found = self.client.get(LIST_URL, {"q": "Acosta Gómez"}).context["people"]
        self.assertEqual(list(found), [self.person])


class ApplicantImportDialogTests(TestCase):
    IMPORT_URL = "/admisiones/aspirantes/importar/"

    def setUp(self):
        self.administrator = User.objects.create_superuser(email="admin-importar", password="Clave-admin-de-prueba!")
        self.client.force_login(self.administrator)
        self.program = Program.objects.create(name="Programa importado")
        self.call = Call.objects.create(name="Convocatoria importada")

    def test_import_opens_as_a_step_inside_the_dialog(self):
        response = self.client.get(self.IMPORT_URL, **DIALOG)
        self.assertContains(response, "data-management-form")
        self.assertNotContains(response, "<html")

    def test_invalid_upload_is_answered_as_unprocessable_inside_the_dialog(self):
        response = self.client.post(self.IMPORT_URL, {"program": self.program.pk, "call": self.call.pk}, **DIALOG)
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "data-management-form", status_code=422)

    def test_preview_replaces_the_step_inside_the_dialog(self):
        csv = (
            "primer_nombre,segundo_nombre,primer_apellido,segundo_apellido,tipo_identificacion,numero_identificacion,correo,telefono\n"
            "Eva,,Ríos,,CC,00998877,eva@example.test,3000000003\n"
        ).encode("utf-8")
        response = self.client.post(self.IMPORT_URL, {
            "program": self.program.pk, "call": self.call.pk,
            "file": SimpleUploadedFile("aspirantes.csv", csv, content_type="text/csv"),
        }, **DIALOG)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirmar importación")
        self.assertContains(response, "eva@example.test")
        self.assertNotContains(response, "<html")

    def test_sidebar_has_no_import_option(self):
        self.assertNotContains(self.client.get("/admisiones/"), 'href="/admisiones/aspirantes/importar/"')
