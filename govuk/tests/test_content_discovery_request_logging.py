import socket

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from govuk.content_discovery import USER_AGENT, fetch_source_content


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


class ContentDiscoveryRequestLoggingTests(SimpleTestCase):
    @override_settings(CONTENT_DISCOVERY_REQUEST_INFO_LOGGING=True)
    @patch("govuk.content_discovery.socket.getaddrinfo")
    @patch("govuk.content_discovery._open_source_request")
    def test_fetch_logs_sent_headers_when_info_logging_enabled(
        self, mock_open_source_request, mock_getaddrinfo
    ):
        mock_getaddrinfo.return_value = _addrinfo_for("93.184.216.34")
        mock_response = MagicMock()
        mock_response.read.return_value = b"<feed/>"
        mock_open_source_request.return_value.__enter__.return_value = mock_response

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
    @patch("govuk.content_discovery.socket.getaddrinfo")
    @patch("govuk.content_discovery._open_source_request")
    def test_fetch_does_not_log_headers_when_info_logging_disabled(
        self, mock_open_source_request, mock_getaddrinfo
    ):
        mock_getaddrinfo.return_value = _addrinfo_for("93.184.216.34")
        mock_response = MagicMock()
        mock_response.read.return_value = b"<feed/>"
        mock_open_source_request.return_value.__enter__.return_value = mock_response

        with patch("govuk.content_discovery.logger.info") as mock_info:
            fetch_source_content("https://example.gov.uk/feed.xml")

        mock_info.assert_not_called()
