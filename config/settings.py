import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv("DEBUG", "0") == "1"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-change-before-deploy")
if not DEBUG and SECRET_KEY == "development-only-change-before-deploy":
    raise ImproperlyConfigured("Configura DJANGO_SECRET_KEY antes de iniciar con DEBUG=0.")
ALLOWED_HOSTS = [host.strip() for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,web").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "admisiones",
]
AUTH_USER_MODEL = "admisiones.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "admisiones.context_processors.navigation_context",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"


def database_from_url(value):
    parsed = urlparse(value)
    if parsed.scheme in {"sqlite", "sqlite3"}:
        path = unquote(parsed.path)
        name = ":memory:" if path == "/:memory:" else str(BASE_DIR / path.lstrip("/"))
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": name}
    if parsed.scheme in {"postgres", "postgresql"}:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(parsed.path.lstrip("/")),
            "USER": unquote(parsed.username or ""),
            "PASSWORD": unquote(parsed.password or ""),
            "HOST": parsed.hostname or "localhost",
            "PORT": parsed.port or 5432,
            "CONN_HEALTH_CHECKS": True,
        }
    raise ImproperlyConfigured("DATABASE_URL debe usar postgresql:// o sqlite:///.")


DATABASES = {"default": database_from_url(os.getenv("DATABASE_URL", "postgresql://asistente:asistente@localhost:5432/asistente"))}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
PASSWORD_RESET_TIMEOUT = 60 * 60
LOGIN_URL = "/ingresar/"
APP_PUBLIC_URL = os.getenv("APP_PUBLIC_URL", "")
LOGIN_RATE_LIMIT = int(os.getenv("LOGIN_RATE_LIMIT", "5"))
LOGIN_IP_RATE_LIMIT = int(os.getenv("LOGIN_IP_RATE_LIMIT", "20"))

LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
PRIVATE_MEDIA_ROOT = Path(os.getenv("PRIVATE_MEDIA_ROOT", str(BASE_DIR / "var" / "private")))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "10"))
MAX_UPLOADS_PER_HOUR = int(os.getenv("MAX_UPLOADS_PER_HOUR", "30"))
FILE_UPLOAD_MAX_MEMORY_SIZE = (MAX_UPLOAD_MB + 1) * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = (MAX_UPLOAD_MB + 1) * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 10

OCR_MAX_PAGES = int(os.getenv("OCR_MAX_PAGES", "20"))
AI_PREVALIDATION_ENABLED = os.getenv("AI_PREVALIDATION_ENABLED", "0") == "1"
AI_MODEL = "qwen3-vl:4b-instruct"
AI_TIMEOUT_SECONDS = 120
OCR_DPI = int(os.getenv("OCR_DPI", "150"))
OCR_MAX_IMAGE_PIXELS = int(os.getenv("OCR_MAX_IMAGE_PIXELS", "12000000"))
OCR_TIMEOUT_SECONDS = int(os.getenv("OCR_TIMEOUT_SECONDS", "60"))
OCR_MAX_ATTEMPTS = int(os.getenv("OCR_MAX_ATTEMPTS", "3"))
OCR_STALE_MINUTES = int(os.getenv("OCR_STALE_MINUTES", "15"))
OCR_RULES_VERSION = os.getenv("OCR_RULES_VERSION", "2026-01")
OCR_IDENTIFICATION_PATTERN = os.getenv("OCR_IDENTIFICATION_PATTERN", r"(?<!\d)(\d[\d. ]{5,14}\d)(?!\d)")
OCR_TYPE_RULES = (
    ("identity", tuple(filter(None, os.getenv("OCR_IDENTITY_KEYWORDS", "cedula|documento de identidad|tarjeta de identidad|passport|pasaporte").split("|"))), "Documento de identidad"),
    ("saber11", tuple(filter(None, os.getenv("OCR_SABER11_KEYWORDS", "saber 11|icfes|resultado de pruebas").split("|"))), "Resultado Saber 11"),
    ("graduation_record", tuple(filter(None, os.getenv("OCR_GRADUATION_KEYWORDS", "acta de grado|acta de graduacion").split("|"))), "Acta de grado"),
    ("diploma", tuple(filter(None, os.getenv("OCR_DIPLOMA_KEYWORDS", "diploma|titulo de bachiller").split("|"))), "Diploma"),
)

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("SMTP_HOST", "")
EMAIL_PORT = int(os.getenv("SMTP_PORT", "587"))
EMAIL_HOST_USER = os.getenv("SMTP_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("SMTP_USE_TLS", "1") == "1"
EMAIL_USE_SSL = os.getenv("SMTP_USE_SSL", "0") == "1"
EMAIL_TIMEOUT = int(os.getenv("SMTP_TIMEOUT", "10"))
EMAIL_MAX_ATTEMPTS = int(os.getenv("EMAIL_MAX_ATTEMPTS", "5"))
EMAIL_STALE_MINUTES = int(os.getenv("EMAIL_STALE_MINUTES", "5"))
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "Admisiones <no-reply@example.invalid>")

CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if origin.strip()]
SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "0") == "1"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "SAMEORIGIN"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
