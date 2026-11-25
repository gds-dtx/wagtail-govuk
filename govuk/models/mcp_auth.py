"""OAuth for the MCP server: the site signs MCP clients in through its own SSO sign-in.

Codes and tokens are stored only as SHA-256 hashes.
"""

from django.conf import settings
from django.db import models


class MCPClient(models.Model):
    """A client that registered itself (RFC 7591), such as Claude Code or the MCP Inspector."""

    client_id = models.CharField(max_length=64, unique=True)
    # The SDK checks a confidential client's secret against this, so it is kept as issued.
    client_secret = models.CharField(max_length=128, blank=True)
    name = models.CharField(max_length=200, blank=True)
    information = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name or self.client_id


class MCPAuthorisationRequest(models.Model):
    """An /authorize request waiting for the reader to sign in and agree."""

    request_id = models.CharField(max_length=64, unique=True)
    client = models.ForeignKey(MCPClient, on_delete=models.CASCADE)
    redirect_uri = models.TextField()
    redirect_uri_provided_explicitly = models.BooleanField(default=True)
    code_challenge = models.CharField(max_length=128)
    state = models.TextField(blank=True)
    scopes = models.JSONField(default=list)
    resource = models.TextField()
    expires_at = models.DateTimeField(db_index=True)


class MCPAuthorisationCode(models.Model):
    code_hash = models.CharField(max_length=64, unique=True)
    client = models.ForeignKey(MCPClient, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    redirect_uri = models.TextField()
    redirect_uri_provided_explicitly = models.BooleanField(default=True)
    code_challenge = models.CharField(max_length=128)
    scopes = models.JSONField(default=list)
    resource = models.TextField()
    expires_at = models.DateTimeField(db_index=True)


class MCPAccessToken(models.Model):
    token_hash = models.CharField(max_length=64, unique=True)
    client = models.ForeignKey(MCPClient, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    scopes = models.JSONField(default=list)
    resource = models.TextField()
    expires_at = models.DateTimeField(db_index=True)
    revoked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class MCPConsent(models.Model):
    """A reader's agreement that a client may use the MCP server as them; revoked from Connected apps."""

    client = models.ForeignKey(MCPClient, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mcp_consents")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["client", "user"], name="mcp_consent_once")]
