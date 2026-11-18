from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm as DjangoUserChangeForm
from django.contrib.auth.forms import UserCreationForm as DjangoUserCreationForm
from django.db import transaction
from django.utils import timezone

from .models import (
    Applicant,
    Application,
    ApplicationRequirement,
    Call,
    DocumentVersion,
    EmailOutbox,
    LoginAttempt,
    Notification,
    Observation,
    PrevalidationResult,
    PrivacyAcceptance,
    PrivacyNotice,
    ProcessingJob,
    Program,
    RequirementDefinition,
    Review,
    StateHistory,
    User,
)
from .forms import RateLimitedAdminAuthenticationForm

admin.site.login_form = RateLimitedAdminAuthenticationForm

def is_administrator(user):
    return user.is_superuser or (user.is_staff and user.role == User.Role.ADMINISTRATOR)


class ManagedModelAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return is_administrator(request.user)

    def has_view_permission(self, request, obj=None):
        return is_administrator(request.user)

    def has_add_permission(self, request):
        return is_administrator(request.user)

    def has_change_permission(self, request, obj=None):
        return is_administrator(request.user)

    def has_delete_permission(self, request, obj=None):
        return is_administrator(request.user)


class ProfileInline(admin.StackedInline):
    model = Applicant
    extra = 0
    max_num = 1
    can_delete = False


class UserAddForm(DjangoUserCreationForm):
    class Meta:
        model = User
        fields = ("email", "first_name", "last_name", "role")


class UserEditForm(DjangoUserChangeForm):
    class Meta:
        model = User
        fields = "__all__"


@admin.register(User)
class UserAdmin(ManagedModelAdmin, DjangoUserAdmin):
    add_form = UserAddForm
    form = UserEditForm
    inlines = (ProfileInline,)
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "role", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("email", "first_name", "last_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Datos personales", {"fields": ("first_name", "last_name", "role")}),
        ("Permisos", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Fechas", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "first_name", "last_name", "role", "password1", "password2")}),)
    filter_horizontal = ("groups", "user_permissions")

    def save_model(self, request, obj, form, change):
        if obj.role == User.Role.ADMINISTRATOR:
            obj.is_staff = True
        elif not obj.is_superuser:
            obj.is_staff = False
        super().save_model(request, obj, form, change)


@admin.register(PrivacyNotice)
class PrivacyNoticeAdmin(ManagedModelAdmin):
    list_display = ("version", "active", "created_at")
    readonly_fields = ("created_at",)

    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            if obj.active:
                PrivacyNotice.objects.exclude(pk=obj.pk).update(active=False)
            super().save_model(request, obj, form, change)


@admin.register(RequirementDefinition)
class RequirementDefinitionAdmin(ManagedModelAdmin):
    list_display = ("name", "code", "call", "required", "active", "position")
    list_filter = ("call", "required", "active")
    search_fields = ("name", "code")
    ordering = ("call__name", "position")
    change_list_template = "admin/admisiones/requirementdefinition/change_list.html"


@admin.register(Program)
class ProgramAdmin(ManagedModelAdmin):
    list_display = ("name", "active")
    list_filter = ("active",)
    search_fields = ("name",)


@admin.register(Call)
class CallAdmin(ManagedModelAdmin):
    list_display = ("name", "opens_at", "closes_at", "active")
    list_filter = ("active",)
    search_fields = ("name",)


class ReadOnlyAuditAdmin(ManagedModelAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.action(description="Reintentar envíos fallidos")
def retry_email(modeladmin, request, queryset):
    queryset.filter(status=EmailOutbox.Status.FAILED).update(
        status=EmailOutbox.Status.QUEUED,
        attempts=0,
        last_error="",
        available_at=timezone.now(),
    )


class EmailOutboxAdmin(ReadOnlyAuditAdmin):
    list_display = ("recipient", "status", "attempts", "available_at", "sent_at")
    list_filter = ("status",)
    actions = (retry_email,)

    def has_change_permission(self, request, obj=None):
        return is_administrator(request.user)


for model in (Applicant, Application, ApplicationRequirement, DocumentVersion, LoginAttempt, Notification, Observation, PrevalidationResult, PrivacyAcceptance, ProcessingJob, Review, StateHistory):
    admin.site.register(model, ReadOnlyAuditAdmin)
admin.site.register(EmailOutbox, EmailOutboxAdmin)
