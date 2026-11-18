from django.db.models import Q

from admisiones.models import Application, EmailOutbox, Notification


def notifications_for(user):
    return Notification.objects.filter(
        Q(application__in=Application.for_applicant(user)) | Q(application__isnull=True, user=user)
    )


def create_notification(applicant, application, kind, title, message):
    notification = Notification.objects.create(
        user=applicant.user,
        application=application,
        kind=kind,
        title=title,
        message=message,
    )
    if applicant.email:
        EmailOutbox.objects.create(notification=notification, recipient=applicant.email)
    return notification
