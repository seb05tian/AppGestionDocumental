from django.core.paginator import Paginator
from django.db.models import Count, F, Q

from admisiones.filter_forms import QueueFilters
from admisiones.models import Applicant, Application, Review, User


def reviewer_queue(request, paginate=True):
    filters = QueueFilters(request.GET, search_placeholder="Nombre o documento")
    is_admin = request.user.role == User.Role.ADMINISTRATOR
    if is_admin:
        filters.fields["state"].choices = [
            ("", "Todos los estados"), ("active", "En gestión"), *Application.State.choices,
        ]
    filters.is_valid()
    selected = filters.cleaned_data
    applications = Application.objects.all()
    if not is_admin:
        applications = applications.exclude(state=Application.State.DRAFT, origin=Application.Origin.PORTAL)
    state = selected.get("state")
    if state == "active" or (not is_admin and not state):
        applications = applications.exclude(state__in=(Application.State.APPROVED, Application.State.CLOSED))
    elif state:
        applications = applications.filter(state=state)
    if query := selected.get("q"):
        applications = applications.filter(applicant__in=Applicant.matching_text(query))
    if program := selected.get("program"):
        applications = applications.filter(program=program)
    if call := selected.get("call"):
        applications = applications.filter(call=call)
    assignment = selected.get("assignment")
    if assignment == "mine":
        applications = applications.filter(assigned_reviewer=request.user)
    elif assignment == "unassigned":
        applications = applications.filter(assigned_reviewer__isnull=True)
    elif assignment == "others":
        applications = applications.exclude(assigned_reviewer__isnull=True).exclude(assigned_reviewer=request.user)

    applications = applications.annotate(
        required_count=Count("requirements", filter=Q(requirements__required=True), distinct=True),
        uploaded_count=Count("requirements", filter=Q(requirements__required=True, requirements__current_version__isnull=False), distinct=True),
        approved_count=Count(
            "requirements",
            filter=Q(
                requirements__required=True,
                requirements__current_version__reviews__decision=Review.Decision.APPROVE,
                requirements__current_version__reviews__review_round=F("review_round"),
            ),
            distinct=True,
        ),
        alert_count=Count(
            "requirements",
            filter=Q(
                requirements__required=True,
                requirements__current_version__prevalidation__status="review",
            ),
            distinct=True,
        ),
    ).select_related("applicant", "program", "call", "assigned_reviewer").order_by(
        F("submitted_at").asc(nulls_last=True), "created_at", "pk"
    )
    alerts = selected.get("alerts")
    if alerts == "yes":
        applications = applications.filter(alert_count__gt=0)
    elif alerts == "no":
        applications = applications.filter(alert_count=0)

    if not paginate:
        return applications, filters, None
    page = Paginator(applications, 25).get_page(request.GET.get("page"))
    return page.object_list, filters, page
