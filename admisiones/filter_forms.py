from urllib.parse import urlencode

from django import forms

from .models import Application, Call, Program


class ListingFilters(forms.Form):
    q = forms.CharField(label="Buscar", required=False, widget=forms.SearchInput(attrs={"maxlength": 120}))

    def __init__(self, data, *, search_placeholder):
        super().__init__(data)
        self.fields["q"].widget.attrs["placeholder"] = search_placeholder

    def clean_q(self):
        return self.cleaned_data["q"][:120]

    def query_string(self):
        return urlencode({
            name: self.fields[name].prepare_value(value)
            for name, value in self.cleaned_data.items() if value
        })


class QueueFilters(ListingFilters):
    state = forms.ChoiceField(label="Estado", required=False, choices=[("", "En gestión"), *Application.State.choices])
    program = forms.ModelChoiceField(
        label="Programa", queryset=Program.objects.order_by("name"), required=False, empty_label="Todos",
    )
    call = forms.ModelChoiceField(
        label="Convocatoria", queryset=Call.objects.all(), required=False, empty_label="Todas",
    )
    assignment = forms.ChoiceField(label="Asignación", required=False, choices=[
        ("", "Todas"), ("mine", "A mí"), ("unassigned", "Sin asignar"), ("others", "A otros"),
    ])
    alerts = forms.ChoiceField(label="Alertas automáticas", required=False, choices=[
        ("", "Todas"), ("yes", "Con alertas"), ("no", "Sin alertas"),
    ])


class CatalogFilters(ListingFilters):
    status = forms.ChoiceField(label="Estado", required=False, choices=[
        ("", "Todos los estados"), ("active", "Activos"), ("inactive", "Desactivados"),
    ])


class ApplicantFilters(ListingFilters):
    account = forms.ChoiceField(label="Cuenta de acceso", required=False, choices=[
        ("", "Todas las personas"), ("linked", "Con cuenta"), ("unlinked", "Sin cuenta"),
    ])
