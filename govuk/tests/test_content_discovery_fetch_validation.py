from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from govuk.content_discovery import ContentDiscoveryError, fetch_source_content


class ContentDiscoveryFetchValidationTests(SimpleTestCase):
    @patch("govuk.content_discovery.urlopen")
    def test_fetch_rejects_localhost_hosts(self, mock_urlopen):
        urls = [
            "https://localhost/feed.xml",
            "https://LOCALHOST/feed.xml",
            "https://foo.localhost/feed.xml",
            "https://localhost./feed.xml",
        ]

        for url in urls:
            with self.subTest(url=url):
                with self.assertRaisesMessage(
                    ContentDiscoveryError,
                    "localhost hosts are not allowed",
                ):
                    fetch_source_content(url)

        mock_urlopen.assert_not_called()

    @patch("govuk.content_discovery.urlopen")
    def test_fetch_rejects_ip_literal_hosts(self, mock_urlopen):
        urls = [
            "https://127.0.0.1/feed.xml",
            "https://127.1/feed.xml",
            "https://2130706433/feed.xml",
            "https://0x7f000001/feed.xml",
            "https://0177.0.0.1/feed.xml",
            "https://[::1]/feed.xml",
        ]

        for url in urls:
            with self.subTest(url=url):
                with self.assertRaisesMessage(
                    ContentDiscoveryError,
                    "IP address hosts are not allowed",
                ):
                    fetch_source_content(url)

        mock_urlopen.assert_not_called()

    @patch("govuk.content_discovery.urlopen")
    def test_fetch_allows_named_hosts(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b"<feed/>"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        body = fetch_source_content("https://example.gov.uk/feed.xml")

        self.assertEqual(body, b"<feed/>")
        mock_urlopen.assert_called_once()
