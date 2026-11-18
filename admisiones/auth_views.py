from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeDoneView, PasswordChangeView, PasswordResetCompleteView, PasswordResetConfirmView, PasswordResetDoneView, PasswordResetView
from django.db import IntegrityError, transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .forms import LoginForm, RegistrationForm
from .models import Applicant, Application, PrivacyAcceptance, PrivacyNotice, User
from .services.auth import attempt_keys, login_blocked, record_login_attempt


def _role_home(user):
    if user.role == User.Role.APPLICANT:
        return reverse("applicant_dashboard")
    return reverse("administration_home" if user.role == User.Role.ADMINISTRATOR else "reviewer_inbox")


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect(_role_home(request.user))
    notice = PrivacyNotice.objects.filter(active=True).order_by("-created_at").first()
    form = RegistrationForm(request.POST or None)
    if request.method == "POST":
        if notice is None:
            form.add_error(None, "El aviso de privacidad todavía no está configurado.")
        elif form.is_valid():
            try:
                with transaction.atomic():
                    user = form.save()
                    PrivacyAcceptance.objects.create(user=user, notice=notice)
            except IntegrityError:
                form.add_error(None, "Los datos ya están registrados. Si admisiones creó tu expediente, usa Vincular mi expediente.")
            else:
                auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
                messages.success(request, "Tu cuenta está lista. Ahora puedes crear una solicitud.")
                return redirect("applicant_dashboard")
    return render(request, "admisiones/register.html", {"form": form, "privacy_notice": notice})


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect(_role_home(request.user))
    form = LoginForm(request, data=request.POST or None)
    status = 200
    if request.method == "POST":
        email = request.POST.get("username", "")
        ip = request.META.get("REMOTE_ADDR", "")
        keys = attempt_keys("login", email, ip)
        if login_blocked(keys):
            form.add_error(None, "Demasiados intentos. Espera 15 minutos antes de volver a intentar.")
            status = 429
        elif form.is_valid():
            user = form.get_user()
            record_login_attempt(keys, True)
            auth_login(request, user)
            return redirect(_role_home(user))
        else:
            record_login_attempt(keys, False)
            status = 401
    return render(request, "admisiones/login.html", {"form": form}, status=status)


@require_POST
def logout_view(request):
    auth_logout(request)
    messages.success(request, "Has cerrado la sesión.")
    return redirect("login")


@login_required
def applicant_dashboard(request):
    if request.user.role != User.Role.APPLICANT:
        return HttpResponseForbidden()
    applications = Application.for_applicant(request.user).select_related("program", "call")
    return render(request, "admisiones/applicant_dashboard.html", {"applications": applications})


@login_required
@require_GET
def profile(request):
    return render(request, "admisiones/profile.html", {"applicant": Applicant.objects.filter(user=request.user).first()})


def home(request):
    if not request.user.is_authenticated:
        return redirect("login")
    return redirect(_role_home(request.user))


class AppPasswordResetView(PasswordResetView):
    template_name = "registration/password_reset_form.html"
    email_template_name = "registration/password_reset_email.html"
    subject_template_name = "registration/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")


class AppPasswordResetDoneView(PasswordResetDoneView):
    template_name = "registration/password_reset_done.html"


class AppPasswordResetConfirmView(PasswordResetConfirmView):
    template_name = "registration/password_reset_confirm.html"
    success_url = reverse_lazy("password_reset_complete")


class AppPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "registration/password_reset_complete.html"


class AppPasswordChangeView(PasswordChangeView):
    template_name = "registration/password_change_form.html"
    success_url = reverse_lazy("password_change_done")


class AppPasswordChangeDoneView(PasswordChangeDoneView):
    template_name = "registration/password_change_done.html"
