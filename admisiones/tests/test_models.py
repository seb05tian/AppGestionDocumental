from django.test import SimpleTestCase, TestCase

from admisiones import models


class DomainModelContractTests(SimpleTestCase):
    def test_user_defines_applicant_reviewer_and_administrator_roles(self):
        values = {value for value, _ in models.User.Role.choices}
        self.assertEqual(values, {"applicant", "reviewer", "administrator"})

    def test_application_has_database_level_duplicate_guard(self):
        self.assertTrue(hasattr(models, "Application"))
        constraints = models.Application._meta.constraints
        self.assertTrue(any("applicant" in c.fields and "program" in c.fields and "call" in c.fields for c in constraints))

    def test_processing_is_unique_per_immutable_document_version(self):
        self.assertTrue(hasattr(models, "ProcessingJob"))
        self.assertTrue(any(c.fields == ("version",) for c in models.ProcessingJob._meta.constraints))

    def test_reopened_review_round_keeps_prior_decisions_and_prevents_duplicates(self):
        self.assertIn("review_round", [field.name for field in models.Application._meta.fields])
        self.assertIn("review_round", [field.name for field in models.Review._meta.fields])
        self.assertTrue(any(c.fields == ("version", "review_round") for c in models.Review._meta.constraints))


class InitialConfigurationTests(TestCase):
    def test_initial_requirements_are_the_three_documents_of_the_admissions_file(self):
        codes = set(models.RequirementDefinition.objects.filter(call__isnull=True).values_list("code", flat=True))
        self.assertEqual(codes, {"identity", "saber11", "graduation"})

    def test_privacy_notice_is_seeded_as_a_draft_for_institutional_replacement(self):
        notice = models.PrivacyNotice.objects.get(active=True)
        self.assertIn("borrador", notice.text.lower())


class RequirementOrderTests(TestCase):
    def test_new_requirement_goes_last_in_its_call_without_typing_an_order(self):
        call = models.Call.objects.create(name="Convocatoria de orden")
        first = models.RequirementDefinition.objects.create(call=call, code="a", name="A")
        second = models.RequirementDefinition.objects.create(call=call, code="b", name="B")
        self.assertGreater(second.position, first.position)
        self.assertEqual(list(models.RequirementDefinition.objects.filter(call=call)), [first, second])
