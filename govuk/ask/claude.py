"""Claude, for Ask: one structured call at a time, and what it used.

Every call asks for JSON to a schema (structured outputs, ``output_config``)
and gets back the parsed object; the callers (``rerank``, ``summary``) check
what is in it. Claude Opus 5.5 at low effort: thinking cannot be turned off on
it, and low keeps it quick. Each call has its own timeout and no retries: a
reader is waiting, and Ask works without Claude.

``settings.ASK_CLAUDE``:

- "bedrock": Claude on Amazon Bedrock's ``bedrock-runtime`` endpoint
  (InvokeModel), through an inference profile such as
  ``eu.anthropic.claude-opus-5-5``, which keeps processing in EU regions. The
  SDK's ``AnthropicBedrock`` client, signed with the task's AWS credentials.
- "bedrock-mantle": Claude in Amazon Bedrock's ``bedrock-mantle`` endpoint
  (``AnthropicBedrockMantle``), where an account and region offer the model
  there. Its model IDs have no profile prefix (``anthropic.claude-opus-5-5``).
- "fake": a stand-in that answers from what it was given, for tests.
- unset: no model.

Both Bedrock endpoints take the same Messages API request.

Nothing here logs or raises with the question or the answer.
"""

import json
import logging
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

EFFORT = "low"


class ClaudeError(Exception):
    """Claude could not be reached, or its reply could not be used. The
    message names the reason, never the question."""

    def __init__(self, message: str, *, usage: "Usage | None" = None):
        super().__init__(message)
        self.usage = usage


class ClaudeRefused(ClaudeError):
    """Claude declined (``stop_reason`` "refusal")."""


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @classmethod
    def from_response(cls, usage) -> "Usage":
        if usage is None:
            return cls()
        return cls(
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass
class Reply:
    data: dict
    usage: Usage


RUNTIME, MANTLE = "bedrock-runtime", "bedrock-mantle"


class BedrockClaude:
    """Claude on Amazon Bedrock, through one of the SDK's two Bedrock clients."""

    def __init__(self, model: str, region: str, endpoint: str = RUNTIME):
        if endpoint not in (RUNTIME, MANTLE):
            raise ImproperlyConfigured(f"Unknown Bedrock endpoint {endpoint!r}.")
        self.model = model
        self.region = region
        self.endpoint = endpoint
        self._client = None

    def _sdk(self):
        if self._client is None:
            from anthropic import AnthropicBedrock, AnthropicBedrockMantle

            client_class = AnthropicBedrock if self.endpoint == RUNTIME else AnthropicBedrockMantle
            # Retries are per call (below): none, since a reader is waiting.
            self._client = client_class(aws_region=self.region, max_retries=0)
        return self._client

    def complete(
        self, *, purpose: str, system: str, prompt: str, schema: dict, max_tokens: int, timeout: float, context=None
    ) -> Reply:
        import anthropic

        try:
            response = self._sdk().with_options(timeout=timeout).messages.create(
                model=self.model,
                max_tokens=max_tokens,
                # The instructions are the same every time: cached, where they
                # are long enough to be.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APITimeoutError as exc:
            raise ClaudeError("Claude took too long.") from exc
        except anthropic.RateLimitError as exc:
            raise ClaudeError("Claude is busy (429).") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code in (403, 404):
                # Access and model ID problems: Bedrock's own message says
                # which, and holds nothing of the request. (A 400's could.)
                logger.warning(
                    "Bedrock answered %s for model %s: %s", exc.status_code, self.model, str(exc.message)[:300]
                )
            raise ClaudeError(f"Claude refused the request ({exc.status_code}).") from exc
        except anthropic.APIConnectionError as exc:
            raise ClaudeError("Claude could not be reached.") from exc
        except anthropic.AnthropicError as exc:
            # Credentials and configuration, mostly.
            raise ClaudeError(f"Claude could not be called ({type(exc).__name__}).") from exc
        usage = Usage.from_response(getattr(response, "usage", None))
        if response.stop_reason == "refusal":
            category = getattr(getattr(response, "stop_details", None), "category", None)
            raise ClaudeRefused(f"Claude declined ({category or 'no category'}).", usage=usage)
        if response.stop_reason == "max_tokens":
            raise ClaudeError("Claude's reply was cut short.", usage=usage)
        text = next((block.text for block in response.content if block.type == "text"), "")
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ClaudeError("Claude's reply was not JSON.", usage=usage) from exc
        if not isinstance(data, dict):
            raise ClaudeError("Claude's reply was not an object.", usage=usage)
        return Reply(data, usage)


class FakeClaude:
    """Answers without a model, from what it was given (``context``).

    Re-ranking keeps search's order and the first six; a summary names the
    first two matches. Enough to see the page work, and for tests, which can
    give it a ``responder`` of their own, or tell it to ``fail`` or ``refuse``.
    """

    model = "fake"

    def __init__(self, responder=None, *, fail: bool = False, refuse: bool = False):
        self.responder = responder
        self.fail = fail
        self.refuse = refuse
        self.calls: list[str] = []

    def complete(
        self, *, purpose: str, system: str, prompt: str, schema: dict, max_tokens: int, timeout: float, context=None
    ) -> Reply:
        self.calls.append(purpose)
        usage = Usage(input_tokens=len(system + prompt) // 4, output_tokens=50)
        if self.fail:
            raise ClaudeError("The fake model was told to fail.")
        if self.refuse:
            raise ClaudeRefused("Claude declined (fake).", usage=usage)
        if self.responder is not None:
            return Reply(self.responder(purpose, context), usage)
        keys = list(context or [])
        if purpose == "rerank":
            return Reply({"in_scope": bool(keys), "relevant": [{"id": key, "why": "It matched."} for key in keys[:6]]}, usage)
        cited = " and ".join(f"[{key}]" for key in keys[:2])
        return Reply({"summary": f"Start with {cited}." if keys else "Nothing here answers that.", "cited": keys[:2]}, usage)


@lru_cache(maxsize=4)
def _bedrock(model: str, region: str, endpoint: str) -> BedrockClaude:
    return BedrockClaude(model, region, endpoint)


_fake = FakeClaude()


def get_claude():
    """The configured Claude, or None when Ask uses no model."""
    backend = settings.ASK_CLAUDE
    if not backend or backend == "none":
        return None
    if backend in ("bedrock", RUNTIME):
        return _bedrock(settings.ASK_CLAUDE_MODEL, settings.ASK_CLAUDE_REGION, RUNTIME)
    if backend == MANTLE:
        return _bedrock(settings.ASK_CLAUDE_MODEL, settings.ASK_CLAUDE_REGION, MANTLE)
    if backend == "fake":
        return _fake
    raise ImproperlyConfigured(f"ASK_CLAUDE {backend!r} is not one of bedrock, bedrock-mantle, fake or none.")
