from unittest.mock import patch
from urllib.parse import urlsplit

from django.core import mail
from django.test import Client, TestCase, override_settings

from admisiones.models import Applicant, Call, PrivacyAcceptance, PrivacyNotice, Program, User
from admisiones.services.workflow import create_staff_application


@override_settings(
    APP_PUBLIC_URL="https://admisiones.example.test",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    LOGIN_RATE_LIMIT=2,
)
class ClaimTests(TestCase):
    def setUp(self):
        self.person = Applicant.objects.create(
            first_name="Laura", last_name="Ríos", email="laura@example.test", phone="03005550101",
            identification_type="CC", identification_number="0012345678",
        )
        self.password_form = {
            "new_password1": "Clave-de-prueba-2027!",
            "new_password2": "Clave-de-prueba-2027!",
            "privacy_acceptance": "on",
            "notice_id": PrivacyNotice.objects.get(active=True).pk,
        }

    def _link(self):
        response = self.client.post("/vincular/", {"email": "LAURA@example.test"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox[-1].to, ["laura@example.test"])
        url = next(line for line in mail.outbox[-1].body.splitlines() if line.startswith("https://"))
        self.assertEqual(urlsplit(url).netloc, "admisiones.example.test")
        return urlsplit(url).path

    def _application(self):
        reviewer = User.objects.create_user(email="revision@example.test", role=User.Role.REVIEWER)
        return create_staff_application(
            reviewer, {}, Program.objects.create(name="Medicina"), Call.objects.create(name="2027-1"),
            applicant=self.person,
        )

    def test_valid_email_link_creates_account_once_after_password_and_consent(self):
        application = self._application()
        link = self._link()
        self.assertFalse(User.objects.filter(email=self.person.email).exists())
        response = self.client.get(link)
        self.assertRedirects(response, "/vincular/confirmar/")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        self.assertFalse(User.objects.filter(email=self.person.email).exists())

        without_consent = dict(self.password_form, privacy_acceptance="")
        self.client.post("/vincular/confirmar/", without_consent)
        self.assertFalse(User.objects.filter(email=self.person.email).exists())

        response = self.client.post("/vincular/confirmar/", self.password_form)
        self.assertRedirects(response, "/ingresar/")
        self.person.refresh_from_db()
        self.assertEqual(self.person.user.role, User.Role.APPLICANT)
        self.assertTrue(self.person.user.check_password(self.password_form["new_password1"]))
        self.assertTrue(PrivacyAcceptance.objects.filter(user=self.person.user, notice__active=True).exists())

        other_browser = Client()
        other_browser.get(link)
        response = other_browser.post("/vincular/confirmar/", self.password_form)
        self.assertContains(response, "El enlace no está disponible")
        self.assertEqual(User.objects.filter(email=self.person.email).count(), 1)
        self.client.force_login(self.person.user)
        self.assertContains(self.client.get(f"/aspirante/solicitud/{application.public_id}/"), "Medicina")

    def test_existing_applicant_account_must_verify_its_own_contact_before_access(self):
        application = self._application()
        account = User.objects.create_user(email=self.person.email, password="Clave-original-2027!")
        self.person.user = account
        self.person.save(update_fields=("user",))
        self.client.force_login(account)
        path = f"/aspirante/solicitud/{application.public_id}/"
        self.assertEqual(self.client.get(path).status_code, 404)
        link = self._link()

        stranger = Client()
        stranger.force_login(User.objects.create_user(email="otra@example.test"))
        stranger.get(link)
        stranger.post("/vincular/confirmar/", self.password_form)
        self.assertEqual(self.client.get(path).status_code, 404)

        self.client.get(link)
        response = self.client.post("/vincular/confirmar/", self.password_form)
        self.assertRedirects(response, "/aspirante/")
        self.assertContains(self.client.get(path), "Medicina")
        account.refresh_from_db()
        self.assertTrue(account.check_password("Clave-original-2027!"))
        self.assertEqual(User.objects.filter(email=self.person.email).count(), 1)

    def test_account_provisioned_without_profile_can_link_after_email_verification(self):
        application = self._application()
        account = User.objects.create_user(email=self.person.email, password="Clave-original-2027!")
        self.client.force_login(account)
        self.assertContains(self.client.get("/aspirante/"), "Vincular mi expediente")
        link = self._link()
        self.client.get(link)
        self.assertRedirects(self.client.post("/vincular/confirmar/", self.password_form), "/aspirante/")
        self.person.refresh_from_db()
        self.assertEqual(self.person.user, account)
        self.assertContains(self.client.get(f"/aspirante/solicitud/{application.public_id}/"), "Medicina")

    @override_settings(LOGIN_RATE_LIMIT=1)
    def test_unknown_address_and_rate_limit_use_the_same_response(self):
        missing = self.client.post("/vincular/", {"email": "no-existe@example.test"})
        known = self.client.post("/vincular/", {"email": self.person.email})
        limited = self.client.post("/vincular/", {"email": self.person.email})
        for response in (missing, known, limited):
            self.assertContains(response, "Si el correo está registrado")
        self.assertEqual(len(mail.outbox), 1)

    def test_expired_link_cannot_create_an_account(self):
        with patch("django.core.signing.time.time", return_value=1_700_000_000):
            link = self._link()
        self.client.get(link)
        response = self.client.post("/vincular/confirmar/", self.password_form)
        self.assertContains(response, "El enlace no está disponible")
        self.person.refresh_from_db()
        self.assertIsNone(self.person.user_id)

    def test_existing_login_is_never_attached_by_email(self):
        link = self._link()
        existing = User.objects.create_user(email=self.person.email, password="otra-clave-2027!")
        self.client.get(link)
        response = self.client.post("/vincular/confirmar/", self.password_form)
        self.assertContains(response, "El enlace no está disponible")
        self.person.refresh_from_db()
        self.assertIsNone(self.person.user_id)
        self.assertFalse(PrivacyAcceptance.objects.filter(user=existing).exists())

    def test_verified_email_must_still_match_when_finishing(self):
        link = self._link()
        self.client.get(link)
        self.person.email = "otra@example.test"
        self.person.save(update_fields=("email",))
        self.client.post("/vincular/confirmar/", self.password_form)
        self.person.refresh_from_db()
        self.assertIsNone(self.person.user_id)

    def test_claim_requires_an_active_privacy_notice(self):
        link = self._link()
        self.client.get(link)
        PrivacyNotice.objects.update(active=False)
        response = self.client.post("/vincular/confirmar/", self.password_form)
        self.assertContains(response, "aviso de privacidad")
        self.person.refresh_from_db()
        self.assertIsNone(self.person.user_id)

    @override_settings(APP_PUBLIC_URL="", DEBUG=True, ALLOWED_HOSTS=["portal.example.test"])
    def test_unconfigured_origin_never_builds_links_with_a_remote_request_host(self):
        response = self.client.post(
            "/vincular/", {"email": self.person.email}, secure=True, HTTP_HOST="portal.example.test"
        )
        self.assertContains(response, "Si el correo está registrado")
        self.assertEqual(mail.outbox, [])

    def test_invalid_token_does_not_store_a_session(self):
        response = self.client.get("/vincular/no-es-un-token/")
        self.assertRedirects(response, "/vincular/confirmar/", fetch_redirect_response=False)
        self.assertNotIn("sessionid", response.cookies)
