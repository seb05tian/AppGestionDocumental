# UI Refinement Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement these tasks in order.

**Goal:** Make the applicant and admissions workflows easier to scan and use across desktop and mobile, while preserving the approved institutional palette and all existing behavior.

**Architecture:** Refine the shared Django templates and CSS variables/components. Keep interactions server rendered; use CSS for purposeful feedback and responsive layout. Add no frontend dependency.

**Tech Stack:** Django templates, CSS, Playwright browser checks.

**Spec:** [DESIGN.md](../../../DESIGN.md) and the user's UI request.

## Constraints

- Keep the existing blue, teal, gray, and white palette.
- Preserve private document access, server actions, semantic labels, and keyboard focus.
- Avoid decorative motion; respect `prefers-reduced-motion`.

## Tasks

### 1. Shared hierarchy and states

- [x] Inspect desktop and mobile baselines for the applicant and reviewer paths.
- [x] Adjust title measure, spacing, surface rhythm, active navigation, and component states in `static/admisiones/css/app.css`.
- [x] Make history state labels human readable in `admisiones/models.py` and both detail templates; add a focused regression test.
- [x] Verify login, registration, empty, error, and success states remain readable.

### 2. Applicant path

- [x] Improve the dashboard's application list and the dossier summary/document rows in their existing templates and CSS.
- [x] Verify draft, submitted, correction, and approved views at narrow and wide widths.

### 3. Admissions path

- [x] Keep the queue table fully usable on desktop and present its rows as readable cards on narrower screens.
- [x] Clarify the review workspace and reduce preview height on mobile without obscuring decisions or downloads.
- [x] Keep indicators and notifications consistent with the same components.

### 4. Motion and verification

- [x] Add brief feedback to controls, disclosures, and result notices, with a reduced-motion path.
- [x] Run the Django tests and the full browser journey; inspect representative screenshots at 320, 390, 768, 1280, and 1440 px.
- [x] Update `DESIGN.md` with the final component and motion decisions.
