import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    dependencies = [("admisiones", "0004_applicant_identity")]

    operations = [
        migrations.AlterField("applicant", "public_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
        migrations.AlterField("applicant", "user", models.OneToOneField(
            blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
            related_name="profile", to=settings.AUTH_USER_MODEL,
        )),
        migrations.AddConstraint("applicant", models.UniqueConstraint(Lower("email"), name="unique_applicant_contact_email")),
        migrations.RemoveConstraint("application", "unique_applicant_program_call"),
        migrations.RemoveField("application", "applicant"),
        migrations.RenameField("application", "person", "applicant"),
        migrations.AlterField("application", "applicant", models.ForeignKey(
            on_delete=django.db.models.deletion.PROTECT, related_name="applications", to="admisiones.applicant",
        )),
        migrations.AlterField("application", "created_by", models.ForeignKey(
            on_delete=django.db.models.deletion.PROTECT, related_name="created_applications", to=settings.AUTH_USER_MODEL,
        )),
        migrations.AddConstraint("application", models.UniqueConstraint(
            fields=("applicant", "program", "call"), name="unique_applicant_program_call",
        )),
        migrations.AddField("application", "origin", models.CharField(
            choices=[("portal", "Portal"), ("staff", "Admisiones"), ("csv", "Importación CSV")], default="portal", max_length=8,
        )),
        migrations.AlterField("notification", "user", models.ForeignKey(
            blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
            related_name="notifications", to=settings.AUTH_USER_MODEL,
        )),
        migrations.AlterField("notification", "message", models.TextField()),
        migrations.AddField("applicant", "portal_verified_at", models.DateTimeField(blank=True, editable=False, null=True)),
    ]
