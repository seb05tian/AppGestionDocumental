from django.http import JsonResponse
from django.shortcuts import redirect, render


def is_management_dialog(request):
    return request.headers.get("X-Management-Dialog") == "1"


def build_management_form(request, form_type, **kwargs):
    data = request.POST if request.method == "POST" else None
    auto_id = "dialog_%s" if is_management_dialog(request) else "id_%s"
    return form_type(data, auto_id=auto_id, **kwargs)


def render_management_form(request, page, context):
    if is_management_dialog(request):
        status = 422 if request.method == "POST" else 200
        return render(request, "admisiones/_management_form.html", {**context, "as_dialog": True}, status=status)
    return render(request, page, context)


def complete_management_form(request, destination):
    if is_management_dialog(request):
        return JsonResponse({"location": destination})
    return redirect(destination)
