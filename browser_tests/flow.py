import re
import tempfile
from pathlib import Path

import pymupdf
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings
from playwright.sync_api import expect, sync_playwright

from admisiones.models import Call, Program, User


IDENTITY = "Documento de identidad"
SABER = "Resultado de pruebas Saber 11"
GRADUATION = "Acta de grado o diploma"


class AdmissionsBrowserFlowTests(StaticLiveServerTestCase):
    # Restore the default requirements and privacy notice seeded by the migrations.
    serialized_rollback = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.files_dir = tempfile.TemporaryDirectory()
        cls.pdf_paths = []
        for name, text in (
            ("identity", "DOCUMENTO DE IDENTIDAD 1234567890"),
            ("saber11", "RESULTADO SABER 11 1234567890"),
            ("graduation", "ACTA DE GRADO 1234567890"),
            ("identity-replacement", "DOCUMENTO DE IDENTIDAD 1234567890 REEMPLAZADO"),
            ("saber11-replacement", "RESULTADO SABER 11 1234567890 REEMPLAZADO"),
            ("identity-second-applicant", "DOCUMENTO DE IDENTIDAD 0012345679"),
        ):
            document = pymupdf.open()
            document.new_page().insert_text((60, 80), f"DOCUMENTO SINTETICO - {text}")
            path = Path(cls.files_dir.name, f"{name}.pdf")
            document.save(path)
            document.close()
            cls.pdf_paths.append(path)

    @classmethod
    def tearDownClass(cls):
        cls.files_dir.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.private_dir = tempfile.TemporaryDirectory()
        self.override = override_settings(PRIVATE_MEDIA_ROOT=Path(self.private_dir.name))
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(self.private_dir.cleanup)
        self.program = Program.objects.create(name="Ingeniería de Sistemas")
        self.call = Call.objects.create(name="Convocatoria 2027-1")
        self.reviewer = User.objects.create_user(
            email="revisor@example.test", password="Fuerte-2026-Clave!", first_name="Revisor", last_name="Prueba",
            role=User.Role.REVIEWER,
        )
        self.administrator = User.objects.create_superuser(email="admin", password="Fuerte-2026-Clave!")

    def sign_in(self, page, user, destination):
        page.set_default_timeout(8000)
        page.goto(f"{self.live_server_url}/ingresar/")
        page.get_by_label("Correo electrónico o usuario:", exact=True).fill(user.email)
        page.locator('input[name="password"]').fill("Fuerte-2026-Clave!")
        page.get_by_role("button", name="Ingresar").click()
        page.wait_for_url(f"**/{destination}/")

    def staff_menu(self, page):
        return page.get_by_role("navigation", name="Menú de trabajo", exact=True)

    def select_program_and_call(self, page):
        page.get_by_label("Programa:", exact=True).select_option(label=self.program.name)
        page.get_by_label("Convocatoria:", exact=True).select_option(label=self.call.name)

    def register_applicant(self, page, *, first_name, last_name, email, identification_number):
        page.goto(f"{self.live_server_url}/registro/")
        page.locator('input[name="first_name"]').fill(first_name)
        page.locator('input[name="last_name"]').fill(last_name)
        page.locator('input[name="email"]').fill(email)
        page.locator('select[name="identification_type"]').select_option("CC")
        page.locator('input[name="identification_number"]').fill(identification_number)
        page.locator('input[name="phone"]').fill("3005550101")
        page.locator('input[name="password1"]').fill("Fuerte-2026-Clave!")
        page.locator('input[name="password2"]').fill("Fuerte-2026-Clave!")
        page.locator('input[name="privacy_acceptance"]').check()
        page.get_by_role("button", name="Crear cuenta").click()
        page.wait_for_url("**/aspirante/")

    def register_from_admissions(self, page, *, first_name, last_name, email, identification_number):
        page.get_by_label("Primer nombre", exact=True).fill(first_name)
        page.get_by_label("Primer apellido", exact=True).fill(last_name)
        page.get_by_label("Tipo de identificación:", exact=True).select_option("CC")
        page.get_by_label("Número de identificación:", exact=True).fill(identification_number)
        page.get_by_label("Correo electrónico:", exact=True).fill(email)
        page.get_by_label("Teléfono:", exact=True).fill("03005550101")
        self.select_program_and_call(page)
        page.get_by_role("button", name="Crear expediente", exact=True).click()

    def document(self, page, name, selector=".review-document"):
        heading = page.get_by_role("heading", name=re.compile(rf"^{re.escape(name)}(?:\s*(?:\*|Obligatorio|Opcional))?$"), level=3)
        return page.locator(selector).filter(has=heading)

    def upload_from_admissions(self, page, name, path, *, replace=False):
        item = self.document(page, name)
        item.locator('input[type="file"]').set_input_files(str(path))
        item.get_by_role("button", name="Reemplazar documento" if replace else "Cargar documento", exact=True).click()
        expect(item.locator('iframe[title]').first).to_have_attribute("title", f"Vista previa de {path.name}")
        return item.get_by_role("link", name="Descargar", exact=True).get_attribute("href")

    def mark_correction(self, page, document_name, reason):
        item = self.document(page, document_name)
        item.locator('textarea[name="reason"]').fill(reason)
        item.get_by_role("button", name="Marcar para corrección", exact=True).click()
        expect(page.locator(".review-heading .status-in_review")).to_be_visible()

    def test_administrator_creates_imports_and_reviews_records_without_portal_accounts(self):
        first = dict(first_name="Laura", last_name="Ríos", email="laura@example.test", identification_number="1234567890")
        second = dict(first_name="Mateo", last_name="López", email="mateo@example.test", identification_number="0012345679")
        csv_content = (
            "primer_nombre,segundo_nombre,primer_apellido,segundo_apellido,tipo_identificacion,numero_identificacion,correo,telefono\n"
            "Camila,,Ortiz,,CC,0012345680,camila@example.test,03005550102\n"
            "Diego,,Acosta,,CC,0012345681,correo-invalido,03005550103\n"
            "Laura,,Ríos,,CC,1234567890,laura@example.test,03005550101\n"
            "Camila,,Ortiz,,CC,0012345680,camila@example.test,03005550102\n"
        )
        csv_upload = {"name": "aspirantes.csv", "mimeType": "text/csv", "buffer": csv_content.encode("utf-8")}

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, ignore_default_args=["--disable-back-forward-cache"])
            try:
                reviewer = browser.new_page(viewport={"width": 1280, "height": 900})
                self.sign_in(reviewer, self.administrator, "administracion")
                expect(reviewer.get_by_role("heading", name="Panel de administración", level=1)).to_be_visible()
                new_applicant = reviewer.locator(".page-heading").get_by_role("link", name="Nuevo aspirante", exact=True)
                expect(new_applicant).to_be_visible()
                new_applicant.click()
                expect(reviewer.get_by_role("dialog", name="Nuevo aspirante", exact=True)).to_be_visible()
                expect(reviewer).to_have_url(f"{self.live_server_url}/administracion/")
                self.register_from_admissions(reviewer, **first)
                reviewer.wait_for_url("**/admisiones/expedientes/*/")
                first_url = reviewer.url
                reviewer.go_back()
                expect(reviewer).to_have_url(f"{self.live_server_url}/administracion/")
                expect(reviewer.get_by_role("dialog", name="Nuevo aspirante", exact=True)).not_to_be_visible()
                reviewer.go_forward()
                expect(reviewer).to_have_url(first_url)
                expect(reviewer.get_by_role("heading", name="Laura Ríos", exact=True)).to_be_visible()
                expect(reviewer.locator(".review-heading .status-draft")).to_be_visible()
                first_file = self.upload_from_admissions(reviewer, IDENTITY, self.pdf_paths[0])

                self.staff_menu(reviewer).get_by_role("link", name="Nuevo aspirante", exact=True).click()
                self.register_from_admissions(reviewer, **second)
                reviewer.wait_for_url("**/admisiones/expedientes/*/")
                second_url = reviewer.url
                self.assertNotEqual(first_url, second_url)
                expect(reviewer.get_by_role("heading", name="Mateo López", exact=True)).to_be_visible()
                expect(reviewer.get_by_text("0012345679", exact=False).first).to_be_visible()
                second_file = self.upload_from_admissions(reviewer, IDENTITY, self.pdf_paths[5])
                self.assertNotEqual(first_file, second_file)

                self.staff_menu(reviewer).get_by_role("link", name="Nuevo aspirante", exact=True).click()
                self.register_from_admissions(reviewer, **first)
                first_path = first_url.removeprefix(self.live_server_url)
                duplicate = reviewer.get_by_role("link", name="Ver expediente existente", exact=True)
                expect(duplicate).to_be_visible()
                expect(duplicate).to_have_attribute("href", first_path)
                duplicate.click()
                expect(reviewer).to_have_url(first_url)
                expect(self.document(reviewer, IDENTITY).locator("iframe")).to_have_attribute("title", "Vista previa de identity.pdf")

                other = browser.new_page()
                self.register_applicant(other, first_name="Olga", last_name="Mora", email="olga@example.test", identification_number="0012345689")
                self.assertEqual(other.request.get(f"{self.live_server_url}{first_file}").status, 404)
                self.assertEqual(other.request.get(f"{self.live_server_url}{second_file}").status, 404)
                self.assertEqual(other.request.get(first_url).status, 403)
                for file, path in ((first_file, self.pdf_paths[0]), (second_file, self.pdf_paths[5])):
                    downloaded = reviewer.request.get(f"{self.live_server_url}{file}")
                    self.assertEqual(downloaded.status, 200)
                    self.assertEqual(downloaded.body(), path.read_bytes())

                self.staff_menu(reviewer).get_by_role("link", name="Importar aspirantes", exact=True).click()
                for import_number in range(2):
                    self.select_program_and_call(reviewer)
                    reviewer.get_by_label("Archivo CSV:", exact=True).set_input_files(csv_upload)
                    reviewer.get_by_role("button", name="Vista previa", exact=True).click()
                    expect(reviewer.get_by_role("heading", name="Vista previa", exact=True)).to_be_visible()
                    expect(reviewer.get_by_text("Error", exact=True).first).to_be_visible()
                    expect(reviewer.get_by_text("Existente", exact=True).first).to_be_visible()
                    if import_number == 0:
                        expect(reviewer.get_by_text("Válida", exact=True).first).to_be_visible()
                    reviewer.get_by_role("button", name="Confirmar importación", exact=True).click()
                    expect(reviewer.get_by_role("heading", name="Importación completada", exact=True)).to_be_visible()
                    counts = reviewer.locator("#import-counts")
                    expect(counts.locator('dt:text-is("Expedientes creados") + dd')).to_have_text("1" if import_number == 0 else "0")
                    expect(counts.locator('dt:text-is("Expedientes existentes") + dd')).to_have_text("1" if import_number == 0 else "2")
                    expect(counts.locator('dt:text-is("Filas con errores") + dd')).to_have_text("2")
                    if import_number == 0:
                        reviewer.get_by_role("link", name="Importar otro archivo", exact=True).click()

                reviewer.goto(first_url)
                self.upload_from_admissions(reviewer, SABER, self.pdf_paths[1])
                self.upload_from_admissions(reviewer, GRADUATION, self.pdf_paths[2])
                reviewer.get_by_role("button", name="Enviar a revisión", exact=True).click()
                expect(reviewer.locator(".review-heading .status-submitted")).to_be_visible()
                reviewer.get_by_role("button", name="Asignarme expediente", exact=True).click()
                expect(reviewer.locator(".review-heading .status-in_review")).to_be_visible()
                self.mark_correction(reviewer, IDENTITY, "Presenta la identificación completa.")
                self.mark_correction(reviewer, SABER, "Adjunta el resultado completo de las pruebas.")
                graduation = self.document(reviewer, GRADUATION)
                graduation.get_by_role("button", name="Aprobar documento", exact=True).click()
                expect(graduation.locator(".status-approved")).to_be_visible()
                reviewer.get_by_role("button", name="Enviar solicitud de correcciones", exact=True).click()
                expect(reviewer.locator(".review-heading .status-correction")).to_be_visible()

                self.upload_from_admissions(reviewer, IDENTITY, self.pdf_paths[3], replace=True)
                self.upload_from_admissions(reviewer, SABER, self.pdf_paths[4], replace=True)
                reviewer.get_by_role("button", name="Enviar a revisión", exact=True).click()
                reviewer.get_by_role("button", name="Continuar revisión", exact=True).click()
                expect(graduation.locator(".status-approved")).to_be_visible()
                for name in (IDENTITY, SABER):
                    item = self.document(reviewer, name)
                    expect(item.locator(".status-pending_review")).to_be_visible()
                    item.get_by_role("button", name="Aprobar documento", exact=True).click()
                    expect(item.locator(".status-approved")).to_be_visible()
                reviewer.get_by_role("button", name="Aprobar documentación", exact=True).click()
                expect(reviewer.locator(".review-heading .status-approved")).to_be_visible()

                reviewer.set_viewport_size({"width": 390, "height": 844})
                reviewer.get_by_role("button", name="Abrir menú de trabajo", exact=True).press("Enter")
                self.staff_menu(reviewer).get_by_role("link", name="Aspirantes", exact=True).click()
                expect(reviewer.get_by_role("heading", name="Aspirantes", exact=True)).to_be_visible()
                expect(reviewer.get_by_role("rowheader", name="Laura Ríos", exact=True)).to_be_visible()
                self.assertLessEqual(reviewer.evaluate("document.documentElement.scrollWidth"), 390)
            finally:
                browser.close()
        self.assertFalse(User.objects.filter(email__in=[first["email"], second["email"]]).exists())

    def test_sidebar_collapses_filters_and_opens_as_a_mobile_drawer(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
                self.sign_in(page, self.administrator, "administracion")
                menu = self.staff_menu(page)
                width = menu.bounding_box()["width"]
                page.get_by_role("button", name="Contraer menú", exact=True).click()
                expect(page.get_by_role("button", name="Expandir menú", exact=True)).to_have_attribute("aria-expanded", "false")
                expect(page.locator(".sidebar-slot")).to_have_css("width", "80px")
                self.assertLess(menu.bounding_box()["width"], width)
                programs = menu.get_by_role("link", name="Programas", exact=True)
                programs.click()
                expect(page.get_by_role("button", name="Expandir menú", exact=True)).to_be_visible()
                expect(programs).to_have_attribute("aria-current", "page")
                page.get_by_role("button", name="Expandir menú", exact=True).click()
                page.get_by_role("button", name="Buscar en el menú", exact=True).click()
                search = page.get_by_role("searchbox", name="Buscar opciones", exact=True)
                expect(search).to_be_focused()
                search.fill("constituciones")
                expect(menu.get_by_text("No se encontraron opciones.", exact=True)).to_be_visible()
                search.fill("configuracion")
                expect(menu.get_by_role("link")).to_have_count(3)
                page.get_by_role("button", name="Cerrar búsqueda del menú", exact=True).click()
                expect(menu.get_by_role("link")).to_have_count(8)

                page.set_viewport_size({"width": 390, "height": 844})
                trigger = page.get_by_role("button", name="Abrir menú de trabajo", exact=True)
                expect(menu).not_to_be_visible()
                trigger.press("Enter")
                drawer = page.get_by_role("dialog", name="Menú de trabajo", exact=True)
                expect(drawer).to_be_visible()
                close = drawer.get_by_role("button", name="Cerrar menú de trabajo", exact=True)
                expect(close).to_be_focused()
                close.press("Escape")
                expect(drawer).not_to_be_visible()
                expect(trigger).to_be_focused()
                trigger.click()
                drawer.get_by_role("link", name="Nuevo aspirante", exact=True).click()
                form = page.get_by_role("dialog", name="Nuevo aspirante", exact=True)
                expect(drawer).not_to_be_visible()
                expect(form.get_by_label("Primer nombre", exact=True)).to_be_focused()
                form.get_by_role("button", name="Cancelar", exact=True).click()
                expect(form).not_to_be_visible()
                expect(trigger).to_be_focused()
                trigger.click()
                drawer.get_by_role("link", name="Convocatorias", exact=True).click()
                expect(page.get_by_role("heading", name="Convocatorias", exact=True, level=1)).to_be_visible()
                expect(drawer).not_to_be_visible()
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)
            finally:
                browser.close()

    def test_administrator_configures_catalogs_from_sidebar(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                self.sign_in(page, self.administrator, "administracion")
                menu = self.staff_menu(page)
                page.get_by_label("Estado", exact=True).click()
                page.get_by_role("option", name="Borrador", exact=True).click()
                expect(page).to_have_url(re.compile(r"/administracion/\?.*\bstate=draft\b"))
                page.get_by_role("link", name="Limpiar", exact=True).click()
                expect(page).to_have_url(f"{self.live_server_url}/administracion/")
                for link, action, form_label, value in (
                    ("Programas", "Crear programa", "Nombre del programa:", "Medicina"),
                    ("Convocatorias", "Crear convocatoria", "Nombre de la convocatoria:", "Ingreso 2028-1"),
                ):
                    entry = menu.get_by_role("link", name=link, exact=True)
                    entry.click()
                    expect(entry).to_have_attribute("aria-current", "page")
                    trigger = page.get_by_role("link", name=action, exact=True)
                    expect(page.get_by_role("table", name=link, exact=True)).to_be_visible()
                    trigger.click()
                    modal = page.get_by_role("dialog", name=action, exact=True)
                    expect(modal).to_be_visible()
                    modal.get_by_role("button", name="Guardar cambios", exact=True).click()
                    expect(modal.get_by_label(form_label, exact=True)).to_have_attribute("aria-invalid", "true")
                    modal.get_by_label(form_label, exact=True).press("Escape")
                    expect(modal).not_to_be_visible()
                    expect(trigger).to_be_focused()
                    trigger.click()
                    page.get_by_label(form_label, exact=True).fill(value)
                    page.get_by_role("button", name="Guardar cambios", exact=True).click()
                    expect(page.get_by_role("rowheader", name=value, exact=True)).to_be_visible()

                menu.get_by_role("link", name="Requisitos documentales", exact=True).click()
                expect(page.get_by_text("Los cambios se aplican a expedientes nuevos.", exact=False)).to_be_visible()
                page.get_by_role("link", name="Crear requisito documental", exact=True).click()
                page.get_by_label("Nombre del requisito:", exact=True).fill("Constancia escolar")
                page.get_by_label("Código:", exact=True).fill("constancia")
                page.get_by_label("Convocatoria:", exact=True).select_option(label="Ingreso 2028-1")
                page.get_by_role("checkbox", name="Otro documento", exact=True).check()
                page.get_by_role("button", name="Guardar cambios", exact=True).click()
                page.get_by_role("link", name="Editar Constancia escolar", exact=True).click()
                expect(page.get_by_role("checkbox", name="Otro documento", exact=True)).to_be_checked()
                page.get_by_role("checkbox", name="Activo", exact=False).uncheck()
                page.get_by_role("button", name="Guardar cambios", exact=True).click()
                row = page.get_by_role("row").filter(has=page.get_by_role("rowheader", name="Constancia escolar", exact=False))
                expect(row.get_by_text("Desactivado", exact=True)).to_be_visible()

                menu.get_by_role("link", name="Programas", exact=True).click()
                page.get_by_role("link", name="Crear programa", exact=True).click()
                modal = page.get_by_role("dialog", name="Crear programa", exact=True)
                modal.get_by_label("Nombre del programa:", exact=True).fill("Programa de prueba")
                page.route("**/administracion/programas/nuevo/", lambda route: route.abort())
                save = modal.get_by_role("button", name="Guardar cambios", exact=True)
                save.click()
                expect(modal.get_by_text("No pudimos confirmar el guardado.", exact=False)).to_be_visible()
                expect(save).to_be_disabled()
                modal.get_by_role("button", name="Cancelar", exact=True).click()
                expect(modal).not_to_be_visible()
            finally:
                browser.close()

    def test_queue_filters_follow_viewport_changes(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 390, "height": 844})

                def serve_old_stylesheet(route):
                    response = route.fetch()
                    css = "\n".join(line for line in response.text().splitlines() if ".filter-" not in line)
                    route.fulfill(response=response, body=css)

                page.route(re.compile(r"/static/admisiones/css/app\.css$"), serve_old_stylesheet)
                page.goto(f"{self.live_server_url}/ingresar/")
                page.locator('input[name="username"]').fill("revisor@example.test")
                page.locator('input[name="password"]').fill("Fuerte-2026-Clave!")
                page.get_by_role("button", name="Ingresar").click()
                page.wait_for_url("**/admisiones/")
                filters = page.locator(".filter-disclosure")
                self.assertFalse(filters.evaluate("element => element.open"))
                page.get_by_text("Filtros", exact=True).click()
                expect(filters.locator(".filter-fields")).to_have_css("display", "grid", timeout=1000)
                expect(filters.locator(".filter-search-control svg")).to_have_css("width", "18px", timeout=1000)
                page.get_by_label("Estado", exact=True).click()
                page.get_by_role("option", name="Borrador", exact=True).click()
                expect(filters).to_have_attribute("data-has-selection", "")
                expect(page.get_by_label("Estado", exact=True)).to_have_text("Borrador")
                page.get_by_role("link", name="Limpiar", exact=True).click()
                expect(page).to_have_url(f"{self.live_server_url}/admisiones/")
                page.set_viewport_size({"width": 1280, "height": 900})
                page.wait_for_function("() => document.querySelector('.filter-disclosure').open")
                self.assertTrue(filters.evaluate("element => element.open"))
                state = page.get_by_label("Estado", exact=True)
                expect(state).to_have_css("border-radius", "12px")
                expect(page.get_by_role("searchbox")).to_be_visible()
                state.focus()
                page.set_viewport_size({"width": 390, "height": 844})
                expect(state).to_be_focused()
            finally:
                browser.close()

    def test_applicant_receives_one_correction_notice_and_replaces_both_files(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                applicant_context = browser.new_context(viewport={"width": 1280, "height": 900})
                applicant = applicant_context.new_page()
                applicant.set_default_timeout(5000)
                self.register_applicant(applicant, first_name="Ana", last_name="Ejemplo", email="ana@example.test", identification_number="1234567890")

                applicant.locator(".page-heading").get_by_role("link", name="Crear solicitud", exact=True).click()
                self.select_program_and_call(applicant)
                applicant.get_by_role("button", name="Crear solicitud").click()
                applicant.wait_for_url("**/aspirante/solicitud/**")
                for index in range(3):
                    item = applicant.locator(".document-item").nth(index)
                    item.locator('input[type="file"]').set_input_files(str(self.pdf_paths[index]))
                    item.locator('button[type="submit"]').click()
                    applicant.wait_for_load_state("domcontentloaded")
                applicant.get_by_role("button", name="Enviar expediente").click()
                applicant.wait_for_load_state("domcontentloaded")
                applicant.locator(".page-heading .status-submitted").wait_for()

                reviewer_context = browser.new_context(viewport={"width": 1280, "height": 900})
                reviewer = reviewer_context.new_page()
                self.sign_in(reviewer, self.reviewer, "admisiones")
                reviewer.get_by_role("link", name="Abrir expediente", exact=False).click()
                review_url = reviewer.url
                reviewer.get_by_role("button", name="Asignarme expediente").click()
                identity_reason = "El archivo debe mostrar el documento completo."
                saber_reason = "Adjunta una copia legible de los resultados."
                self.mark_correction(reviewer, IDENTITY, identity_reason)
                self.mark_correction(reviewer, SABER, saber_reason)
                applicant.reload()
                expect(applicant.get_by_text(identity_reason, exact=True)).to_have_count(0)
                expect(applicant.get_by_text(saber_reason, exact=True)).to_have_count(0)
                graduation = self.document(reviewer, GRADUATION)
                graduation.get_by_role("button", name="Aprobar documento", exact=True).click()
                expect(graduation.locator(".status-approved")).to_be_visible()
                reviewer.get_by_role("button", name="Enviar solicitud de correcciones", exact=True).click()
                expect(reviewer.locator(".review-heading .status-correction")).to_be_visible()

                applicant.reload()
                applicant.get_by_text(identity_reason, exact=True).first.wait_for()
                applicant.get_by_text(saber_reason, exact=True).first.wait_for()
                application_url = applicant.url
                applicant.get_by_role("link", name="Notificaciones", exact=False).click()
                correction_notice = applicant.locator(".notification-item").filter(has_text=identity_reason)
                expect(correction_notice).to_have_count(1)
                expect(correction_notice).to_contain_text(saber_reason)
                applicant.goto(application_url)
                for name, replacement in ((IDENTITY, self.pdf_paths[3]), (SABER, self.pdf_paths[4])):
                    document = self.document(applicant, name, ".document-item")
                    document.locator('input[type="file"]').set_input_files(str(replacement))
                    document.locator('button[type="submit"]').click()
                    expect(document.get_by_text(replacement.name, exact=True)).to_be_visible()
                applicant.get_by_role("button", name="Reenviar expediente").click()
                applicant.wait_for_load_state("domcontentloaded")
                applicant.locator(".page-heading .status-submitted").wait_for()

                reviewer.goto(review_url)
                reviewer.get_by_role("button", name="Continuar revisión").click()
                expect(graduation.locator(".status-approved")).to_be_visible()
                for name in (IDENTITY, SABER):
                    pending_document = self.document(reviewer, name)
                    expect(pending_document.locator(".status-pending_review")).to_be_visible()
                    pending_document.get_by_role("button", name="Aprobar documento", exact=True).click()
                    expect(pending_document.locator(".status-approved")).to_be_visible()
                approve_application = reviewer.get_by_role("button", name="Aprobar documentación", exact=True)
                self.assertEqual(approve_application.count(), 1)
                approve_application.wait_for(state="visible")
                approve_application.click()
                reviewer.wait_for_load_state("domcontentloaded")
                reviewer.locator(".review-heading .status-approved").wait_for()

                applicant.reload()
                applicant.locator(".page-heading .status-approved").wait_for()
                applicant.set_viewport_size({"width": 375, "height": 812})
                applicant.reload()
                self.assertLessEqual(
                    applicant.evaluate("document.documentElement.scrollWidth"),
                    applicant.evaluate("document.documentElement.clientWidth"),
                )
                reviewer.set_viewport_size({"width": 375, "height": 812})
                reviewer.reload()
                self.assertLessEqual(
                    reviewer.evaluate("document.documentElement.scrollWidth"),
                    reviewer.evaluate("document.documentElement.clientWidth"),
                )
                reviewer.keyboard.press("Tab")
                self.assertTrue(reviewer.evaluate("document.activeElement.matches('.skip-link:focus-visible')"))
                reviewer.keyboard.press("Enter")
                self.assertEqual(reviewer.evaluate("document.activeElement.id"), "contenido")
                reviewer_context.close()
                applicant_context.close()
            finally:
                browser.close()
