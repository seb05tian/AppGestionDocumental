import re
import unicodedata
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def populate_applicants(apps, schema_editor):
    db = schema_editor.connection.alias
    User = apps.get_model("admisiones", "User")
    Applicant = apps.get_model("admisiones", "Applicant")
    Application = apps.get_model("admisiones", "Application")
    StateHistory = apps.get_model("admisiones", "StateHistory")
    account_ids = Application.objects.using(db).values_list("applicant_id", flat=True)
    accounts = User.objects.using(db).filter(models.Q(role="applicant") | models.Q(pk__in=account_ids))
    for account in accounts.iterator():
        Applicant.objects.using(db).get_or_create(user_id=account.pk, defaults={
            "identification_type": "", "identification_number": "", "phone": "",
        })

    seen_identities, seen_emails = {}, {}
    for person in Applicant.objects.using(db).select_related("user").iterator():
        account = person.user
        person.public_id = uuid.uuid4()
        person.first_name, person.last_name = account.first_name, account.last_name
        person.email = account.email
        normalized = re.sub(r"[\s.\-]+", "", unicodedata.normalize("NFKC", person.identification_number).upper())
        person.identity_key = f"{person.identification_type}:{normalized}" if person.identification_type and normalized else None
        email_key = person.email.lower()
        collision = seen_emails.get(email_key) or seen_identities.get(person.identity_key)
        if collision is not None:
            raise RuntimeError(
                f"Los perfiles históricos {collision} y {person.pk} tienen correos o identificaciones equivalentes. "
                "Verifica sus datos en la instalación anterior antes de reintentar la migración."
            )
        seen_emails[email_key] = person.pk
        if person.identity_key:
            seen_identities[person.identity_key] = person.pk
        person.save(using=db)

    for application in Application.objects.using(db).iterator():
        actor_id = StateHistory.objects.using(db).filter(
            application_id=application.pk, from_state="", to_state="draft",
        ).order_by("created_at", "pk").values_list("actor_id", flat=True).first()
        application.created_by_id = actor_id or application.applicant_id
        application.person_id = Applicant.objects.using(db).get(user_id=application.applicant_id).pk
        application.save(using=db, update_fields=("person", "created_by"))


class Migration(migrations.Migration):
    dependencies = [("admisiones", "0003_emailoutbox_locked_at_alter_emailoutbox_status")]

    operations = [
        migrations.RenameModel("Profile", "Applicant"),
        migrations.AddField("applicant", "public_id", models.UUIDField(null=True, editable=False)),
        migrations.AddField("applicant", "first_name", models.CharField(blank=True, max_length=150)),
        migrations.AddField("applicant", "last_name", models.CharField(blank=True, max_length=150)),
        migrations.AddField("applicant", "email", models.EmailField(default="", max_length=254), preserve_default=False),
        migrations.AddField("applicant", "identity_key", models.CharField(editable=False, max_length=128, null=True, unique=True)),
        migrations.AddField("applicant", "claim_nonce", models.UUIDField(default=uuid.uuid4, editable=False)),
        migrations.AlterField("applicant", "identification_number", models.CharField(max_length=32)),
        migrations.AddField("application", "person", models.ForeignKey(
            null=True, on_delete=django.db.models.deletion.PROTECT, to="admisiones.applicant",
        )),
        migrations.AddField("application", "created_by", models.ForeignKey(
            null=True, on_delete=django.db.models.deletion.PROTECT,
            related_name="created_applications", to=settings.AUTH_USER_MODEL,
        )),
        migrations.RunPython(populate_applicants),
    ]
