from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm, UsernameField
from django.core.validators import RegexValidator
from django.utils import timezone

from .models import Applicant, Call, Program, RequirementDefinition, Review, User, account_names


class ApplicantDetailsForm(forms.Form):
    first_name = forms.CharField(label="Primer nombre", max_length=150)
    second_name = forms.CharField(label="Segundo nombre (opcional)", max_length=150, required=False)
    last_name = forms.CharField(label="Primer apellido", max_length=150)
    second_last_name = forms.CharField(label="Segundo apellido (opcional)", max_length=150, required=False)
    identification_type = forms.ChoiceField(label="Tipo de identificación", choices=Applicant.IdentificationType.choices)
    identification_number = forms.CharField(label="Número de identificación", max_length=32)
    email = forms.EmailField(label="Correo electrónico", max_length=254)
    phone = forms.CharField(
        label="Teléfono", max_length=32, widget=forms.TextInput(attrs={"inputmode": "tel"}),
        validators=[RegexValidator(
            r"\A\+?[(). -]*[0-9][0-9(). -]*\Z", "Ingresa un teléfono válido con dígitos y un prefijo opcional.",
        )],
    )

    def clean_email(self):
        return self.cleaned_data["email"].lower()

    def clean_identification_number(self):
        number = self.cleaned_data["identification_number"]
        key = Applicant.normalized_identity("ID", number)
        if not key or len(key) > 128 or not key.partition(":")[2].isalnum():
            raise forms.ValidationError("Ingresa un número de identificación válido.")
        return number


class RegistrationForm(ApplicantDetailsForm, UserCreationForm):
    field_order = (
        "first_name", "second_name", "last_name", "second_last_name", "identification_type", "identification_number",
        "email", "phone", "password1", "password2", "privacy_acceptance",
    )
    privacy_acceptance = forms.BooleanField(label="Acepto el aviso de privacidad", error_messages={"required": "Debes aceptar el aviso de privacidad para continuar."})
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.label_suffix = ""
        self.fields["password1"].help_text = "Mínimo 8 caracteres. No uses solo números ni una clave común."
        self.fields["password2"].label = "Repite la contraseña"
        self.fields["password2"].help_text = ""

    class Meta:
        model = User
        fields = ("first_name", "last_name", "email")
        labels = {"email": "Correo electrónico"}

    def clean_email(self):
        email = super().clean_email()
        if User.objects.filter(email__iexact=email).exists() or Applicant.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("No puedes registrar este correo aquí. Si admisiones creó tu expediente, usa Vincular mi expediente.")
        return email

    def clean_identification_number(self):
        number = super().clean_identification_number()
        key = Applicant.normalized_identity(self.cleaned_data.get("identification_type"), number)
        if key and Applicant.objects.filter(identity_key=key).exists():
            raise forms.ValidationError("No puedes registrar esta identificación aquí. Si admisiones creó tu expediente, usa Vincular mi expediente.")
        return number

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.APPLICANT
        user.first_name, user.last_name = account_names(*(self.cleaned_data[name] for name in (
            "first_name", "second_name", "last_name", "second_last_name",
        )))
        if commit:
            user.save()
            Applicant.objects.create(user=user, **{
                field: self.cleaned_data[field] for field in ApplicantDetailsForm.base_fields
            })
        return user


class LoginForm(AuthenticationForm):
    username = UsernameField(label="Correo electrónico o usuario")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.label_suffix = ""
    error_messages = {"invalid_login": "Revisa el correo o usuario y la contraseña."}


class RateLimitedAdminAuthenticationForm(AdminAuthenticationForm):
    def clean(self):
        from .services.auth import attempt_keys, login_blocked, record_login_attempt

        email = self.data.get("username", "")
        ip = self.request.META.get("REMOTE_ADDR", "")
        keys = attempt_keys("login", email, ip)
        if login_blocked(keys):
            raise forms.ValidationError("Demasiados intentos. Espera 15 minutos antes de volver a intentar.")
        try:
            cleaned = super().clean()
        except forms.ValidationError:
            record_login_attempt(keys, False)
            raise
        record_login_attempt(keys, not self.errors)
        return cleaned


