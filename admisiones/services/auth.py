import hashlib
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from admisiones.models import LoginAttempt

WINDOW = timedelta(minutes=15)
HISTORY = timedelta(days=1)


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def attempt_keys(scope, email, ip):
    """Returns [per-account-and-address key, per-address key] for one kind of attempt."""
    return [
        _digest(f"{scope}|email:{email.strip().lower()}|ip:{ip}"),
        _digest(f"{scope}|ip:{ip}"),
    ]


def login_blocked(keys):
    # The account key stops guessing one password; the address key stops spraying many accounts.
    account_key, address_key = keys
    cutoff = timezone.now() - WINDOW
    return (
        LoginAttempt.objects.filter(key=account_key, succeeded=False, created_at__gte=cutoff).count() >= settings.LOGIN_RATE_LIMIT
        or LoginAttempt.objects.filter(key=address_key, succeeded=False, created_at__gte=cutoff).count() >= settings.LOGIN_IP_RATE_LIMIT
    )


def record_login_attempt(keys, succeeded):
    LoginAttempt.objects.bulk_create([LoginAttempt(key=key, succeeded=succeeded) for key in keys])


def purge_login_attempts():
    deleted, _ = LoginAttempt.objects.filter(created_at__lt=timezone.now() - HISTORY).delete()
    return deleted
