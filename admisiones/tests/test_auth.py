from django.test import TestCase, override_settings

from admisiones.forms import RegistrationForm
from admisiones.models import Applicant, PrivacyAcceptance, PrivacyNotice, User


@override_settings(ROOT_URLCONF="config.urls", LOGIN_RATE_LIMIT=5)
class AuthenticationFlowTests(TestCase):
    def setUp(self):
        self.notice = PrivacyNotice.objects.get(active=True)

    def test_registration_fields_follow_the_applicant_task_order(self):
        self.assertEqual(list(RegistrationForm().fields), [
            "first_name", "second_name", "last_name", "second_last_name", "identification_type", "identification_number",
            "email", "phone", "password1", "password2", "privacy_acceptance",
        ])

    def registration_payload(self, **overrides):
        values = {
            "email": "laura@example.test",
            "first_name": "Laura",
            "last_name": "Ríos",
            "identification_type": "CC",
            "identification_number": "1234567890",
            "phone": "3005550101",
            "password1": "Fuerte-2026-Clave!",
            "password2": "Fuerte-2026-Clave!",
            "privacy_acceptance": "on",
            "role": "reviewer",
        }
        return values | overrides

    def test_registration_creates_an_applicant_and_records_notice_version(self):
        response = self.client.post("/registro/", self.registration_payload())
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="laura@example.test")
        self.assertEqual(user.role, User.Role.APPLICANT)
        self.assertEqual(user.get_full_name(), "Laura Ríos")
        self.assertEqual(user.profile.identification_number, "1234567890")
        self.assertTrue(PrivacyAcceptance.objects.filter(user=user, notice=self.notice).exists())
        self.assertTrue(user.check_password("Fuerte-2026-Clave!"))

    def test_registration_keeps_four_names_and_the_account_joins_them(self):
        response = self.client.post("/registro/", self.registration_payload(
            second_name="Sofía", second_last_name="Gómez",
        ))
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="laura@example.test")
        self.assertEqual(user.first_name, "Laura Sofía")
        self.assertEqual(user.last_name, "Ríos Gómez")
        self.assertEqual(user.profile.second_name, "Sofía")
        self.assertEqual(user.profile.second_last_name, "Gómez")

    def test_registration_requires_privacy_acceptance(self):
        response = self.client.post("/registro/", self.registration_payload(privacy_acceptance=""))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="laura@example.test").exists())
        self.assertContains(response, "aceptar el aviso", status_code=200)

    def test_registration_requires_names_and_surnames(self):
        response = self.client.post("/registro/", self.registration_payload(first_name="", last_name=""))
        self.assertEqual(response.status_code, 200)
        self.assertIn("first_name", response.context["form"].errors)
        self.assertIn("last_name", response.context["form"].errors)
        self.assertFalse(User.objects.filter(email="laura@example.test").exists())

    def test_login_limits_repeated_failed_attempts(self):
        statuses = [
            self.client.post("/ingresar/", {"username": "intruso@example.test", "password": "incorrecta"}).status_code
            for _ in range(6)
        ]
        self.assertEqual(statuses[-1], 429)

    def test_one_mistyped_account_does_not_lock_other_users_on_the_same_address(self):
        for _ in range(5):
            self.client.post("/ingresar/", {"username": "uno@example.test", "password": "incorrecta"}, REMOTE_ADDR="10.0.0.9")
        response = self.client.post("/ingresar/", {"username": "dos@example.test", "password": "incorrecta"}, REMOTE_ADDR="10.0.0.9")
        self.assertEqual(response.status_code, 401)

    @override_settings(LOGIN_IP_RATE_LIMIT=3)
    def test_one_address_trying_many_accounts_is_still_blocked(self):
        for index in range(3):
            self.client.post("/ingresar/", {"username": f"cuenta{index}@example.test", "password": "incorrecta"}, REMOTE_ADDR="10.0.0.8")
        response = self.client.post("/ingresar/", {"username": "otra@example.test", "password": "incorrecta"}, REMOTE_ADDR="10.0.0.8")
        self.assertEqual(response.status_code, 429)

    def test_messages_render_as_dismissible_toasts(self):
        user = User.objects.create_user(email="aspirante@example.test", password="Fuerte-2026-Clave!")
        self.client.force_login(user)
        response = self.client.post("/salir/", follow=True)
        self.assertContains(response, 'class="toast toast-success"')
        self.assertContains(response, "data-toast")
        self.assertContains(response, 'class="toast-close"')

    def test_administrator_can_sign_in_from_main_login_with_username(self):
        User.objects.create_superuser(email="admin", password="Clave-admin-de-prueba!")
        response = self.client.post("/ingresar/", {"username": "admin", "password": "Clave-admin-de-prueba!"})
        self.assertRedirects(response, "/administracion/")

    def test_django_admin_login_uses_the_same_attempt_limit(self):
        response = None
        for _ in range(6):
            response = self.client.post("/admin/login/", {"username": "intruso@example.test", "password": "incorrecta"})
        self.assertContains(response, "Demasiados intentos", status_code=200)

    def test_password_actions_are_accessible_without_a_page_footer(self):
        login = self.client.get("/ingresar/")
        self.assertNotContains(login, "<footer")
        self.assertContains(login, '<a href="/contrasena/recuperar/">Olvidé mi contraseña</a>', html=True)
        self.assertEqual(self.client.get("/contrasena/recuperar/").status_code, 200)
        user = User.objects.create_user(email="aspirante@example.test", password="Fuerte-2026-Clave!")
        self.client.force_login(user)
        dashboard = self.client.get("/aspirante/")
        self.assertNotContains(dashboard, "<footer")
        self.assertContains(dashboard, 'href="/perfil/"')
        self.assertEqual(self.client.get("/contrasena/cambiar/").status_code, 200)

    def test_profile_is_private_and_links_to_password_change(self):
        self.assertRedirects(self.client.get("/perfil/"), "/ingresar/?next=/perfil/")
        user = User.objects.create_user(email="perfil@example.test", password="Fuerte-2026-Clave!", role=User.Role.APPLICANT)
        Applicant.objects.create(user=user, first_name="Ana", last_name="Acosta", email=user.email, identification_type="CC", identification_number="55443322", phone="3000000009")
        self.client.force_login(user)
        response = self.client.get("/perfil/")
        self.assertContains(response, "55443322")
        self.assertContains(response, '<a class="button button-secondary" href="/contrasena/cambiar/">Cambiar contraseña</a>', html=True)

    def test_applicant_cannot_access_django_admin(self):
        user = User.objects.create_user(email="aspirante@example.test", password="Fuerte-2026-Clave!")
        self.client.force_login(user)
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_administrator_can_open_native_django_admin(self):
        user = User.objects.create_superuser(email="admin@example.test", password="Fuerte-2026-Clave!")
        self.client.force_login(user)
        response = self.client.get("/admin/")
        self.assertContains(response, "Programas")
        self.assertContains(response, "Convocatorias")
        self.assertContains(response, "Requisitos documentales")
        self.assertContains(
            self.client.get("/admin/admisiones/requirementdefinition/"),
            "Los cambios en los requisitos se aplican a las solicitudes nuevas.",
        )
