from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from govuk.content_discovery import USER_AGENT, fetch_source_content


class ContentDiscoveryRequestLoggingTests(SimpleTestCase):
    @override_settings(CONTENT_DISCOVERY_REQUEST_INFO_LOGGING=True)
    @patch("govuk.content_discovery.urlopen")
    def test_fetch_logs_sent_headers_when_info_logging_enabled(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b"<feed/>"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with patch("govuk.content_discovery.logger.info") as mock_info:
            fetch_source_content(
                "https://example.gov.uk/feed.xml",
                authorization_header="Bearer test-token",
            )

        self.assertTrue(mock_info.called)
        logged_headers = mock_info.call_args.args[4]
        normalised_headers = {
            key.lower(): value for key, value in logged_headers.items()
        }
        self.assertEqual(normalised_headers.get("authorization"), "Bearer test-token")
        self.assertEqual(normalised_headers.get("user-agent"), USER_AGENT)
        self.assertIn("accept", normalised_headers)

    @override_settings(CONTENT_DISCOVERY_REQUEST_INFO_LOGGING=False)
    @patch("govuk.content_discovery.urlopen")
    def test_fetch_does_not_log_headers_when_info_logging_disabled(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = b"<feed/>"
        mock_urlopen.return_value.__enter__.return_value = mock_response

        with patch("govuk.content_discovery.logger.info") as mock_info:
            fetch_source_content("https://example.gov.uk/feed.xml")

        mock_info.assert_not_called()
