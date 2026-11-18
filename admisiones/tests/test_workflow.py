from datetime import timedelta
from importlib import import_module

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from admisiones.forms import ApplicationForm

from admisiones.models import Applicant, Application, ApplicationRequirement, Call, DocumentVersion, EmailOutbox, Notification, Observation, Program, RequirementDefinition, Review, StateHistory, User


class ApplicationWorkflowTests(TestCase):
    def test_history_displays_application_states_in_spanish(self):
        self.assertEqual(StateHistory(to_state=Application.State.SUBMITTED).get_to_state_display(), "Enviado")
        self.assertEqual(StateHistory(to_state=Application.State.CORRECTION).get_to_state_display(), "Pendiente de corrección")

    def setUp(self):
        self.applicant = User.objects.create_user(
            email="aspirante@example.test", password="Fuerte-2026-Clave!", role=User.Role.APPLICANT
        )
        Applicant.objects.create(user=self.applicant, email=self.applicant.email)
        self.reviewer = User.objects.create_user(
            email="revisor@example.test", password="Fuerte-2026-Clave!", role=User.Role.REVIEWER
        )
        self.program = Program.objects.create(name="Ingeniería de Sistemas")
        self.call = Call.objects.create(name="Convocatoria 2027-1", active=True)

    def submit_with_files(self, application):
        for index, slot in enumerate(application.requirements.all(), start=1):
            version = DocumentVersion.objects.create(
                requirement=slot, version_number=1, storage_key=f"x/{index}.pdf", original_name=f"{index}.pdf",
                content_type="application/pdf", byte_size=10, sha256=str(index) * 64, uploaded_by=self.applicant,
            )
            slot.current_version = version
            slot.save(update_fields=("current_version",))
        transition = import_module("admisiones.services.state").transition_application
        return transition(application, Application.State.SUBMITTED, self.applicant)

    def test_application_creation_snapshots_requirements_and_blocks_duplicates(self):
        service = import_module("admisiones.services.workflow")
        create = getattr(service, "create_application", None)
        self.assertTrue(callable(create), "create_application must create the request and snapshot its requirements")
        application = create(self.applicant, self.program, self.call)
        snapshots = {item.code: item for item in application.requirements.all()}
        self.assertEqual(set(snapshots), {"identity", "saber11", "graduation"})
        RequirementDefinition.objects.filter(code="identity", call__isnull=True).update(name="Nuevo nombre")
        self.assertEqual(snapshots["identity"].name, "Documento de identidad")
        with self.assertRaises(ValidationError):
            create(self.applicant, self.program, self.call)

    def test_call_outside_its_dates_does_not_accept_new_requests(self):
        service = import_module("admisiones.services.workflow")
        today = timezone.localdate()
        self.call.closes_at = today - timedelta(days=1)
        self.call.save()
        with self.assertRaises(ValidationError):
            service.create_application(self.applicant, self.program, self.call)
        self.call.closes_at, self.call.opens_at = None, today + timedelta(days=1)
        self.call.save()
        with self.assertRaises(ValidationError):
            service.create_application(self.applicant, self.program, self.call)
        self.call.opens_at = today
        self.call.closes_at = today
        self.call.save()
        self.assertEqual(service.create_application(self.applicant, self.program, self.call).state, Application.State.DRAFT)

    def test_application_form_only_offers_calls_open_today(self):
        today = timezone.localdate()
        closed = Call.objects.create(name="Cerrada", closes_at=today - timedelta(days=1))
        offered = list(ApplicationForm().fields["call"].queryset)
        self.assertIn(self.call, offered)
        self.assertNotIn(closed, offered)

    def test_administrator_releases_an_application_so_another_reviewer_can_take_it(self):
        service = import_module("admisiones.services.workflow")
        application = service.create_application(self.applicant, self.program, self.call)
        self.submit_with_files(application)
        service.assign_to_self(application, self.reviewer)
        other = User.objects.create_user(email="otro@example.test", password="Fuerte-2026-Clave!", role=User.Role.REVIEWER)
        administrator = User.objects.create_user(email="admin@example.test", password="Fuerte-2026-Clave!", role=User.Role.ADMINISTRATOR)
        with self.assertRaises(PermissionDenied):
            service.release_application(application, self.reviewer)
        released = service.release_application(application, administrator)
        self.assertIsNone(released.assigned_reviewer)
        self.assertEqual(released.state, Application.State.IN_REVIEW)
        service.assign_to_self(application, other)
        self.assertEqual(Application.objects.get(pk=application.pk).assigned_reviewer, other)

    def test_only_administrators_can_use_the_release_endpoint(self):
        service = import_module("admisiones.services.workflow")
        application = service.create_application(self.applicant, self.program, self.call)
        self.submit_with_files(application)
        service.assign_to_self(application, self.reviewer)
        self.client.force_login(self.reviewer)
        response = self.client.post(f"/admisiones/expedientes/{application.public_id}/liberar/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Application.objects.get(pk=application.pk).assigned_reviewer, self.reviewer)

    def test_only_applicants_can_create_requests(self):
        service = import_module("admisiones.services.workflow")
        create = getattr(service, "create_application", None)
        self.assertTrue(callable(create))
        with self.assertRaises(PermissionDenied):
            create(self.reviewer, self.program, self.call)

    def test_explicit_call_configuration_with_no_active_requirements_does_not_fall_back(self):
        service = import_module("admisiones.services.workflow")
        call = Call.objects.create(name="Convocatoria sin requisitos", active=True)
        RequirementDefinition.objects.create(
            call=call, code="custom", name="Requisito desactivado", active=False
        )
        with self.assertRaises(ValidationError):
            service.create_application(self.applicant, self.program, call)

    def test_submitting_with_missing_files_is_rejected(self):
        workflow = import_module("admisiones.services.workflow")
        create = getattr(workflow, "create_application", None)
        transition = getattr(import_module("admisiones.services.state"), "transition_application", None)
        self.assertTrue(callable(create))
        self.assertTrue(callable(transition), "state transitions must be centralized on the server")
        application = create(self.applicant, self.program, self.call)
        with self.assertRaises(ValidationError):
            transition(application, Application.State.SUBMITTED, self.applicant)

    def test_call_with_only_optional_requirements_can_be_submitted_and_approved(self):
        workflow = import_module("admisiones.services.workflow")
        transition = import_module("admisiones.services.state").transition_application
        RequirementDefinition.objects.create(
            call=self.call, code="optional", name="Soporte adicional", required=False,
        )
        application = workflow.create_application(self.applicant, self.program, self.call)
        application = transition(application, Application.State.SUBMITTED, self.applicant)
        application = workflow.assign_to_self(application, self.reviewer)
        application = transition(application, Application.State.APPROVED, self.reviewer)
        self.assertEqual(application.state, Application.State.APPROVED)

    def test_reviewer_assignment_cannot_be_overwritten(self):
        workflow = import_module("admisiones.services.workflow")
        create = getattr(workflow, "create_application", None)
        assign = getattr(workflow, "assign_to_self", None)
        conflict = getattr(workflow, "ReviewConflict", None)
        self.assertTrue(callable(create) and callable(assign) and conflict is not None)
        application = create(self.applicant, self.program, self.call)
        application = self.submit_with_files(application)
        application = assign(application, self.reviewer)
        application.refresh_from_db()
        self.assertEqual(application.assigned_reviewer_id, self.reviewer.id)
        self.assertEqual(application.state, Application.State.IN_REVIEW)
        another = User.objects.create_user(email="segundo@example.test", password="Fuerte-2026-Clave!", role=User.Role.REVIEWER)
        with self.assertRaises(conflict):
            assign(application, another)

    def test_correction_requires_reason_and_stale_version_conflicts(self):
        workflow = import_module("admisiones.services.workflow")
        create = getattr(workflow, "create_application", None)
        assign = getattr(workflow, "assign_to_self", None)
        review = getattr(workflow, "review_document", None)
        conflict = getattr(workflow, "ReviewConflict", None)
        self.assertTrue(callable(create) and callable(assign) and callable(review) and conflict is not None)
        application = create(self.applicant, self.program, self.call)
        self.submit_with_files(application)
        slot = application.requirements.get(code="identity")
        first = slot.current_version
        assign(application, self.reviewer)
        with self.assertRaises(ValidationError):
            review(slot, first.id, self.reviewer, Review.Decision.CORRECTION, reason="")
        second = DocumentVersion.objects.create(
            requirement=slot, version_number=2, storage_key="x/second.pdf", original_name="second.pdf",
            content_type="application/pdf", byte_size=10, sha256="b" * 64, uploaded_by=self.applicant,
        )
        slot.current_version = second
        slot.save(update_fields=("current_version",))
        with self.assertRaises(conflict):
            review(slot, first.id, self.reviewer, Review.Decision.APPROVE)
        review(slot, second.id, self.reviewer, Review.Decision.CORRECTION, reason="La imagen está recortada.")
        application.refresh_from_db()
        self.assertEqual(application.state, Application.State.IN_REVIEW)
        self.assertFalse(Notification.objects.filter(application=application, kind=Notification.Kind.CORRECTION).exists())
        for other_slot in application.requirements.exclude(pk=slot.pk):
            review(other_slot, other_slot.current_version_id, self.reviewer, Review.Decision.APPROVE)
        workflow.send_corrections(application, self.reviewer)
        application.refresh_from_db()
        self.assertEqual(application.state, Application.State.CORRECTION)
        self.assertTrue(Observation.objects.filter(application=application, audience=Observation.Audience.APPLICANT, text__contains="La imagen está recortada.").exists())
        notice = Notification.objects.get(application=application, kind=Notification.Kind.CORRECTION)
        self.assertTrue(EmailOutbox.objects.filter(notification=notice, recipient=self.applicant.email).exists())

    def test_cannot_approve_application_with_pending_documents(self):
        workflow = import_module("admisiones.services.workflow")
        create = getattr(workflow, "create_application", None)
        assign = getattr(workflow, "assign_to_self", None)
        transition = getattr(import_module("admisiones.services.state"), "transition_application", None)
        self.assertTrue(callable(create) and callable(assign) and callable(transition))
        application = create(self.applicant, self.program, self.call)
        application = self.submit_with_files(application)
        application = assign(application, self.reviewer)
        with self.assertRaises(ValidationError):
            transition(application, Application.State.APPROVED, self.reviewer)

    def test_reopening_requires_reason_and_is_recorded(self):
        workflow = import_module("admisiones.services.workflow")
        create = getattr(workflow, "create_application", None)
        transition = getattr(import_module("admisiones.services.state"), "transition_application", None)
        self.assertTrue(callable(create) and callable(transition))
        application = create(self.applicant, self.program, self.call)
        application.state = Application.State.APPROVED
        application.assigned_reviewer = self.reviewer
        application.save()
        with self.assertRaises(ValidationError):
            transition(application, Application.State.IN_REVIEW, self.reviewer)
        transition(application, Application.State.IN_REVIEW, self.reviewer, reason="Verificar documento reemplazado por error.")
        application.refresh_from_db()
        self.assertEqual(application.state, Application.State.IN_REVIEW)
        self.assertEqual(application.history.order_by("-id").first().reason, "Verificar documento reemplazado por error.")
        self.assertTrue(Notification.objects.filter(application=application, kind=Notification.Kind.REOPENED).exists())

    def test_closing_requires_a_fully_reviewed_folder_and_reopening_starts_a_new_round(self):
        workflow = import_module("admisiones.services.workflow")
        create = getattr(workflow, "create_application", None)
        assign = getattr(workflow, "assign_to_self", None)
        transition = getattr(import_module("admisiones.services.state"), "transition_application", None)
        application = create(self.applicant, self.program, self.call)
        application = self.submit_with_files(application)
        application = assign(application, self.reviewer)
        for slot in application.requirements.all():
            Review.objects.create(
                version=slot.current_version,
                reviewer=self.reviewer,
                decision=Review.Decision.APPROVE,
                review_round=application.review_round,
            )
        application = transition(application, Application.State.APPROVED, self.reviewer)
        application = transition(application, Application.State.CLOSED, self.reviewer)
        self.assertEqual(application.state, Application.State.CLOSED)
        application = transition(
            application, Application.State.IN_REVIEW, self.reviewer, reason="Revisión posterior solicitada."
        )
        self.assertEqual(application.review_round, 2)
        self.assertEqual(application.requirements.get(code="identity").document_state(), "pending_review")
        self.assertTrue(Notification.objects.filter(application=application, kind=Notification.Kind.REOPENED).exists())

    def test_same_version_has_one_decision_per_review_round(self):
        workflow = import_module("admisiones.services.workflow")
        create = getattr(workflow, "create_application", None)
        assign = getattr(workflow, "assign_to_self", None)
        review = getattr(workflow, "review_document", None)
        transition = getattr(import_module("admisiones.services.state"), "transition_application", None)
        conflict = getattr(workflow, "ReviewConflict", None)
        self.assertTrue(callable(create) and callable(assign) and callable(review) and callable(transition) and conflict is not None)
        application = create(self.applicant, self.program, self.call)
        self.submit_with_files(application)
        slot = application.requirements.get(code="identity")
        version = slot.current_version
        assign(application, self.reviewer)
        review(slot, version.id, self.reviewer, Review.Decision.APPROVE)
        with self.assertRaises(conflict):
            review(slot, version.id, self.reviewer, Review.Decision.APPROVE)
        application.refresh_from_db()
        application.state = Application.State.APPROVED
        application.save(update_fields=("state",))
        transition(application, Application.State.IN_REVIEW, self.reviewer, reason="Revisión reabierta.")
        application.refresh_from_db()
        self.assertEqual(application.review_round, 2)
        self.assertEqual(slot.document_state(), "pending_review")
        self.assertEqual(version.reviews.count(), 1)
        review(slot, version.id, self.reviewer, Review.Decision.APPROVE)
        self.assertEqual(version.reviews.count(), 2)
