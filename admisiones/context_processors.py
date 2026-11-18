from .services.notifications import notifications_for


def navigation_context(request):
    count = 0
    if request.user.is_authenticated:
        count = notifications_for(request.user).filter(read_at__isnull=True).count()
    return {"unread_notifications_count": count}
