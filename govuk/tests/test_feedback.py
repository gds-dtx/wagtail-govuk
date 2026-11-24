"""Feedback: the signed-in feedback form and its admin listing, and the "Is this page useful?" prompt.

Each prompt answer is logged and stored as a PageUsefulnessVote row, which the
admin "Page usefulness" report reads and a command prunes.
"""

import importlib
from contextlib import contextmanager
from datetime import timedelta
from io import StringIO
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import clear_url_caches, reverse, set_urlconf
from django.utils import timezone
from wagtail import hooks
from wagtail.models import Site

from govuk.models import CustomiseSettings, Feedback, PageUsefulnessVote
from govuk.tests.helpers import feature_flags, publish_page


def _feedback_flags(enabled: bool) -> dict[str, bool]:
    return {"FEEDBACK": enabled}


def _reload_project_urls():
    from govuk import urls as project_urls

    importlib.reload(project_urls)
    clear_url_caches()
    set_urlconf(None)


@contextmanager
def _with_feedback_feature(enabled: bool):
    # The /feedback route is added at import time, so the URLconf is rebuilt.
    with override_settings(FEATURE_FLAGS=_feedback_flags(enabled)):
        _reload_project_urls()
        yield
    _reload_project_urls()


def _reload_hooks():
    import govuk.wagtail_hooks as hooks_module

    return importlib.reload(hooks_module)


def _switch_on_prompt(site, **overrides) -> CustomiseSettings:
    customise = CustomiseSettings.for_site(site)
    customise.show_page_feedback_prompt = True
    for name, value in overrides.items():
        setattr(customise, name, value)
    customise.save()
    return customise


def _vote(site, answer="yes", path="/job-grades/", *, created_at=None) -> PageUsefulnessVote:
    vote = PageUsefulnessVote.objects.create(answer=answer, path=path, site=site)
    if created_at is not None:
        PageUsefulnessVote.objects.filter(pk=vote.pk).update(created_at=created_at)
    return vote


class FeedbackViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="feedback-user",
            first_name="Jamie",
            last_name="Example",
            email="jamie@example.gov.uk",
            password="unused-password",
        )

    def test_an_anonymous_reader_is_asked_to_sign_in_and_brought_back(self):
        for path, sign_in in (
            ("/feedback/", "/login/?next=%2Ffeedback%2F"),
            ("/feedback", "/login/?next=%2Ffeedback"),
            (
                "/feedback/?feedback_type=content_request",
                "/login/?next=%2Ffeedback%2F%3Ffeedback_type%3Dcontent_request",
            ),
        ):
            with self.subTest(path), _with_feedback_feature(True):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "You need to sign in before you can send feedback.")
                self.assertContains(response, sign_in)

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

    def test_authenticated_submission_redirects_to_named_feedback_route(self):
        with _with_feedback_feature(True):
            self.client.force_login(self.user)

            response = self.client.post(
                "/feedback",
                data={"feedback_type": Feedback.FeedbackType.GENERAL, "comments": "General feedback"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/feedback/?submitted=1")

    def test_submission_uses_header_referrer_when_hidden_field_missing(self):
        with _with_feedback_feature(True):
            self.client.force_login(self.user)

            self.client.post(
                "/feedback/",
                data={"feedback_type": Feedback.FeedbackType.GENERAL, "comments": "General feedback"},
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

    def test_the_feedback_type_is_prefilled_from_a_valid_query_param_only(self):
        for value, expected in (
            ("content_request", Feedback.FeedbackType.CONTENT_REQUEST),
            ("not-a-valid-choice", Feedback.FeedbackType.GENERAL),
        ):
            with self.subTest(value), _with_feedback_feature(True):
                self.client.force_login(self.user)
                response = self.client.get(f"/feedback/?feedback_type={value}")

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["form"]["feedback_type"].value(), expected)

    def test_feedback_view_returns_404_when_disabled(self):
        with _with_feedback_feature(False):
            response = self.client.get("/feedback/")

        self.assertEqual(response.status_code, 404)


class FeedbackWagtailHooksTests(SimpleTestCase):
    def test_the_feedback_snippet_is_registered_only_with_the_flag_on(self):
        for enabled in (True, False):
            with (
                self.subTest(enabled=enabled),
                override_settings(FEATURE_FLAGS=_feedback_flags(enabled)),
                patch("wagtail.snippets.models.register_snippet") as mock_register_snippet,
            ):
                hooks_module = _reload_hooks()
                expected = [call(hooks_module.GovukTagViewSet), call(hooks_module.ExternalContentItemViewSet)]
                if enabled:
                    expected.append(call(hooks_module.FeedbackViewSet))

                mock_register_snippet.assert_has_calls(expected)
                self.assertEqual(mock_register_snippet.call_count, len(expected))
                if not enabled:
                    self.assertNotIn(call(hooks_module.FeedbackViewSet), mock_register_snippet.mock_calls)
            _reload_hooks()

    def test_the_title_links_to_the_inspect_view_falling_back_to_edit(self):
        hooks_module = _reload_hooks()
        for inspect_url, expected in (("/inspect/123/", "/inspect/123/"), (None, "/edit/123/")):
            with self.subTest(inspect_url=inspect_url):
                index_view = hooks_module.FeedbackIndexView(model=Feedback, list_display=["name"])
                index_view.get_inspect_url = lambda obj, url=inspect_url: url
                index_view.get_edit_url = lambda obj: "/edit/123/"

                title_column = index_view._get_title_column("name")
                link_url = title_column.get_link_url(Feedback(name="Example", comments="Body"), {})

                self.assertEqual(link_url, expected)


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class PageFeedbackPromptTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.url = publish_page("Job grades", "<p>The grades.</p>").url

    def _opening_tag(self, html, marker, tag):
        start = html.index(marker)
        return html[html.rfind(f"<{tag}", 0, start) : html.index(">", start)]

    def test_the_prompt_is_off_until_a_site_switches_it_on(self):
        """Every site on the platform shares this template; only the framework asked for the prompt."""
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Is this page useful?")
        self.assertNotContains(response, 'id="page-feedback"')

    def test_the_prompt_is_a_form_with_a_yes_and_a_no(self):
        """Real submit buttons in a real form, so it works before any script runs."""
        _switch_on_prompt(self.site)

        response = self.client.get(self.url)

        self.assertContains(response, "Is this page useful?")
        self.assertContains(response, f'action="{reverse("page_feedback")}"')
        self.assertContains(response, 'name="answer"', count=2)
        self.assertContains(response, 'value="yes"')
        self.assertContains(response, 'value="no"')
        self.assertContains(response, 'name="page" value="/job-grades/"')
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_each_button_is_announced_with_the_question(self):
        """A bare "Yes" or "No" says nothing on its own.

        The question travels with each answer as visually hidden text, while the
        visible label stays exactly "Yes" (WCAG 2.5.3 Label in Name).
        """
        _switch_on_prompt(self.site)

        response = self.client.get(self.url)

        for word in ("Yes", "No"):
            self.assertContains(
                response,
                f'<span class="govuk-visually-hidden">Is this page useful: </span>{word}</button>',
            )

    def test_the_thank_you_shows_after_a_post_without_javascript(self):
        """The view redirects back with ?page_feedback=sent, and the template does what the script would."""
        _switch_on_prompt(self.site)

        response = self.client.get(self.url + "?page_feedback=sent")
        html = response.content.decode("utf-8")

        self.assertIn("hidden", self._opening_tag(html, "js-prompt-questions", "form"))
        self.assertNotIn("hidden", self._opening_tag(html, "js-prompt-success", "div"))
        self.assertContains(response, "Thank you for your feedback")
        self.assertContains(response, 'href="/feedback"')

    def test_the_thank_you_is_hidden_until_someone_answers(self):
        _switch_on_prompt(self.site)

        html = self.client.get(self.url).content.decode("utf-8")

        self.assertIn("hidden", self._opening_tag(html, "js-prompt-success", "div"))

    def test_the_follow_up_wording_and_link_come_from_the_site_settings(self):
        _switch_on_prompt(
            self.site,
            page_feedback_more_url="https://example.gov.uk/survey",
            page_feedback_more_intro="A sentence of our own.",
            page_feedback_more_link_text="Tell us more",
        )

        response = self.client.get(self.url + "?page_feedback=sent")

        self.assertContains(response, 'href="https://example.gov.uk/survey"')
        self.assertContains(response, "A sentence of our own.")
        self.assertContains(response, "Tell us more")

    def test_an_error_page_does_not_ask_whether_it_was_useful(self):
        _switch_on_prompt(self.site)

        response = self.client.get("/no-such-page/")

        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "Is this page useful?", status_code=404)


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class PageFeedbackViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        _switch_on_prompt(self.site)
        self.url = reverse("page_feedback")

    def test_only_post_is_accepted(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_a_site_without_the_prompt_has_no_endpoint(self):
        customise = CustomiseSettings.for_site(self.site)
        customise.show_page_feedback_prompt = False
        customise.save()

        response = self.client.post(self.url, {"answer": "yes", "page": "/"})

        self.assertEqual(response.status_code, 404)

    def test_only_yes_or_no_is_an_answer(self):
        response = self.client.post(self.url, {"answer": "maybe", "page": "/"})

        self.assertEqual(response.status_code, 400)

    def test_the_answer_is_recorded_and_the_reader_sent_back(self):
        """The log line and the stored row are both the server-side record."""
        with self.assertLogs("govuk.page_feedback", level="INFO") as logs:
            response = self.client.post(self.url, {"answer": "Yes", "page": "/job-grades/"})

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], "/job-grades/?page_feedback=sent#page-feedback")
        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertEqual(record.getMessage(), "Page feedback")
        self.assertEqual(record.page_feedback_answer, "yes")
        self.assertEqual(record.page_feedback_path, "/job-grades/")
        self.assertEqual(record.site_hostname, self.site.hostname)

        vote = PageUsefulnessVote.objects.get()
        self.assertEqual(vote.answer, "yes")
        self.assertEqual(vote.path, "/job-grades/")
        self.assertEqual(vote.site, self.site)

    def test_a_background_post_gets_no_content_and_no_redirect(self):
        response = self.client.post(
            self.url, {"answer": "no", "page": "/job-grades/"}, HTTP_X_REQUESTED_WITH="fetch"
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")

    def test_a_page_value_that_is_not_a_local_path_goes_back_to_the_root(self):
        """The path is visitor input, so it cannot become an open redirect."""
        for value in (
            "https://evil.example/x",
            "//evil.example",
            "javascript:alert(1)",
            "/ok\r\nSet-Cookie: x=y",
            "job-grades",
            "",
        ):
            with self.subTest(value=value):
                response = self.client.post(self.url, {"answer": "yes", "page": value})
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response["Location"], "/?page_feedback=sent#page-feedback")

    def test_a_query_string_or_fragment_on_the_page_is_dropped(self):
        response = self.client.post(self.url, {"answer": "yes", "page": "/job-grades/?x=1#frag"})

        self.assertEqual(response["Location"], "/job-grades/?page_feedback=sent#page-feedback")

    def test_the_form_is_protected_against_cross_site_posts(self):
        response = Client(enforce_csrf_checks=True).post(self.url, {"answer": "yes", "page": "/"})

        self.assertEqual(response.status_code, 403)


