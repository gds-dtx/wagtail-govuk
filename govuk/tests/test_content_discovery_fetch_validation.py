import socket
from unittest.mock import MagicMock, patch
from urllib.request import Request

from django.test import SimpleTestCase

from govuk.content_discovery import (
    ContentDiscoveryError,
    _ValidatedSourceRedirectHandler,
    fetch_source_content,
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


class ContentDiscoveryFetchValidationTests(SimpleTestCase):
    @patch("govuk.content_discovery._open_source_request")
    def test_fetch_rejects_localhost_hosts(self, mock_open_source_request):
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

        mock_open_source_request.assert_not_called()

    @patch("govuk.content_discovery._open_source_request")
    def test_fetch_rejects_ip_literal_hosts(self, mock_open_source_request):
        urls = [
            "https://127.0.0.1/feed.xml",
            "https://1.1.1.1/feed.xml",
        ]

        for url in urls:
            with self.subTest(url=url):
                with self.assertRaisesMessage(
                    ContentDiscoveryError,
                    "IP address hosts are not allowed",
                ):
                    fetch_source_content(url)

        mock_open_source_request.assert_not_called()

    @patch("govuk.content_discovery._open_source_request")
    def test_fetch_rejects_invalid_tld_hosts(self, mock_open_source_request):
        urls = [
            "https://127.1/feed.xml",
            "https://0177.0.0.1/feed.xml",
        ]

        for url in urls:
            with self.subTest(url=url):
                with self.assertRaisesMessage(
                    ContentDiscoveryError,
                    "non-alpha TLDs are not allowed",
                ):
                    fetch_source_content(url)

        mock_open_source_request.assert_not_called()

    @patch("govuk.content_discovery._open_source_request")
    def test_fetch_rejects_incomplete_hosts(self, mock_open_source_request):
        urls = [
            "https://2130706433/feed.xml",
            "https://0x7f000001/feed.xml",
            "https://[::1]/feed.xml",
        ]

        for url in urls:
            with self.subTest(url=url):
                with self.assertRaisesMessage(
                    ContentDiscoveryError,
                    "hostname appears invalid",
                ):
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
    def test_fetch_allows_named_hosts(
        self, mock_open_source_request, mock_getaddrinfo
    ):
        mock_getaddrinfo.return_value = _addrinfo_for("93.184.216.34")
        mock_response = MagicMock()
        mock_response.read.return_value = b"<feed/>"
        mock_open_source_request.return_value.__enter__.return_value = mock_response

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
