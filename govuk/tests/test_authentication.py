from unittest.mock import patch, sentinel

from django.test import RequestFactory, SimpleTestCase
from rest_framework.request import Request

from govuk.api import WagtailDocuments, WagtailImages, WagtailPages
from govuk.authentication import InternalAccessJWTAuthentication


class InternalAccessJWTAuthenticationTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.authentication = InternalAccessJWTAuthentication()

    def test_authenticate_delegates_authorization_header_to_simplejwt(self):
        request = Request(
            self.factory.get("/", HTTP_AUTHORIZATION="Bearer header-access-token")
        )

        with patch(
            "govuk.authentication.JWTStatelessUserAuthentication.authenticate",
            return_value=sentinel.authenticated,
        ) as mock_authenticate:
            authenticated = self.authentication.authenticate(request)

        self.assertIs(authenticated, sentinel.authenticated)
        mock_authenticate.assert_called_once_with(request)

    def test_authenticate_does_not_fall_back_to_query_string(self):
        request = Request(self.factory.get("/", {"bearer": "query-access-token"}))

        with (
            patch(
                "govuk.authentication.JWTStatelessUserAuthentication.authenticate",
                return_value=None,
            ) as mock_authenticate,
            patch.object(
                self.authentication, "get_validated_token"
            ) as mock_get_validated_token,
            patch.object(self.authentication, "get_user") as mock_get_user,
        ):
            authenticated = self.authentication.authenticate(request)

        self.assertIsNone(authenticated)
        mock_authenticate.assert_called_once_with(request)
        mock_get_validated_token.assert_not_called()
        mock_get_user.assert_not_called()

    def test_api_viewsets_do_not_whitelist_bearer_query_parameter(self):
        for viewset in (WagtailPages, WagtailImages, WagtailDocuments):
            self.assertNotIn("bearer", viewset.known_query_parameters)
