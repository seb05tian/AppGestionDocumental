import re
import unicodedata
import uuid

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Max, Q
from django.db.models.functions import Lower
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("El correo electrónico es obligatorio.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMINISTRATOR)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("Un administrador debe tener permisos de staff y superusuario.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        APPLICANT = "applicant", "Aspirante"
        REVIEWER = "reviewer", "Revisor de admisiones"
        ADMINISTRATOR = "administrator", "Administrador"

    ADMISSIONS_ROLES = (Role.REVIEWER, Role.ADMINISTRATOR)

    username = None
    email = models.EmailField("correo electrónico", unique=True)
    role = models.CharField("rol", max_length=20, choices=Role.choices, default=Role.APPLICANT)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    objects = UserManager()

    @property
    def can_manage_admissions(self):
        return self.role in self.ADMISSIONS_ROLES

    def __str__(self):
        return self.get_full_name() or self.email


def account_names(first, second, last, second_last):
    """Login accounts keep one first and one last name: second names are joined to them."""
    return " ".join(filter(None, (first, second))), " ".join(filter(None, (last, second_last)))


class Applicant(models.Model):
    class IdentificationType(models.TextChoices):
        CC = "CC", "Cédula de ciudadanía"
        TI = "TI", "Tarjeta de identidad"
        CE = "CE", "Cédula de extranjería"
        PASSPORT = "PASSPORT", "Pasaporte"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="profile")
    first_name = models.CharField("primer nombre", max_length=150, blank=True)
    second_name = models.CharField("segundo nombre", max_length=150, blank=True, default="")
    last_name = models.CharField("primer apellido", max_length=150, blank=True)
    second_last_name = models.CharField("segundo apellido", max_length=150, blank=True, default="")
    email = models.EmailField()
    identification_type = models.CharField(max_length=12, choices=IdentificationType.choices)
    identification_number = models.CharField(max_length=32)
    identity_key = models.CharField(max_length=128, unique=True, null=True, editable=False)
    phone = models.CharField(max_length=32)
    claim_nonce = models.UUIDField(default=uuid.uuid4, editable=False)
    portal_verified_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        constraints = [models.UniqueConstraint(Lower("email"), name="unique_applicant_contact_email")]

    @staticmethod
    def normalized_identity(kind, number):
        value = unicodedata.normalize("NFKC", number).upper()
        normalized = re.sub(r"[\s.\-]+", "", value)
        return f"{kind}:{normalized}" if kind and normalized else None

    def get_full_name(self):
        return " ".join(filter(None, (self.first_name, self.second_name, self.last_name, self.second_last_name))) or self.email

    @classmethod
    def matching_text(cls, text):
        """Every typed word must appear in some name, the email or the identification."""
        query = Q()
        for word in text.split():
            query &= (
                Q(first_name__icontains=word) | Q(second_name__icontains=word)
                | Q(last_name__icontains=word) | Q(second_last_name__icontains=word)
                | Q(email__icontains=word) | Q(identification_number__icontains=word)
            )
        return cls.objects.filter(query)

    def save(self, *args, **kwargs):
        self.identity_key = self.normalized_identity(self.identification_type, self.identification_number)
        fields = kwargs.get("update_fields")
        if self.pk and (fields is None or "email" in fields):
            previous = Applicant.objects.filter(pk=self.pk).values_list("email", flat=True).first()
            if previous and previous.lower() != self.email.lower():
                self.portal_verified_at = None
                self.claim_nonce = uuid.uuid4()
                if fields is not None:
                    kwargs["update_fields"] = {*fields, "portal_verified_at", "claim_nonce"}
        if fields and {"identification_type", "identification_number"}.intersection(fields):
            kwargs["update_fields"] = {*kwargs["update_fields"], "identity_key"}
        super().save(*args, **kwargs)

    def __str__(self):
        return self.get_full_name()


class Program(models.Model):
    name = models.CharField(max_length=140, unique=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "programa"
        verbose_name_plural = "programas"

    def __str__(self):
        return self.name


class CallQuerySet(models.QuerySet):
    def accepting(self, day):
        """Active calls whose dates include `day`; an empty date means no limit on that side."""
        return self.filter(
            Q(opens_at__isnull=True) | Q(opens_at__lte=day),
            Q(closes_at__isnull=True) | Q(closes_at__gte=day),
            active=True,
        )


class Call(models.Model):
    name = models.CharField(max_length=140)
    opens_at = models.DateField(null=True, blank=True)
    closes_at = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)
    objects = CallQuerySet.as_manager()

    class Meta:
        ordering = ("-opens_at", "name")
        verbose_name = "convocatoria"
        verbose_name_plural = "convocatorias"

    def __str__(self):
        return self.name