class ApplicationForm(forms.Form):
    program = forms.ModelChoiceField(label="Programa", queryset=Program.objects.filter(active=True), empty_label="Selecciona un programa")
    call = forms.ModelChoiceField(label="Convocatoria", queryset=Call.objects.none(), empty_label="Selecciona una convocatoria")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["call"].queryset = Call.objects.accepting(timezone.localdate())
        for field in self.fields.values():
            field.label_suffix = ""


class StaffApplicationForm(ApplicantDetailsForm, ApplicationForm):
    field_order = (*ApplicantDetailsForm.base_fields, *ApplicationForm.base_fields)


class ProgramSettingsForm(forms.ModelForm):
    class Meta:
        model = Program
        fields = ("name", "active")
        labels = {"name": "Nombre del programa", "active": "Activo"}


class CallSettingsForm(forms.ModelForm):
    class Meta:
        model = Call
        fields = ("name", "opens_at", "closes_at", "active")
        labels = {"name": "Nombre de la convocatoria", "opens_at": "Fecha de apertura", "closes_at": "Fecha de cierre", "active": "Activa"}
        widgets = {
            "opens_at": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
            "closes_at": forms.DateInput(format="%Y-%m-%d", attrs={"type": "date"}),
        }

    def clean(self):
        cleaned = super().clean()
        opens, closes = cleaned.get("opens_at"), cleaned.get("closes_at")
        if opens and closes and closes < opens:
            self.add_error("closes_at", "El cierre debe ser igual o posterior a la apertura.")
        return cleaned


class RequirementSettingsForm(forms.ModelForm):
    class Meta:
        model = RequirementDefinition
        fields = ("name", "code", "call", "required", "active")
        labels = {
            "name": "Nombre del requisito", "code": "Código", "call": "Convocatoria",
            "required": "Obligatorio", "active": "Activo",
        }
        help_texts = {
            "code": "Usa letras, números, guiones o guiones bajos.",
            "call": "Vacío: se aplica a todas las convocatorias.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["call"].empty_label = "General"


class UploadForm(forms.Form):
    file = forms.FileField(label="Documento", widget=forms.ClearableFileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"}))


class ReviewDecisionForm(forms.Form):
    version_id = forms.IntegerField(widget=forms.HiddenInput)
    decision = forms.ChoiceField(choices=Review.Decision.choices, widget=forms.HiddenInput)
    reason = forms.CharField(label="Motivo de corrección", max_length=1000, required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("decision") == Review.Decision.CORRECTION and not cleaned.get("reason", "").strip():
            self.add_error("reason", "Indica el motivo que debe ver el aspirante.")
        return cleaned


class ReopenForm(forms.Form):
    reason = forms.CharField(label="Motivo para reabrir", widget=forms.Textarea(attrs={"rows": 3}))


class ApplicantEditForm(ApplicantDetailsForm):
    def __init__(self, *args, person, **kwargs):
        kwargs.setdefault("initial", {name: getattr(person, name) for name in ApplicantDetailsForm.base_fields})
        super().__init__(*args, **kwargs)
        self.person = person

    def clean(self):
        cleaned = super().clean()
        kind, number = cleaned.get("identification_type"), cleaned.get("identification_number")
        if kind and number:
            key = Applicant.normalized_identity(kind, number)
            if Applicant.objects.exclude(pk=self.person.pk).filter(identity_key=key).exists():
                self.add_error("identification_number", "Otra persona ya tiene esa identificación.")
        email = cleaned.get("email")
        if email and Applicant.objects.exclude(pk=self.person.pk).filter(email__iexact=email).exists():
            self.add_error("email", "Otra persona ya usa ese correo.")
        return cleaned

    def save(self):
        for name in ApplicantDetailsForm.base_fields:
            setattr(self.person, name, self.cleaned_data[name])
        self.person.save()
        return self.person


class ApplicantDeleteForm(forms.Form):
    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        self.person = person

    def clean(self):
        cleaned = super().clean()
        if self.person.applications.exists():
            raise forms.ValidationError("Este aspirante tiene expedientes. Para conservar el historial no se puede eliminar.")
        return cleaned
