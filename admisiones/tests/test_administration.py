from django.test import Client, TestCase

from admisiones.models import Applicant, Application, Call, Program, RequirementDefinition, User


class AdministrationPanelTests(TestCase):
    def setUp(self):
        self.administrator = User.objects.create_superuser(email="admin", password="Clave-admin-de-prueba!")
        self.client.force_login(self.administrator)

    def test_sidebar_exposes_admissions_and_configuration_in_the_app(self):
        response = self.client.get("/administracion/")
        self.assertContains(response, "Panel de administración")
        for url in (
            "/admisiones/", "/admisiones/aspirantes/", "/admisiones/aspirantes/nuevo/",
            "/administracion/programas/",
            "/administracion/convocatorias/", "/administracion/requisitos/",
        ):
            with self.subTest(url=url):
                self.assertContains(response, f'href="{url}"')
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_catalog_state_and_search_stay_applied_across_pages(self):
        Program.objects.bulk_create([Program(name=f"Ciclo {number:02}", active=False) for number in range(26)])
        Program.objects.create(name="Ciclo activo")
        Program.objects.create(name="Otro programa", active=False)
        url = "/administracion/programas/?q=Ciclo&status=inactive"
        response = self.client.get(url)
        self.assertEqual(response.context["records"].paginator.count, 26)
        self.assertContains(response, 'href="?q=Ciclo&amp;status=inactive&amp;page=2"')
        second_page = self.client.get(url + "&page=2").context["records"]
        self.assertEqual([program.name for program in second_page], ["Ciclo 25"])

    def test_edits_to_requirements_only_affect_new_applications(self):
        self.assertRedirects(self.client.post("/administracion/programas/nuevo/", {
            "name": "Medicina", "active": "on",
        }), "/administracion/programas/")
        self.assertRedirects(self.client.post("/administracion/convocatorias/nuevo/", {
            "name": "2027-1", "active": "on",
        }), "/administracion/convocatorias/")
        program = Program.objects.get(name="Medicina")
        call = Call.objects.get(name="2027-1")
        form = {
            "call": call.pk, "name": "Identidad original", "code": "identity",
            "required": "on", "active": "on",
        }
        self.assertRedirects(self.client.post("/administracion/requisitos/nuevo/", form), "/administracion/requisitos/")
        requirement = RequirementDefinition.objects.get(call=call, code="identity")
        details = {
            "first_name": "Ana", "last_name": "Acosta", "identification_type": "CC", "identification_number": "0011234",
            "email": "ana@example.test", "phone": "03001234567", "program": program.pk, "call": call.pk,
        }
        self.assertEqual(self.client.post("/admisiones/aspirantes/nuevo/", details).status_code, 302)
        first = Application.objects.get(applicant__email="ana@example.test")
        self.assertEqual(first.created_by, self.administrator)
        edit = f"/administracion/requisitos/{requirement.pk}/editar/"
        version = self.client.get(edit).context["record_version"]
        self.assertRedirects(self.client.post(edit, {
            **form, "name": "Identidad actualizada", "required": "", "record_version": version,
        }), "/administracion/requisitos/")
        self.assertEqual(self.client.post("/admisiones/aspirantes/nuevo/", {
            **details, "first_name": "Luis", "email": "luis@example.test", "identification_number": "0011235",
        }).status_code, 302)
        old = first.requirements.get()
        new = Application.objects.get(applicant__email="luis@example.test").requirements.get()
        self.assertEqual((old.name, old.required), ("Identidad original", True))
        self.assertEqual((new.name, new.required), ("Identidad actualizada", False))

    def test_stale_modal_cannot_overwrite_a_catalog_edit(self):
        program = Program.objects.create(name="Medicina")
        url = f"/administracion/programas/{program.pk}/editar/"
        stale_version = self.client.get(url).context["record_version"]
        Program.objects.filter(pk=program.pk).update(active=False)
        response = self.client.post(url, {
            "name": "Medicina general", "active": "on", "record_version": stale_version,
        }, HTTP_X_MANAGEMENT_DIALOG="1")
        self.assertContains(response, "El registro cambió", status_code=422)
        program.refresh_from_db()
        self.assertEqual(program.name, "Medicina")
        self.assertFalse(program.active)

    def test_configuration_is_reserved_for_administrators(self):
        for role in (User.Role.REVIEWER, User.Role.APPLICANT):
            with self.subTest(role=role):
                self.client.force_login(User.objects.create_user(email=f"{role}@example.test", role=role))
                self.assertEqual(self.client.get("/administracion/").status_code, 403)
                self.assertEqual(self.client.get("/administracion/requisitos/").status_code, 403)
                self.assertEqual(self.client.post("/administracion/programas/nuevo/", {"name": "Prohibido"}).status_code, 403)
                self.assertEqual(self.client.get("/administracion/programas/nuevo/", HTTP_X_MANAGEMENT_DIALOG="1").status_code, 403)
        self.assertFalse(Program.objects.filter(name="Prohibido").exists())

    def test_modal_form_requires_csrf_and_serves_a_private_fragment(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.administrator)
        url = "/administracion/programas/nuevo/"
        fragment = client.get(url, HTTP_X_MANAGEMENT_DIALOG="1")
        self.assertContains(fragment, "data-management-form")
        self.assertContains(fragment, 'id="dialog_name"')
        self.assertNotContains(fragment, "<!doctype html>")
        self.assertIn("no-store", fragment["Cache-Control"])
        page = client.get(url)
        self.assertContains(page, "<!doctype html>")
        self.assertContains(page, 'id="id_name"')
        response = client.post(url, {"name": "Sin token"}, HTTP_X_MANAGEMENT_DIALOG="1")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Program.objects.filter(name="Sin token").exists())