class RequirementDefinition(models.Model):
    call = models.ForeignKey(Call, null=True, blank=True, on_delete=models.CASCADE, related_name="requirements")
    code = models.SlugField(max_length=60)
    name = models.CharField(max_length=160)
    required = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ("position", "id")
        verbose_name = "requisito documental"
        verbose_name_plural = "requisitos documentales"
        constraints = [
            models.UniqueConstraint(fields=("call", "code"), condition=Q(call__isnull=False), name="unique_call_requirement_code"),
            models.UniqueConstraint(fields=("code",), condition=Q(call__isnull=True), name="unique_default_requirement_code"),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding:
            last = RequirementDefinition.objects.filter(call=self.call).aggregate(last=Max("position"))["last"]
            self.position = (last or 0) + 1
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class PrivacyNotice(models.Model):
    version = models.CharField(max_length=40, unique=True)
    text = models.TextField()
    active = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self):
        return f"Aviso {self.version}"


class PrivacyAcceptance(models.Model):
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name="privacy_acceptances")
    notice = models.ForeignKey(PrivacyNotice, on_delete=models.PROTECT)
    accepted_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("user", "notice"), name="unique_privacy_acceptance")]


class Application(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Borrador"
        SUBMITTED = "submitted", "Enviado"
        IN_REVIEW = "in_review", "En revisión"
        CORRECTION = "correction", "Pendiente de corrección"
        APPROVED = "approved", "Documentación aprobada"
        CLOSED = "closed", "Cerrado"

    class Origin(models.TextChoices):
        PORTAL = "portal", "Portal"
        STAFF = "staff", "Admisiones"
        CSV = "csv", "Importación CSV"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    applicant = models.ForeignKey(Applicant, on_delete=models.PROTECT, related_name="applications")
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="created_applications")
    origin = models.CharField(max_length=8, choices=Origin.choices, default=Origin.PORTAL)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="applications")
    call = models.ForeignKey(Call, on_delete=models.PROTECT, related_name="applications")
    state = models.CharField(max_length=20, choices=State.choices, default=State.DRAFT)
    assigned_reviewer = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_applications")
    review_round = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("created_at",)
        constraints = [models.UniqueConstraint(fields=("applicant", "program", "call"), name="unique_applicant_program_call")]

    def __str__(self):
        return f"{self.applicant} · {self.program} · {self.call}"

    def visible_to_reviewer(self, actor=None):
        return (
            (actor is not None and actor.role == User.Role.ADMINISTRATOR)
            or self.state != self.State.DRAFT or self.origin != self.Origin.PORTAL
        )

    def can_review_as(self, actor):
        return actor.can_manage_admissions and (
            actor.role == User.Role.ADMINISTRATOR or self.assigned_reviewer_id == actor.pk
        )

    @classmethod
    def for_applicant(cls, actor):
        return cls.objects.filter(applicant__user=actor).filter(
            Q(origin=cls.Origin.PORTAL) | Q(applicant__portal_verified_at__isnull=False)
        )

    def can_edit_documents(self, actor):
        if self.state not in {self.State.DRAFT, self.State.CORRECTION}:
            return False
        if actor.role == User.Role.APPLICANT:
            return self.applicant.user_id == actor.pk and (
                self.origin == self.Origin.PORTAL or self.applicant.portal_verified_at is not None
            )
        return actor.can_manage_admissions and self.origin != self.Origin.PORTAL


class ApplicationRequirement(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="requirements")
    source_definition = models.ForeignKey(RequirementDefinition, null=True, blank=True, on_delete=models.SET_NULL)
    code = models.SlugField(max_length=60)
    name = models.CharField(max_length=160)
    required = models.BooleanField(default=True)
    position = models.PositiveSmallIntegerField(default=0)
    current_version = models.ForeignKey("DocumentVersion", null=True, blank=True, on_delete=models.SET_NULL, related_name="current_for_requirements")

    class Meta:
        ordering = ("position", "id")
        constraints = [models.UniqueConstraint(fields=("application", "code"), name="unique_application_requirement_code")]

    def can_upload_by(self, actor, *, document_state=None):
        if actor.role == User.Role.ADMINISTRATOR and self.current_version_id:
            return (
                self.application.state not in {Application.State.APPROVED, Application.State.CLOSED}
                and (document_state or self.document_state()) != "approved"
            )
        return self.application.can_edit_documents(actor)

    def document_state(self):
        if self.current_version_id is None:
            return "pending_upload"
        review = self.current_version.reviews.filter(
            review_round=self.application.review_round
        ).order_by("-created_at", "-id").first()
        if review is None:
            return "pending_review"
        return "approved" if review.decision == Review.Decision.APPROVE else "correction"


def applicant_file_key(applicant):
    """Identification without punctuation; falls back to a short public id when there is none."""
    return re.sub(r"[^A-Za-z0-9]", "", applicant.identification_number) or str(applicant.public_id)[:8]


