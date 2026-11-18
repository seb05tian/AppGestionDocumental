# Visual system

## Surface

Operate interface for applicants and admissions reviewers. The primary job is scanning an application, identifying what needs attention, and recording a review with minimal navigation.

## Direction

Use the institutional direction in the brief: restrained deep blue for the admissions shell and primary actions, a cool light gray canvas, white work surfaces, and a limited teal accent. Keep common web navigation and controls. The signature move is a clear side-by-side review workspace: document preview beside extracted facts, alerts, and decision controls.

## Tokens

The implementation in `static/admisiones/css/app.css` is the source of truth for CSS custom properties. Keep color, type, spacing, radii, shadow, widths, and motion durations centralized there. Keep state meaning visible in text as well as color.

## Interaction

- Desktop admissions and administration share a left navigation rail with grouped options, menu search, and a compact mode. The current section is highlighted and labeled for assistive technology.
- Applicant pages use a simple top navigation and a single-column layout on narrow screens. The application list and document requirements use distinct rows with explicit actions.
- The admissions table remains visible across the desktop workspace. Below 1241px, rows reflow into labeled cards. On phones, "Menú de trabajo" opens the navigation as a drawer and unused filters start collapsed. Selected filters stay open.
- Forms show labels, inline validation, success/error feedback, and visible keyboard focus.
- Listings use one filter panel with a search field, labeled controls, and an action to clear applied filters. Single selects use the same rounded field, arrow, and focus treatment in filters, forms, and dialogs while keeping native keyboard and mobile behavior.
- Staff forms for applicant intake and catalog editing open in an accessible dialog. Its header and actions stay in place while the form body scrolls. Staff tables share a navy header, clear statuses, and labeled row actions. On narrow screens, rows keep their actions visible.
- Document preview sits beside extracted facts and human decisions on desktop. On phones it comes first at a shorter height, with a download action when embedded preview is unavailable.
- Motion acknowledges button presses, reveals server feedback and disclosures, and stops spatial effects under `prefers-reduced-motion`. Routine pages do not use entrance sequences.

## Constraints

Do not add decorative charts, marketing claims, false metrics, institution logos, external AI language, or OCR confidence percentages. No applicant-facing screen should expose implementation details.