class CatalogDeleteTests(TestCase):
    def setUp(self):
        self.administrator = User.objects.create_superuser(email="admin-catalogo", password="Clave-admin-de-prueba!")
        self.client.force_login(self.administrator)

    def test_unused_requirement_is_deleted_only_after_confirmation(self):
        definition = RequirementDefinition.objects.create(code="temporal", name="Temporal")
        url = f"/administracion/requisitos/{definition.pk}/eliminar/"
        self.assertContains(self.client.get(url), "Esta acción no se puede deshacer")
        self.assertTrue(RequirementDefinition.objects.filter(pk=definition.pk).exists())
        self.assertRedirects(self.client.post(url), "/administracion/requisitos/")
        self.assertFalse(RequirementDefinition.objects.filter(pk=definition.pk).exists())

    def test_program_with_applications_is_kept_and_the_user_is_told_why(self):
        program = Program.objects.create(name="Con expedientes")
        call = Call.objects.create(name="Convocatoria con expedientes")
        applicant = Applicant.objects.create(
            email="aspirante-catalogo@example.test", first_name="Ana", last_name="Acosta",
            identification_type="CC", identification_number="99887766", phone="3000000000",
        )
        Application.objects.create(applicant=applicant, created_by=self.administrator, program=program, call=call)
        response = self.client.post(f"/administracion/programas/{program.pk}/eliminar/", follow=True)
        self.assertContains(response, "tiene expedientes asociados")
        self.assertTrue(Program.objects.filter(pk=program.pk).exists())

    def test_reviewers_cannot_delete_catalog_records(self):
        reviewer = User.objects.create_user(email="revisor-catalogo@example.test", password="Fuerte-2026-Clave!", role=User.Role.REVIEWER)
        definition = RequirementDefinition.objects.create(code="protegido", name="Protegido")
        self.client.force_login(reviewer)
        self.assertEqual(self.client.post(f"/administracion/requisitos/{definition.pk}/eliminar/").status_code, 403)
        self.assertTrue(RequirementDefinition.objects.filter(pk=definition.pk).exists())
