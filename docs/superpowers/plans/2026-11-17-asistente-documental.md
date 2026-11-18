# Asistente de revisión documental Implementation Plan

**Estado:** implementación y verificaciones de desarrollo completadas. La puesta en producción, la conexión a SMTP institucional y la medición de precisión con documentos reales autorizados quedan pendientes.

> **For agentic workers:** Use the native in-session workflow to execute this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Deliver a working, permission-checked admissions document workflow from registration through review, correction, and documentary approval.

**Architecture:** A Django monolith serves templates and stores operational state in PostgreSQL. A separate Django management command processes OCR and email outbox work from database tables; private files live in a persistent shared volume.

**Tech Stack:** Python 3.12, Django 5.2 LTS, PostgreSQL, Pillow, PyMuPDF, pytesseract/Tesseract Spanish, Docker Compose, Django TestCase, Playwright browser test.

**Spec:** `docs/superpowers/specs/2026-11-17-asistente-documental-design.md`

## Global Constraints

- Roles: applicant, admissions reviewer, administrator; no public staff signup.
- PDF, JPG, and PNG only; default file limit 10 MB, configurable.
- OCR is local, asynchronous, rule-based, and never approves or rejects.
- Application requirements are snapshotted; prior file versions/reviews remain available.
- All private file views, downloads, actions, and exports enforce server-side permissions.
- UI uses institutional blue/gray/white, keyboard focus, labels, and mobile-responsive layouts.
- Do not fabricate real applicant documents, measurements, identities, or institutional legal claims.

## Review Focus

- Malformed, oversized, encrypted, and content/extension-mismatched uploads must fail before storage.
- A guessed application/file ID must never grant another applicant access.
- A stale document version or concurrent review assignment must return a controlled conflict.
- An interrupted/retried job must not overwrite a newer version or create duplicate decisions/notifications.
- Empty indicator denominators and spreadsheet formulas must render safely without invented values.

---

### Task 1: Django base, identity, configuration, and privacy

**Files:** `manage.py`, `config/`, `admisiones/models.py`, `admisiones/forms.py`, `admisiones/views.py`, `admisiones/urls.py`, `admisiones/admin.py`, migrations, auth templates, `requirements.txt`, `.env.example`, `docker-compose.yml`.

**Interfaces:** Custom email-based user with `role`; Program, Call, RequirementDefinition, PrivacyNotice, PrivacyAcceptance. Public registration creates applicant role only. Admin creates reviewer/admin accounts.

- [x] Add tests for registration, privacy version acceptance, login throttling, role redirects, and inaccessible admin routes.
- [x] Implement models/forms/authentication and Django password reset/change views.
- [x] Add initial requirement definitions and admin configuration.
- [x] Run Django tests and migration checks against PostgreSQL.

### Task 2: Application, private uploads, versions, and manual review

**Files:** `admisiones/models.py`, `admisiones/services/{state,files,workflow}.py`, applicant/reviewer views and templates, `admisiones/tests/`.

**Interfaces:** `create_application(user, program, call)`, `transition_application(application, target, actor, reason)`, `store_version(slot, upload, actor)`, and version-conditional `review_document(slot, version_id, reviewer, decision, reason)`.

- [x] Test uniqueness and requirement snapshots, file signatures/size/encryption, private storage permissions, versioning, valid transitions, stale-version conflicts, correction/reupload/resubmission, incomplete-approval rejection, and audit history.
- [x] Implement the applicant dashboard and complete submit/review/correction loop.
- [x] Add role-safe private preview/download responses and reviewer assignment conflict handling.
- [x] Run the full workflow tests and verify all six synthetic documents remained stored after restarting the app and worker.

### Task 3: Local prevalidation and DB worker

**Files:** `admisiones/services/prevalidation.py`, `admisiones/services/processing.py`, `admisiones/management/commands/run_worker.py`, OCR tests, Dockerfile.

**Interfaces:** `extract_and_check(version) -> OCRResult`; a unique processing job per version; worker claims transactionally, retries a bounded number of times, and recovers stale locks.

- [x] Test text PDF, scanned synthetic PDF, synthetic JPG/PNG, encrypted/corrupt files, type/ID rules, duplicate hashes, inconclusive/error outcomes, retry caps, stale-job recovery, and old-version isolation.
- [x] Implement page, DPI, image-size, and OCR timeout limits; store extracted text privately in the result table with a rule-set version.
- [x] Verify that upload remains responsive and OCR failures leave manual review possible.

### Task 4: Admissions queue, notifications, indicators, and export

**Files:** reviewer views/services/templates, notification outbox models/admin/worker, CSV/export tests.

**Interfaces:** shared `filtered_applications(params, reviewer)` queryset powers both paginated queue and CSV. Metrics use recorded application events and current-version OCR outcomes.

- [x] Test filters, oldest-first order, pagination, formula-safe CSV, indicator formulas with empty and populated data, in-app notices, and retryable email failures.
- [x] Implement queue, review screen, dashboard indicators, and notification list.
- [x] Implement configurable SMTP delivery and verify that email failures never roll back human review. A real institutional SMTP endpoint remains unconfigured.

### Task 5: End-to-end checks, responsive UI, and operations guide

**Files:** `static/admisiones/css/app.css`, templates, `browser_tests/`, Docker files, `README.md`, operations/backup docs, explicit demo command.

- [x] Add a Playwright journey from applicant signup through reviewer correction and applicant resubmission to documentary approval.
- [x] Test desktop/mobile widths, skip-link keyboard focus, form labels, overflow, and rendered success/error states; review screenshots of both portals.
- [x] Add explicit fictional demo-data command, Compose volumes for PostgreSQL and private files, and backup/restore instructions.
- [x] Run 56 Django tests, the Playwright journey, migration checks, Django checks, and visual review. `check --deploy` reports seven expected development-environment warnings; see the operations guide for production setup.

## Verification commands

- `python manage.py test`
- `python manage.py makemigrations --check --dry-run`
- `python manage.py check --deploy` (review expected deployment warnings against environment configuration)
- `python -m playwright install chromium` followed by the documented browser journey command
- `docker compose config`

## Results

- PostgreSQL suite: 56 tests passed; Django `check` reported no issues; `makemigrations --check --dry-run` found no missing migration.
- Playwright: the complete applicant/reviewer correction journey passed against a temporary PostgreSQL database; desktop and mobile layouts, skip-link keyboard focus, and no horizontal overflow were checked.
- Persistence: after restarting app and worker, the two demo applications and all six private files remained; all six synthetic OCR jobs were `clear`, and three email outbox entries were `sent`.
- Deployment check: `check --deploy` reported seven expected warnings for the development server, DEBUG, development secret, HTTP cookies, TLS/HSTS, and same-origin framing. No production deployment was performed.

## Scope note

External academic-system integration, payments, native mobile apps, official-source validation, and accuracy measurement on real authorized pilot records remain outside this MVP.
