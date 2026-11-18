from datetime import timedelta

from django.db.models import Count, F, Min, Q

from admisiones.models import Application, PrevalidationResult, StateHistory


def _average(durations):
    return timedelta(seconds=sum(item.total_seconds() for item in durations) / len(durations)) if durations else None


def _duration_display(duration):
    if duration is None:
        return "Sin datos"
    total_minutes = max(0, int(duration.total_seconds() // 60))
    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days} d")
    if hours:
        parts.append(f"{hours} h")
    if minutes or not parts:
        parts.append(f"{minutes} min")
    return " ".join(parts)


def compute_indicators():
    status_counts = dict(
        Application.objects.values_list("state").annotate(total=Count("id")).values_list("state", "total")
    )
    # One query: the first review and first approval times come from the history, grouped per application.
    timeline = Application.objects.filter(submitted_at__isnull=False).annotate(
        first_review_at=Min("history__created_at", filter=Q(
            history__from_state=Application.State.SUBMITTED, history__to_state=Application.State.IN_REVIEW,
        )),
        approved_at=Min("history__created_at", filter=Q(history__to_state=Application.State.APPROVED)),
    ).values_list("submitted_at", "first_review_at", "approved_at")
    first_review_times, approval_times = [], []
    submitted_count = 0
    for submitted_at, first_review_at, approved_at in timeline:
        submitted_count += 1
        if first_review_at:
            first_review_times.append(first_review_at - submitted_at)
        if approved_at:
            approval_times.append(approved_at - submitted_at)

    corrected = StateHistory.objects.filter(to_state=Application.State.CORRECTION).values("application_id").distinct().count()
    current_document_issues = PrevalidationResult.objects.filter(
        status__in=(PrevalidationResult.Status.INCONCLUSIVE, PrevalidationResult.Status.ERROR),
        version__requirement__required=True,
        version__requirement__current_version_id=F("version_id"),
    ).count()
    first_review_average = _average(first_review_times)
    approval_average = _average(approval_times)
    return {
        "status_counts": [(state, label, status_counts.get(state, 0)) for state, label in Application.State.choices],
        "pending_review_count": Application.objects.filter(
            state__in=(Application.State.SUBMITTED, Application.State.IN_REVIEW)
        ).count(),
        "first_review_average": first_review_average,
        "approval_average": approval_average,
        "first_review_display": _duration_display(first_review_average),
        "approval_display": _duration_display(approval_average),
        "correction_percent": (corrected * 100 / submitted_count) if submitted_count else None,
        "correction_count": corrected,
        "submitted_count": submitted_count,
        "automatic_issue_count": current_document_issues,
    }
