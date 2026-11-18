import logging
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.forms import SetPasswordForm
from django.core import signing
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_http_methods

from .models import Applicant, PrivacyAcceptance, PrivacyNotice, User, account_names
from .services.auth import attempt_keys, login_blocked, record_login_attempt


logger = logging.getLogger(__name__)
CLAIM_SALT = "admisiones.portal-claim"
CLAIM_SESSION_KEY = "portal_claim_token"
CLAIM_CONFIRM_PATH = "confirmar"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}


class ClaimRequestForm(forms.Form):
    email = forms.EmailField(label="Correo electrónico", widget=forms.EmailInput(attrs={"autocomplete": "email"}))


class ClaimConfirmForm(SetPasswordForm):
    privacy_acceptance = forms.BooleanField(label="Acepto el aviso de privacidad")


class VerifyContactForm(forms.Form):
    privacy_acceptance = ClaimConfirmForm.base_fields["privacy_acceptance"]


def _public_origin(request):
    configured = getattr(settings, "APP_PUBLIC_URL", "").strip()
    origin = configured or (request.build_absolute_uri("/") if settings.DEBUG else "")
    try:
        parts = urlsplit(origin)
    except ValueError:
        return ""
    local = settings.DEBUG and parts.hostname in LOCAL_HOSTS
    if not ((configured and parts.scheme == "https") or (local and parts.scheme in {"http", "https"})):
        return ""
    if not parts.hostname or parts.username is not None or parts.path not in {"", "/"} or parts.query or parts.fragment:
        return ""
    return origin.rstrip("/")


def _email_digest(email):
    return salted_hmac(CLAIM_SALT, email.strip().lower(), algorithm="sha256").hexdigest()


def _send_claim(request, applicant):
    origin = _public_origin(request)
    if not origin:
        logger.warning("Configura APP_PUBLIC_URL para enviar enlaces de vinculación.")
        return
    token = signing.dumps(
        [str(applicant.public_id), str(applicant.claim_nonce), _email_digest(applicant.email)], salt=CLAIM_SALT
    )
    link = origin + reverse("claim_confirm", kwargs={"token": token})
    try:
        send_mail(
            "Vincula tu acceso al portal de aspirantes",
            f"Abre este enlace para verificar tu correo y acceder a tus expedientes. Caduca en 24 horas.\n\n{link}\n\n"
            "Si no solicitaste el enlace, ignora este mensaje.",
            settings.DEFAULT_FROM_EMAIL, [applicant.email],
        )
    except (OSError, ValueError):
        logger.warning("No se pudo enviar un enlace de vinculación.")


@never_cache
@require_http_methods(["GET", "POST"])
def claim_request(request):
    if request.user.is_authenticated and request.user.role != User.Role.APPLICANT:
        return redirect("home")
    form = ClaimRequestForm(request.POST if request.method == "POST" else None)
    if form.is_valid():
        email = form.cleaned_data["email"].lower()
        keys = attempt_keys("portal-claim", email, request.META.get("REMOTE_ADDR", ""))
        if not login_blocked(keys):
            # All requests count, including requests for an unregistered address.
            record_login_attempt(keys, False)
            applicant = _claimable_people(request.user, Applicant.objects).filter(email__iexact=email).first()
            if applicant and (request.user.is_authenticated or not User.objects.filter(email__iexact=email).exists()):
                _send_claim(request, applicant)
        return render(request, "admisiones/claim_request.html", {"requested": True})
    return render(request, "admisiones/claim_request.html", {"form": form})


def _read_claim(token):
    if not token or len(token) > 512:
        return None
    try:
        return signing.loads(token, salt=CLAIM_SALT, max_age=timedelta(hours=24))
    except signing.BadSignature:
        return None


def _claimable_people(user, people):
    people = people.filter(portal_verified_at__isnull=True)
    if user.is_authenticated:
        owner = Q(user_id=user.pk)
        if not Applicant.objects.filter(user_id=user.pk).exists():
            owner |= Q(user__isnull=True)
        return people.filter(owner, email__iexact=user.email)
    return people.filter(user__isnull=True)


def _verified_applicant(token, user, *, lock):
    claim = _read_claim(token)
    if claim is None:
        return None
    public_id, nonce, digest = claim
    people = Applicant.objects.select_for_update() if lock else Applicant.objects
    applicant = _claimable_people(user, people).filter(public_id=public_id, claim_nonce=nonce).first()
    if applicant and constant_time_compare(_email_digest(applicant.email), digest):
        if user.is_authenticated or not User.objects.filter(email__iexact=applicant.email).exists():
            return applicant
    return None


def _finish_claim(request):
    submitted = request.method == "POST"
    linked = request.user.is_authenticated
    with transaction.atomic():
        applicant = _verified_applicant(request.session.get(CLAIM_SESSION_KEY), request.user, lock=submitted)
        if applicant is None:
            request.session.pop(CLAIM_SESSION_KEY, None)
            return render(request, "admisiones/claim_confirm.html", {"invalid_link": True})
        notices = PrivacyNotice.objects.select_for_update() if submitted else PrivacyNotice.objects
        notice = notices.filter(active=True).first()
        data = request.POST if submitted else None
        if linked:
            user = request.user
            form = VerifyContactForm(data)
        else:
            first_name, last_name = account_names(
                applicant.first_name, applicant.second_name, applicant.last_name, applicant.second_last_name,
            )
            user = User(email=applicant.email.lower(), first_name=first_name, last_name=last_name, role=User.Role.APPLICANT)
            form = ClaimConfirmForm(user, data)
        if submitted and notice:
            if request.POST.get("notice_id") != str(notice.pk):
                form.add_error(None, "El aviso de privacidad cambió. Lee la versión vigente y vuelve a confirmar.")
            elif form.is_valid():
                if not linked:
                    form.save()
                applicant.user = user
                applicant.claim_nonce = uuid.uuid4()
                applicant.portal_verified_at = timezone.now()
                applicant.save(update_fields=("user", "claim_nonce", "portal_verified_at"))
                PrivacyAcceptance.objects.get_or_create(user=user, notice=notice)
                request.session.pop(CLAIM_SESSION_KEY, None)
                if linked:
                    messages.success(request, "Verificaste tu correo. Ya puedes consultar tus expedientes.")
                    return redirect("applicant_dashboard")
                messages.success(request, "Tu acceso está listo. Inicia sesión para ver tus expedientes.")
                return redirect("login")
        return render(request, "admisiones/claim_confirm.html", {"form": form, "notice": notice, "linked": linked})


@never_cache
@sensitive_post_parameters("new_password1", "new_password2")
@require_http_methods(["GET", "POST"])
def claim_confirm(request, token):
    if request.user.is_authenticated and request.user.role != User.Role.APPLICANT:
        response = render(request, "admisiones/claim_confirm.html", {"signed_in": True})
    elif token != CLAIM_CONFIRM_PATH:
        request.session.pop(CLAIM_SESSION_KEY, None)
        if request.method == "GET" and _read_claim(token) is not None:
            request.session[CLAIM_SESSION_KEY] = token
        response = redirect("claim_confirm", token=CLAIM_CONFIRM_PATH)
    else:
        try:
            response = _finish_claim(request)
        except IntegrityError:
            request.session.pop(CLAIM_SESSION_KEY, None)
            response = render(request, "admisiones/claim_confirm.html", {"invalid_link": True})
    response["Referrer-Policy"] = "no-referrer"
    return response