class DocumentVersion(models.Model):
    requirement = models.ForeignKey(ApplicationRequirement, on_delete=models.PROTECT, related_name="versions")
    version_number = models.PositiveSmallIntegerField()
    storage_key = models.CharField(max_length=500)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=40)
    byte_size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64, db_index=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-version_number",)
        constraints = [models.UniqueConstraint(fields=("requirement", "version_number"), name="unique_requirement_version_number")]

    def __str__(self):
        return f"{self.requirement.name} · v{self.version_number}"

    @property
    def standard_name(self):
        """Download name: identification, requirement code, version and the validated extension."""
        extension = "." + self.storage_key.rsplit(".", 1)[-1]
        return f"{applicant_file_key(self.requirement.application.applicant)}_{self.requirement.code}_v{self.version_number}{extension}"


class ProcessingJob(models.Model):
    class Kind(models.TextChoices):
        OCR = "ocr", "OCR"
        AI = "ai", "IA"

    class Status(models.TextChoices):
        QUEUED = "queued", "Pendiente"
        RUNNING = "running", "Procesando"
        DONE = "done", "Finalizado"
        FAILED = "failed", "Error técnico"

    version = models.ForeignKey(DocumentVersion, on_delete=models.CASCADE, related_name="processing_jobs")
    kind = models.CharField(max_length=3, choices=Kind.choices, default=Kind.OCR)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    locked_at = models.DateTimeField(null=True, blank=True)
    available_at = models.DateTimeField(default=timezone.now)
    error_message = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("created_at",)
        constraints = [models.UniqueConstraint(fields=("version", "kind"), name="unique_processing_job_version_kind")]


class PrevalidationResult(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        CLEAR = "clear", "Sin alertas detectadas"
        REVIEW = "review", "Requiere revisión"
        INCONCLUSIVE = "inconclusive", "No concluyente"
        ERROR = "error", "Error técnico"

    version = models.OneToOneField(DocumentVersion, on_delete=models.CASCADE, related_name="prevalidation")
    ai_result = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    extracted_text = models.TextField(blank=True)
    detected_type = models.CharField(max_length=40, blank=True)
    extracted_identification = models.CharField(max_length=32, blank=True)
    identification_matches = models.BooleanField(null=True, blank=True)
    alerts = models.JSONField(default=list)
    rules_version = models.CharField(max_length=40, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)


class Review(models.Model):
    class Decision(models.TextChoices):
        APPROVE = "approve", "Aprobar documento"
        CORRECTION = "correction", "Solicitar corrección"

    version = models.ForeignKey(DocumentVersion, on_delete=models.PROTECT, related_name="reviews")
    reviewer = models.ForeignKey(User, on_delete=models.PROTECT, related_name="document_reviews")
    decision = models.CharField(max_length=12, choices=Decision.choices)
    reason = models.TextField(blank=True)
    review_round = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("version", "review_round"), name="unique_version_review_round")]

    def clean(self):
        if self.decision == self.Decision.CORRECTION and not self.reason.strip():
            raise ValidationError({"reason": "Indica el motivo de la corrección solicitada."})


class StateHistory(models.Model):
    application = models.ForeignKey(Application, on_delete=models.PROTECT, related_name="history")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT)
    from_state = models.CharField(max_length=20, blank=True)
    to_state = models.CharField(max_length=20)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("created_at", "id")

    def get_to_state_display(self):
        return dict(Application.State.choices).get(self.to_state, self.to_state)


class Observation(models.Model):
    class Audience(models.TextChoices):
        APPLICANT = "applicant", "Visible al aspirante"
        INTERNAL = "internal", "Interna de admisiones"

    application = models.ForeignKey(Application, on_delete=models.CASCADE, related_name="observations")
    author = models.ForeignKey(User, on_delete=models.PROTECT)
    audience = models.CharField(max_length=12, choices=Audience.choices)
    text = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at",)


class Notification(models.Model):
    class Kind(models.TextChoices):
        RECEIVED = "received", "Expediente recibido"
        CORRECTION = "correction", "Corrección solicitada"
        APPROVED = "approved", "Documentación aprobada"
        REOPENED = "reopened", "Expediente reabierto"

    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="notifications")
    application = models.ForeignKey(Application, null=True, blank=True, on_delete=models.CASCADE)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    title = models.CharField(max_length=120)
    message = models.TextField()
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-created_at",)


class EmailOutbox(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Pendiente"
        SENDING = "sending", "En proceso"
        SENT = "sent", "Enviado"
        FAILED = "failed", "Fallido"

    notification = models.OneToOneField(Notification, on_delete=models.CASCADE, related_name="email_outbox")
    recipient = models.EmailField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    locked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=200, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)


class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, db_index=True)
    succeeded = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ("-created_at",)
