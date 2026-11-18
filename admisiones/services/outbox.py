from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from admisiones.models import EmailOutbox, Notification


def process_next_email():
    with transaction.atomic():
        now = timezone.now()
        item = EmailOutbox.objects.select_for_update(skip_locked=True).filter(
            status=EmailOutbox.Status.QUEUED, available_at__lte=now
        ).order_by("available_at", "pk").first()
        if not item:
            return False
        item.status = EmailOutbox.Status.SENDING
        item.locked_at = now
        item.attempts += 1
        item.save(update_fields=("status", "locked_at", "attempts"))
        notification = item.notification
        body = (
            "Admisiones solicitó correcciones. Consulta los documentos y motivos en tu expediente del portal."
            if notification.kind == Notification.Kind.CORRECTION else notification.message
        )
        claim = item.pk, notification.title, body, item.recipient, now
    item_id, subject, body, recipient, claimed_at = claim
    try:
        sent = send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [recipient], fail_silently=False)
        if sent != 1:
            raise RuntimeError("El backend no aceptó el mensaje.")
    except Exception:
        _finish_email(item_id, claimed_at, error=True)
    else:
        _finish_email(item_id, claimed_at, error=False)
    return True


def _finish_email(item_id, claimed_at, error):
    with transaction.atomic():
        item = EmailOutbox.objects.select_for_update().get(pk=item_id)
        if item.status != EmailOutbox.Status.SENDING or item.locked_at != claimed_at:
            return False
        item.locked_at = None
        if not error:
            item.status = EmailOutbox.Status.SENT
            item.sent_at = timezone.now()
            item.last_error = ""
        elif item.attempts >= settings.EMAIL_MAX_ATTEMPTS:
            item.status = EmailOutbox.Status.FAILED
            item.last_error = "El envío de correo falló después de varios intentos."
        else:
            item.status = EmailOutbox.Status.QUEUED
            item.available_at = timezone.now() + timedelta(minutes=min(60, 2 ** (item.attempts - 1)))
            item.last_error = "El envío de correo falló; se reintentará."
        item.save(update_fields=("status", "locked_at", "sent_at", "last_error", "available_at"))
        return True


def recover_stale_emails():
    cutoff = timezone.now() - timedelta(minutes=settings.EMAIL_STALE_MINUTES)
    recovered = 0
    with transaction.atomic():
        items = EmailOutbox.objects.select_for_update(skip_locked=True).filter(
            status=EmailOutbox.Status.SENDING, locked_at__lt=cutoff
        )
        for item in items:
            item.locked_at = None
            if item.attempts >= settings.EMAIL_MAX_ATTEMPTS:
                item.status = EmailOutbox.Status.FAILED
                item.last_error = "El trabajador de correo se interrumpió durante el envío."
            else:
                item.status = EmailOutbox.Status.QUEUED
                item.available_at = timezone.now()
                item.last_error = ""
            item.save(update_fields=("status", "locked_at", "available_at", "last_error"))
            recovered += 1
    return recovered
