# Asistente de revisión documental — design

## Intent

Build an independent admissions document workflow for applicants, reviewers, and administrators. Applicants create and update their own applications; reviewers inspect files and record decisions; administrators configure institutional data. Rule-based local prevalidation assists a human but never determines authenticity or admission.

## Goals

- Complete the application, upload, review, correction, and resubmission loop.
- Keep files private, versioned, and auditable.
- Make manual review independent of OCR availability.
- Provide real queue metrics, notifications, and safe CSV export.
- Ship reproducible local setup, demo seed data, tests, and operational documentation.

## Non-goals

Payments, enrollment, mobile-native clients, multiple institutions, biometrics, fraud detection, official-source validation, academic-system integration, chatbots, and external AI processing.

## Constraints

- Django monolith, PostgreSQL, and server-rendered templates.
- Local Tesseract OCR, with jobs stored in PostgreSQL and processed by a Django command.
- Private persistent file storage shared by the web and worker containers.
- No public staff signup; Django admin configures staff and institutional data.
- Colombian Spanish interface, keyboard access, visible focus, and responsive layouts.
- Retention and deletion rules and the final institutional privacy notice require institutional decisions.

## Approaches

1. **Django monolith with server-rendered pages and a DB-backed worker (selected).** Keeps data, permissions, review, and configuration in one deployable application; matches the requested workflow and avoids extra infrastructure.
2. **Django API plus separate SPA.** Adds build/runtime layers and duplicated permission/UI state without improving this internal file-review MVP.
3. **Microservices with a broker.** Adds deployment and operational work, and conflicts with the explicit request to use a simple Django worker without Redis or Celery.

## Architecture

One Django project and admissions app contain the custom user, programs, calls, immutable per-application requirement snapshots, application/document versions, OCR results/jobs, review history, state history, notifications, and email outbox. Server views enforce role and object ownership on every route. Uploads go to a non-public persistent directory after signature/content validation. The worker atomically claims jobs, extracts text from PDFs first, uses local Tesseract for image/scanned pages, and records explicit rules/results without any approval side effect.

## Main decisions

- Django 5.2 LTS and PostgreSQL; templates render the applicant and reviewer interfaces.
- Applicant identity is attached to the account; public registration creates applicant accounts only.
- A request snapshots its required items and accepted document alternatives when created.
- Each requirement slot points to one current immutable file version; past versions and reviews remain available to authorized staff.
- Replacing a file creates a new version and a unique OCR job. Processing results are read from the current version only.
- State changes go through a centralized service inside database transactions. Review forms submit the version ID they displayed; stale versions or another reviewer assignment produce a conflict response.
- `clean`, `review`, `inconclusive`, and `technical_error` are OCR outcomes, separate from the human document state.
- Notifications are in-app first; a retryable email outbox is best-effort and contains no full identity number or attachments.
- Demo content is created only by an explicit command and includes no real documents.

## Visual direction

Follow the supplied institutional palette and layout requirements. Reviewer work uses a side navigation and a two-column document/review workspace; applicant pages simplify to a mobile-first single column. CSS custom properties centralize the visual tokens.

## Open institutional decisions

- Approved privacy notice and version.
- Retention period, archival policy, and secure deletion procedure.
- Production SMTP credentials, hostnames, TLS, and sender identity.
- Production domain, secret management, HTTPS/proxy policy, backup schedule, and recovery objectives.
- OCR rule tuning and accuracy measured with authorized pilot documents; synthetic tests prove technical operation only.
