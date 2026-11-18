# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

delegated: Django monolith with PostgreSQL and server-rendered templates, as specified in the brief.

## Users

- Aspirants managing only their own application and documents.
- Admissions reviewers checking files, requesting corrections, and recording decisions.
- Institutional administrators managing users, programs, calls, and document requirements.

## Product Purpose

Asistente de revisión documental centralizes applicant files, highlights missing requirements, records human review, and performs local rule-based prevalidation to reduce admissions paperwork.

## Positioning

The product combines private, versioned applicant files and auditable human decisions with local OCR prevalidation. Automated checks surface possible inconsistencies; they do not prove authenticity or determine admission.

## Operating Context

Admissions staff review applications for programs and calls. Applicants submit required files and replace items when asked. The application is independent of the academic system; future integration is an open institutional decision.

## Capabilities and Constraints

- Roles: applicant, admissions reviewer, administrator. There is no public staff registration.
- Initial document requirements: identity document, Saber 11 results, and graduation record or diploma as alternatives.
- PDF, JPG, and PNG files are private, versioned, server-validated, and limited by configuration.
- Local PDF text extraction and Tesseract OCR run in a Django worker command. No external AI or official-source checks.
- Human review controls document and application decisions. OCR never approves or rejects a file.
- SMTP and privacy notice content/version are configurable. Institutional retention and deletion rules remain open decisions.
- Colombian Spanish is the initial interface language.

## Brand Commitments

The product name is “Asistente de revisión documental”. The brief requires a sober institutional interface with a very light gray background, white surfaces, deep blue primary color, and a moderate accent. No institution-specific logo or identity asset was supplied.

## Evidence on Hand

The product requirements in the user brief are the only confirmed source. No real applicant records, documents, logos, testimonials, or performance baseline were supplied; none should be invented.

## Product Principles

- Keep applicants' files private and accessible only through permission-checked views.
- Make each human decision and state change traceable.
- Keep manual review available when OCR is inconclusive or fails.
- Describe prevalidation as a limited aid, never as authentication.
- Use real application data for indicators and clearly label synthetic demo data.

## Accessibility & Inclusion

The interface must support keyboard navigation, visible focus, associated labels, adequate contrast, and responsive use on mobile and desktop.
