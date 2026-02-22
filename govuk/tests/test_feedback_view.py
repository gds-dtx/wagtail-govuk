import importlib
from contextlib import contextmanager

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import clear_url_caches, set_urlconf

from govuk.models import Feedback


def _feature_flags(*, feedback_enabled: bool) -> dict[str, bool]:
    return {
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": feedback_enabled,
    }


def _reload_project_urls():
    from govuk import urls as project_urls

    importlib.reload(project_urls)
    clear_url_caches()
    set_urlconf(None)


@contextmanager
def _with_feedback_feature(enabled: bool):
    with override_settings(FEATURE_FLAGS=_feature_flags(feedback_enabled=enabled)):
        _reload_project_urls()
        yield
    _reload_project_urls()


class FeedbackViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="feedback-user",
            first_name="Jamie",
            last_name="Example",
            email="jamie@example.gov.uk",
            password="unused-password",
        )

    def test_anonymous_user_sees_sign_in_prompt(self):
        with _with_feedback_feature(True):
            response = self.client.get("/feedback/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You need to sign in before you can send feedback.")
        self.assertContains(response, "/login/?next=%2Ffeedback%2F")

    def test_feedback_route_without_trailing_slash_is_available(self):
        with _with_feedback_feature(True):
            response = self.client.get("/feedback")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/login/?next=%2Ffeedback")

    def test_sign_in_url_preserves_valid_feedback_type_query_param(self):
        with _with_feedback_feature(True):
            response = self.client.get(
                "/feedback/?feedback_type=content_request",
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, "/login/?next=%2Ffeedback%2F%3Ffeedback_type%3Dcontent_request"
        )

    def test_authenticated_submission_creates_feedback_entry(self):
        with _with_feedback_feature(True):
            self.client.force_login(self.user)

            response = self.client.post(
                "/feedback/",
                data={
                    "feedback_type": Feedback.FeedbackType.CORRECTION,
                    "comments": "The search page result count is wrong.",
                    "referrer": "https://example.gov.uk/search/?query=test",
                },
                HTTP_USER_AGENT=(
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                    "Mobile/15E148 Safari/604.1"
                ),
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/feedback/?submitted=1")

        feedback = Feedback.objects.get()
        self.assertEqual(feedback.name, "Jamie Example")
        self.assertEqual(feedback.email, "jamie@example.gov.uk")
        self.assertEqual(feedback.feedback_type, Feedback.FeedbackType.CORRECTION)
        self.assertEqual(feedback.comments, "The search page result count is wrong.")
        self.assertEqual(feedback.referrer, "https://example.gov.uk/search/?query=test")
        self.assertEqual(feedback.browser, "Safari")
        self.assertTrue(feedback.is_mobile)
        self.assertIsNotNone(feedback.created_at)

    def test_submission_uses_header_referrer_when_hidden_field_missing(self):
        with _with_feedback_feature(True):
            self.client.force_login(self.user)

            self.client.post(
                "/feedback/",
                data={
                    "feedback_type": Feedback.FeedbackType.GENERAL,
                    "comments": "General feedback",
                },
                HTTP_REFERER="https://example.gov.uk/content/article/",
                HTTP_USER_AGENT=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/121.0.0.0 Safari/537.36"
                ),
            )

        feedback = Feedback.objects.get()
        self.assertEqual(feedback.referrer, "https://example.gov.uk/content/article/")
        self.assertEqual(feedback.browser, "Chrome")
        self.assertFalse(feedback.is_mobile)

    def test_get_prefills_feedback_type_from_valid_query_param(self):
        with _with_feedback_feature(True):
            self.client.force_login(self.user)
            response = self.client.get(
                "/feedback/?feedback_type=content_request",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"]["feedback_type"].value(),
            Feedback.FeedbackType.CONTENT_REQUEST,
        )

    def test_get_ignores_invalid_feedback_type_query_param(self):
        with _with_feedback_feature(True):
            self.client.force_login(self.user)
            response = self.client.get(
                "/feedback/?feedback_type=not-a-valid-choice",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["form"]["feedback_type"].value(),
            Feedback.FeedbackType.GENERAL,
        )


class FeedbackFeatureFlagTests(TestCase):
    def test_feedback_view_returns_404_when_disabled(self):
        with _with_feedback_feature(False):
            response = self.client.get("/feedback/")

        self.assertEqual(response.status_code, 404)
