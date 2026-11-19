import shutil
import socket
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.request import Request

import jwt
from cryptography.hazmat.primitives import serialization
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.content_discovery import (
    USER_AGENT,
    ContentDiscoveryError,
    FeedEntry,
    _ValidatedSourceRedirectHandler,
    fetch_source_content,
    parse_atom_feed,
    parse_github_org_repositories,
    parse_rss_feed,
    sync_content_discovery_source,
)
from govuk.models import (
    ContentDiscoverySettings,
    ContentDiscoverySource,
    EdDSAKeyPair,
    EdDSAKeySettings,
    ExternalContentItem,
    GovukTag,
)


def _addrinfo_for(ip_address: str, *, port: int = 443):
    if ":" in ip_address:
        family = socket.AF_INET6
        sockaddr = (ip_address, port, 0, 0)
    else:
        family = socket.AF_INET
        sockaddr = (ip_address, port)

    return [
        (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr),
    ]


def _entry(url: str, *, tags: list[str] | None = None) -> FeedEntry:
    return FeedEntry(
        format="atom",
        url=url,
        title="Synced item",
        summary="",
        created_at=None,
        updated_at=None,
        entry_id=url,
        author_names=[],
        published_raw="",
        updated_raw="",
        tags=tags or [],
    )


def _github_repositories(description: str, topics: str = "[]") -> bytes:
    return f"""
        [
          {{
            "html_url": "https://github.com/org/service",
            "name": "service",
            "description": "{description}",
            "topics": {topics}
          }}
        ]
    """.encode()


class ContentDiscoveryParsingTests(SimpleTestCase):
    def test_parsers_extract_tags(self):
        atom_feed = b"""
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>tag-entry</id>
                <title>Tagged entry</title>
                <link rel="alternate" href="https://example.gov.uk/tagged-entry" />
                <category scheme="https://technology.blog.gov.uk" term="Architecture" />
                <category term="Service Design" />
                <category scheme="https://example.gov.uk" />
              </entry>
            </feed>
        """
        cases = [
            ("atom category terms", parse_atom_feed, atom_feed, ["Architecture", "Service Design"]),
            (
                "github topics",
                parse_github_org_repositories,
                _github_repositories("Service repository", '["architecture", "service-design"]'),
                ["architecture", "service-design"],
            ),
        ]
        for label, parse, payload, expected in cases:
            with self.subTest(label):
                entries = parse(payload)

                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].tags, expected)

    def test_parsers_sanitise_nbsp_in_summaries(self):
        atom_feed = b"""
            <feed xmlns="http://www.w3.org/2005/Atom">
              <entry>
                <id>summary-entry</id>
                <title>Summary entry</title>
                <link rel="alternate" href="https://example.gov.uk/summary-entry" />
                <summary>First&amp;nbsp;Second</summary>
              </entry>
            </feed>
        """
        rss_feed = b"""
            <rss version="2.0">
              <channel>
                <item>
                  <guid>rss-summary-entry</guid>
                  <title>RSS summary entry</title>
                  <link>https://example.gov.uk/rss-summary-entry</link>
                  <description><![CDATA[First&nbsp;Second&amp;nbsp;Third]]></description>
                </item>
              </channel>
            </rss>
        """
        cases = [
            (
                "github description",
                parse_github_org_repositories,
                _github_repositories("First&amp;nbsp;Second"),
                "First Second",
            ),
            (
                "github doubly encoded description",
                parse_github_org_repositories,
                _github_repositories("First&amp;amp;nbsp;Second"),
                "First Second",
            ),
            ("atom summary", parse_atom_feed, atom_feed, "First Second"),
            ("rss description", parse_rss_feed, rss_feed, "First Second Third"),
        ]
        for label, parse, payload, expected in cases:
            with self.subTest(label):
                entries = parse(payload)

                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0].summary, expected)