class PageUsefulnessReportTests(TestCase):
    """The admin "Page usefulness" report: yes/no per path, pooled across sites."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.other_site = Site.objects.create(
            hostname="other.example.gov.uk", port=443, root_page=self.site.root_page
        )
        self.url = reverse("govuk_page_usefulness_report")
        self.admin_user = get_user_model().objects.create_superuser(
            username="admin-user", email="admin@example.gov.uk", password="unused-password"
        )
        self.client.force_login(self.admin_user)

    def _export_rows(self, **params):
        response = self.client.get(self.url, {"export": "csv", **params})
        self.assertEqual(response.status_code, 200)
        return [line for line in b"".join(response.streaming_content).decode().splitlines() if line]

    def _vote_ten_days_and_one_day_ago(self):
        now = timezone.now()
        _vote(self.site, created_at=now - timedelta(days=10))
        _vote(self.site, created_at=now - timedelta(days=1))
        return (now - timedelta(days=3)).date().isoformat()

    def test_counts_are_grouped_by_path_and_pooled_across_sites(self):
        _vote(self.site, "yes", "/job-grades/")
        _vote(self.other_site, "yes", "/job-grades/")
        _vote(self.site, "no", "/job-grades/")
        _vote(self.site, "no", "/pay/")

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        rows = {row["path"]: row for row in response.context["object_list"]}
        self.assertEqual(rows["/job-grades/"]["yes_count"], 2)
        self.assertEqual(rows["/job-grades/"]["no_count"], 1)
        self.assertEqual(rows["/job-grades/"]["total"], 3)
        self.assertEqual(rows["/pay/"]["yes_count"], 0)
        self.assertEqual(rows["/pay/"]["no_count"], 1)

    def test_the_date_filter_narrows_the_counts(self):
        since = self._vote_ten_days_and_one_day_ago()

        response = self.client.get(self.url, {"created_at_from": since})

        rows = {row["path"]: row for row in response.context["object_list"]}
        self.assertEqual(rows["/job-grades/"]["total"], 1)

    def test_the_export_has_one_row_per_vote_with_page_answer_and_time(self):
        _vote(self.site, "yes", "/job-grades/")
        _vote(self.site, "no", "/job-grades/")
        _vote(self.site, "yes", "/pay/")

        rows = self._export_rows()

        # One header row plus one row per vote (not one aggregated row per page).
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0], "Page,Answer,Time")
        self.assertEqual(sum("/job-grades/" in row for row in rows[1:]), 2)
        self.assertEqual(sum("/pay/" in row for row in rows[1:]), 1)
        self.assertEqual(sum(",Yes," in row for row in rows[1:]), 2)
        self.assertEqual(sum(",No," in row for row in rows[1:]), 1)

    def test_the_date_filter_narrows_the_export(self):
        since = self._vote_ten_days_and_one_day_ago()

        # Header plus the single in-range vote.
        self.assertEqual(len(self._export_rows(created_at_from=since)), 2)

    def test_the_menu_item_is_at_the_top_of_the_reports_menu(self):
        items = [fn() for fn in hooks.get_hooks("register_reports_menu_item")]
        ours = next(item for item in items if item.name == "page-usefulness")
        self.assertEqual(ours.url, self.url)
        # Built-in reports start at order 700, so ours must sort before them.
        self.assertLess(ours.order, 700)

    def test_it_requires_admin_access(self):
        self.client.logout()
        self.assertNotEqual(self.client.get(self.url).status_code, 200)


class PrunePageUsefulnessVotesTests(TestCase):
    """The prune_page_usefulness_votes command keeps the votes table bounded."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)

    def _vote(self, days_ago):
        return _vote(self.site, created_at=timezone.now() - timedelta(days=days_ago))

    def test_old_votes_are_deleted_and_recent_ones_kept(self):
        old = self._vote(days_ago=400)
        recent = self._vote(days_ago=10)

        call_command("prune_page_usefulness_votes", "--days", "365", stdout=StringIO())

        self.assertFalse(PageUsefulnessVote.objects.filter(pk=old.pk).exists())
        self.assertTrue(PageUsefulnessVote.objects.filter(pk=recent.pk).exists())

    def test_days_zero_clears_everything(self):
        self._vote(days_ago=1)

        call_command("prune_page_usefulness_votes", "--days", "0", stdout=StringIO())

        self.assertEqual(PageUsefulnessVote.objects.count(), 0)
