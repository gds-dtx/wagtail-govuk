"""The "Is this page useful?" prompt at the foot of every page -- CS32-3543.

The ticket asks for three things: that it appears and behaves as it does on
the live service, that it is accessible, and that answers are recorded
server-side. The first two are the template and the form; the third is the
view, which writes one log line per answer and stores nothing else.
"""

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.models import ContentPage, CustomiseSettings


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


def _switch_on(site, **overrides) -> CustomiseSettings:
    customise = CustomiseSettings.for_site(site)
    customise.show_page_feedback_prompt = True
    for name, value in overrides.items():
        setattr(customise, name, value)
    customise.save()
    return customise


@override_settings(FEATURE_FLAGS=_feature_flags())
class PageFeedbackPromptTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        page = self.site.root_page.add_child(
            instance=ContentPage(
                title="Civil Service job grades",
                slug="job-grades",
                body="<p>The grades.</p>",
            )
        )
        page.save_revision().publish()
        self.url = "/job-grades/"

    def test_the_prompt_is_off_until_a_site_switches_it_on(self):
        """Every site on the platform shares this template; only the framework
        asked for the prompt."""
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Is this page useful?")
        self.assertNotContains(response, 'id="page-feedback"')

    def test_the_prompt_is_a_form_with_a_yes_and_a_no(self):
        """Real submit buttons in a real form, so it works before any script
        runs and a screen reader hears two buttons, not two links."""
        _switch_on(self.site)

        response = self.client.get(self.url)

        self.assertContains(response, "Is this page useful?")
        self.assertContains(response, f'action="{reverse("page_feedback")}"')
        self.assertContains(response, 'name="answer"', count=2)
        self.assertContains(response, 'value="yes"')
        self.assertContains(response, 'value="no"')
        self.assertContains(response, 'name="page" value="/job-grades/"')
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_the_thank_you_shows_after_a_post_without_javascript(self):
        """The view redirects back with ?page_feedback=sent; the template then
        hides the question and shows the thank-you, as the script would have."""
        _switch_on(self.site)

        response = self.client.get(self.url + "?page_feedback=sent")
        html = response.content.decode("utf-8")

        form_start = html.index("js-prompt-questions")
        form_tag = html[html.rfind("<form", 0, form_start) : html.index(">", form_start)]
        self.assertIn("hidden", form_tag)
        success_start = html.index("js-prompt-success")
        success_tag = html[html.rfind("<div", 0, success_start) : html.index(">", success_start)]
        self.assertNotIn("hidden", success_tag)
        self.assertContains(response, "Thank you for your feedback")
        self.assertContains(response, 'href="/feedback"')

    def test_the_thank_you_is_hidden_until_someone_answers(self):
        _switch_on(self.site)

        html = self.client.get(self.url).content.decode("utf-8")

        success_start = html.index("js-prompt-success")
        success_tag = html[html.rfind("<div", 0, success_start) : html.index(">", success_start)]
        self.assertIn("hidden", success_tag)

    def test_the_follow_up_wording_and_link_come_from_the_site_settings(self):
        _switch_on(
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
        _switch_on(self.site)

        response = self.client.get("/no-such-page/")

        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, "Is this page useful?", status_code=404)


@override_settings(FEATURE_FLAGS=_feature_flags())
class PageFeedbackViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        _switch_on(self.site)
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
        """This log line is the server-side record the ticket asks for."""
        with self.assertLogs("govuk.page_feedback", level="INFO") as logs:
            response = self.client.post(
                self.url, {"answer": "Yes", "page": "/job-grades/"}
            )

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], "/job-grades/?page_feedback=sent#page-feedback")
        self.assertEqual(len(logs.records), 1)
        record = logs.records[0]
        self.assertEqual(record.getMessage(), "Page feedback")
        self.assertEqual(record.page_feedback_answer, "yes")
        self.assertEqual(record.page_feedback_path, "/job-grades/")
        self.assertEqual(record.site_hostname, self.site.hostname)

    def test_a_background_post_gets_no_content_and_no_redirect(self):
        response = self.client.post(
            self.url,
            {"answer": "no", "page": "/job-grades/"},
            HTTP_X_REQUESTED_WITH="fetch",
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
        response = self.client.post(
            self.url, {"answer": "yes", "page": "/job-grades/?x=1#frag"}
        )

        self.assertEqual(response["Location"], "/job-grades/?page_feedback=sent#page-feedback")

    def test_the_form_is_protected_against_cross_site_posts(self):
        client = Client(enforce_csrf_checks=True)

        response = client.post(self.url, {"answer": "yes", "page": "/"})

        self.assertEqual(response.status_code, 403)