class ContentDiscoveryFetchTests(SimpleTestCase):
    def _public_response(self, mock_open_source_request, mock_getaddrinfo):
        mock_getaddrinfo.return_value = _addrinfo_for("93.184.216.34")
        mock_response = MagicMock()
        mock_response.read.return_value = b"<feed/>"
        mock_open_source_request.return_value.__enter__.return_value = mock_response

    @patch("govuk.content_discovery._open_source_request")
    def test_fetch_rejects_disallowed_hosts(self, mock_open_source_request):
        cases = [
            (
                "localhost hosts are not allowed",
                [
                    "https://localhost/feed.xml",
                    "https://LOCALHOST/feed.xml",
                    "https://foo.localhost/feed.xml",
                    "https://localhost./feed.xml",
                ],
            ),
            (
                "IP address hosts are not allowed",
                ["https://127.0.0.1/feed.xml", "https://1.1.1.1/feed.xml"],
            ),
            (
                "non-alpha TLDs are not allowed",
                ["https://127.1/feed.xml", "https://0177.0.0.1/feed.xml"],
            ),
            (
                "hostname appears invalid",
                [
                    "https://2130706433/feed.xml",
                    "https://0x7f000001/feed.xml",
                    "https://[::1]/feed.xml",
                ],
            ),
        ]
        for message, urls in cases:
            for url in urls:
                with self.subTest(url=url):
                    with self.assertRaisesMessage(ContentDiscoveryError, message):
                        fetch_source_content(url)

        mock_open_source_request.assert_not_called()

    @patch("govuk.content_discovery.socket.getaddrinfo")
    @patch("govuk.content_discovery._open_source_request")
    def test_fetch_rejects_named_hosts_with_non_public_dns_answers(
        self, mock_open_source_request, mock_getaddrinfo
    ):
        mock_getaddrinfo.return_value = _addrinfo_for("169.254.169.254")

        with self.assertRaisesMessage(
            ContentDiscoveryError,
            "resolved to non-public IP addresses: 169.254.169.254",
        ):
            fetch_source_content("https://bad.example.com/feed.xml")

        mock_open_source_request.assert_not_called()

    @patch("govuk.content_discovery.socket.getaddrinfo")
    @patch("govuk.content_discovery._open_source_request")
    def test_fetch_allows_named_hosts(self, mock_open_source_request, mock_getaddrinfo):
        self._public_response(mock_open_source_request, mock_getaddrinfo)

        body = fetch_source_content("https://example.gov.uk/feed.xml")

        self.assertEqual(body, b"<feed/>")
        mock_open_source_request.assert_called_once()

    @patch("govuk.content_discovery.socket.getaddrinfo")
    def test_redirect_handler_rejects_named_hosts_with_non_public_dns_answers(
        self, mock_getaddrinfo
    ):
        mock_getaddrinfo.return_value = _addrinfo_for("169.254.169.254")
        handler = _ValidatedSourceRedirectHandler()
        request = Request("https://example.gov.uk/feed.xml")

        with self.assertRaisesMessage(
            ContentDiscoveryError,
            "resolved to non-public IP addresses: 169.254.169.254",
        ):
            handler.redirect_request(
                request,
                fp=None,
                code=302,
                msg="Found",
                headers={},
                newurl="https://bad.example.com/redirected-feed.xml",
            )

    @override_settings(CONTENT_DISCOVERY_REQUEST_INFO_LOGGING=True)
    @patch("govuk.content_discovery.socket.getaddrinfo")
    @patch("govuk.content_discovery._open_source_request")
    def test_fetch_logs_sent_headers_when_info_logging_enabled(
        self, mock_open_source_request, mock_getaddrinfo
    ):
        self._public_response(mock_open_source_request, mock_getaddrinfo)

        with patch("govuk.content_discovery.logger.info") as mock_info:
            fetch_source_content(
                "https://example.gov.uk/feed.xml",
                authorization_header="Bearer test-token",
            )

        self.assertTrue(mock_info.called)
        logged_headers = mock_info.call_args.args[4]
        normalised_headers = {key.lower(): value for key, value in logged_headers.items()}
        self.assertEqual(normalised_headers.get("authorization"), "Bearer test-token")
        self.assertEqual(normalised_headers.get("user-agent"), USER_AGENT)
        self.assertIn("accept", normalised_headers)

    @override_settings(CONTENT_DISCOVERY_REQUEST_INFO_LOGGING=False)
    @patch("govuk.content_discovery.socket.getaddrinfo")
    @patch("govuk.content_discovery._open_source_request")
    def test_fetch_does_not_log_headers_when_info_logging_disabled(
        self, mock_open_source_request, mock_getaddrinfo
    ):
        self._public_response(mock_open_source_request, mock_getaddrinfo)

        with patch("govuk.content_discovery.logger.info") as mock_info:
            fetch_source_content("https://example.gov.uk/feed.xml")

        mock_info.assert_not_called()


class ContentDiscoverySyncTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.discovery_settings = ContentDiscoverySettings.for_site(self.site)

    def _source(self, url="https://example.gov.uk/feed.xml", **fields) -> ContentDiscoverySource:
        return ContentDiscoverySource.objects.create(
            settings=self.discovery_settings, url=url, **fields
        )

    def _source_with_default_tag(self, *, consume_tags: bool) -> ContentDiscoverySource:
        source = self._source(consume_tags=consume_tags)
        default_tag = GovukTag.objects.create(slug="default-tag", name="Default Tag")
        source.default_tags = [{"type": "tag", "value": default_tag.pk}]
        source.save(update_fields=["default_tags"])
        return source

    def _sync(self, source, entries=()):
        """Sync the source against a feed of these entries; returns the fetch mock."""
        with patch("govuk.content_discovery.fetch_source_content") as mock_fetch:
            mock_fetch.return_value = b"<feed/>"
            with patch("govuk.content_discovery.parse_feed", return_value=list(entries)):
                sync_content_discovery_source(source)
        return mock_fetch

    @staticmethod
    def _tag_slugs(url: str) -> set[str]:
        return set(ExternalContentItem.objects.get(url=url).tags.values_list("slug", flat=True))

    # -- consumed tags ----------------------------------------------------------

    def test_sync_creates_and_applies_consumed_tags_with_default_tags(self):
        source = self._source_with_default_tag(consume_tags=True)
        entry_url = "https://example.gov.uk/content/one"

        self._sync(
            source,
            [_entry(entry_url, tags=[" Architecture ", "service design", "service design"])],
        )

        self.assertEqual(
            self._tag_slugs(entry_url), {"default-tag", "architecture", "service-design"}
        )
        self.assertEqual(GovukTag.objects.get(slug="architecture").name, "Architecture")
        self.assertEqual(GovukTag.objects.get(slug="service-design").name, "Service Design")

    def test_sync_does_not_apply_consumed_tags_when_flag_disabled(self):
        source = self._source_with_default_tag(consume_tags=False)
        entry_url = "https://example.gov.uk/content/two"

        self._sync(source, [_entry(entry_url, tags=["architecture"])])

        self.assertEqual(self._tag_slugs(entry_url), {"default-tag"})
        self.assertFalse(GovukTag.objects.filter(slug="architecture").exists())

    def test_sync_continues_when_tag_creation_fails(self):
        source = self._source_with_default_tag(consume_tags=True)
        entry_url = "https://example.gov.uk/content/three"
        original_create = GovukTag.objects.create

        def create_side_effect(*args, **kwargs):
            if kwargs.get("slug") == "broken-tag":
                raise RuntimeError("boom")
            return original_create(*args, **kwargs)

        with patch(
            "govuk.content_discovery.GovukTag.objects.create",
            side_effect=create_side_effect,
        ):
            self._sync(source, [_entry(entry_url, tags=["broken tag", "good tag"])])

        self.assertEqual(self._tag_slugs(entry_url), {"default-tag", "good-tag"})
        self.assertFalse(GovukTag.objects.filter(slug="broken-tag").exists())

    # -- signed bearer JWT --------------------------------------------------------

    @override_settings(WAGTAILADMIN_BASE_URL="https://admin.example.gov.uk")
    def test_sync_adds_signed_bearer_header_when_enabled_and_keys_exist(self):
        source = self._source(send_signed_bearer_jwt=True)
        key_pair = EdDSAKeyPair.generate_for_settings(
            settings_obj=EdDSAKeySettings.for_site(self.site)
        )

        mock_fetch = self._sync(source)

        self.assertTrue(mock_fetch.called)
        auth_header = mock_fetch.call_args.kwargs["authorization_header"]
        self.assertTrue(auth_header.startswith("Bearer "))

        token = auth_header.removeprefix("Bearer ").strip()
        public_key = serialization.load_pem_public_key(key_pair.public_key.encode("utf-8"))
        payload = jwt.decode(
            token,
            key=public_key,
            algorithms=[key_pair.algorithm],
            audience=source.url,
            issuer="https://admin.example.gov.uk",
            options={"require": ["iss", "iat", "nbf", "exp", "jti"]},
        )
        self.assertEqual(payload["htu"], source.url)
        self.assertEqual(payload["htm"], "GET")
        self.assertLessEqual(payload["exp"] - payload["iat"], 91)
        self.assertGreaterEqual(payload["exp"] - payload["iat"], 89)

    @override_settings(WAGTAILADMIN_BASE_URL="https://admin.example.gov.uk")
    def test_sync_sends_no_header_without_both_the_flag_and_a_key(self):
        for label, send_signed_bearer_jwt, with_key in (
            ("enabled but no keys exist", True, False),
            ("flag disabled", False, True),
        ):
            with self.subTest(label):
                EdDSAKeyPair.objects.all().delete()
                source = self._source(
                    url=f"https://example.gov.uk/{send_signed_bearer_jwt}.xml",
                    send_signed_bearer_jwt=send_signed_bearer_jwt,
                )
                if with_key:
                    EdDSAKeyPair.generate_for_settings(
                        settings_obj=EdDSAKeySettings.for_site(self.site)
                    )

                mock_fetch = self._sync(source)

                self.assertTrue(mock_fetch.called)
                self.assertIsNone(mock_fetch.call_args.kwargs["authorization_header"])

    # -- sync_source --------------------------------------------------------------

    def test_sync_source_hides_items_missing_from_feed_when_enabled(self):
        source = self._source(sync_source=True)
        kept_item = ExternalContentItem.objects.create(
            source=source,
            url="https://example.gov.uk/articles/kept",
            title="Kept item",
            hidden=False,
        )
        missing_item = ExternalContentItem.objects.create(
            source=source,
            url="https://example.gov.uk/articles/missing",
            title="Missing item",
            hidden=False,
        )
        other_source = self._source(url="https://example.gov.uk/other-feed.xml", sync_source=True)
        other_item = ExternalContentItem.objects.create(
            source=other_source,
            url="https://example.gov.uk/articles/other",
            title="Other source item",
            hidden=False,
        )

        self._sync(source, [_entry(kept_item.url)])

        kept_item.refresh_from_db()
        missing_item.refresh_from_db()
        other_item.refresh_from_db()
        self.assertFalse(kept_item.hidden)
        self.assertTrue(missing_item.hidden)
        self.assertFalse(other_item.hidden)

    def test_sync_source_does_not_hide_missing_items_when_disabled(self):
        source = self._source(sync_source=False)
        missing_item = ExternalContentItem.objects.create(
            source=source,
            url="https://example.gov.uk/articles/missing",
            title="Missing item",
            hidden=False,
        )

        self._sync(source)

        missing_item.refresh_from_db()
        self.assertFalse(missing_item.hidden)


class ContentDiscoveryImportViewTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        ContentDiscoverySettings.for_site(self.site)
        self.client.force_login(
            get_user_model().objects.create_superuser(
                username="admin-user",
                email="admin@example.gov.uk",
                password="unused-password",
            )
        )
        self.url = reverse("govuk_content_discovery_import_site", args=[self.site.pk])

    @staticmethod
    def _upload(content: str) -> SimpleUploadedFile:
        return SimpleUploadedFile("sources.csv", content.encode("utf-8"), content_type="text/csv")

    def test_imports_csv_for_current_site(self):
        upload = self._upload(
            "url,name,disable_tls_verification,send_signed_bearer_jwt\n"
            "https://example.com/feed.xml,Example feed,false,true\n"
        )

        response = self.client.post(self.url, data={"csv_file": upload, "next": "/admin/"})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/admin/")
        source = ContentDiscoverySource.objects.get(
            settings=ContentDiscoverySettings.for_site(self.site),
            url="https://example.com/feed.xml",
        )
        self.assertEqual(source.name, "Example feed")
        self.assertFalse(source.disable_tls_verification)
        self.assertTrue(source.send_signed_bearer_jwt)

    def test_rejects_rows_targeting_other_sites(self):
        other_site = Site.objects.create(
            hostname="other.example.gov.uk",
            port=8080,
            root_page=self.site.root_page,
            is_default_site=False,
        )
        upload = self._upload(f"site_id,url\n{other_site.pk},https://example.com/feed.xml\n")

        response = self.client.post(self.url, data={"csv_file": upload}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ContentDiscoverySource.objects.count(), 0)
        self.assertContains(response, "is not allowed for this import")


class ImportContentDiscoverySourcesCommandTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)

    def _write_csv(self, *lines: str) -> str:
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        csv_path = Path(temp_dir) / "content-discovery-sources.csv"
        csv_path.write_text("\n".join(lines), encoding="utf-8")
        return str(csv_path)

    def _call(self, csv_path: str, *, site_id=True) -> str:
        stdout = StringIO()
        args = ["--site-id", str(self.site.pk)] if site_id else []
        call_command("import_content_discovery_sources", csv_path, *args, stdout=stdout)
        return stdout.getvalue()

    def test_imports_new_sources_with_site_fallback_and_tags(self):
        policy_tag = GovukTag.objects.create(slug="policy", name="Policy")
        news_tag = GovukTag.objects.create(slug="news", name="News")
        csv_path = self._write_csv(
            "url,name,disable_tls_verification,send_signed_bearer_jwt,default_tags",
            "https://example.com/feed.xml,Example feed,false,true,policy|news",
            "https://example.org/rss.xml,,true,false,",
        )

        output = self._call(csv_path)

        settings = ContentDiscoverySettings.for_site(self.site)
        sources = list(
            ContentDiscoverySource.objects.filter(settings=settings).order_by("sort_order")
        )
        self.assertEqual(len(sources), 2)

        self.assertEqual(sources[0].name, "Example feed")
        self.assertFalse(sources[0].disable_tls_verification)
        self.assertTrue(sources[0].send_signed_bearer_jwt)
        self.assertEqual(sources[0].get_default_tag_ids(), [policy_tag.pk, news_tag.pk])

        self.assertEqual(sources[1].name, "")
        self.assertTrue(sources[1].disable_tls_verification)
        self.assertFalse(sources[1].send_signed_bearer_jwt)
        self.assertEqual(sources[1].get_default_tag_ids(), [])

        self.assertIn("created 2", output)

    def test_updates_existing_source_matched_by_site_and_url(self):
        old_tag = GovukTag.objects.create(slug="old", name="Old")
        new_tag = GovukTag.objects.create(slug="new", name="New")
        settings = ContentDiscoverySettings.for_site(self.site)
        source = ContentDiscoverySource.objects.create(
            settings=settings,
            sort_order=0,
            name="Old name",
            url="https://example.com/feed.xml",
            disable_tls_verification=False,
            default_tags=[{"type": "tag", "value": old_tag.pk}],
        )
        csv_path = self._write_csv(
            "url,name,disable_tls_verification,send_signed_bearer_jwt,default_tags",
            "https://example.com/feed.xml,Updated name,true,true,new",
        )

        output = self._call(csv_path)

        source.refresh_from_db()
        self.assertEqual(source.name, "Updated name")
        self.assertTrue(source.disable_tls_verification)
        self.assertTrue(source.send_signed_bearer_jwt)
        self.assertEqual(source.get_default_tag_ids(), [new_tag.pk])
        self.assertEqual(
            ContentDiscoverySource.objects.filter(
                settings=settings, url="https://example.com/feed.xml"
            ).count(),
            1,
        )
        self.assertIn("updated 1", output)

    def test_requires_site_id_when_csv_does_not_define_it(self):
        csv_path = self._write_csv("url,name", "https://example.com/feed.xml,Name", "")

        with self.assertRaisesMessage(
            CommandError,
            "Provide a 'site_id' column or pass --site-id for all rows.",
        ):
            self._call(csv_path, site_id=False)

    def test_rolls_back_when_unknown_tag_key_is_present(self):
        GovukTag.objects.create(slug="known", name="Known")
        csv_path = self._write_csv(
            "url,default_tags",
            "https://example.com/feed.xml,known",
            "https://example.org/feed.xml,missing-tag",
        )

        with self.assertRaisesMessage(CommandError, "unknown tag key(s): missing-tag"):
            self._call(csv_path)

        self.assertEqual(
            ContentDiscoverySource.objects.filter(
                settings=ContentDiscoverySettings.for_site(self.site)
            ).count(),
            0,
        )
