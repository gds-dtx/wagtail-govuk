"""Structured Claude calls for AI search (``settings.AI_CLAUDE``: bedrock, fake or unset).

Medium effort, Claude Haiku 5.5's own default, set so it stays put; no
retries because a reader is waiting. Never log or raise with the question or answer.
"""

import json
import logging
import math
from dataclasses import dataclass
from functools import lru_cache

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

EFFORT = "medium"


class ClaudeError(Exception):
    """Claude failed or its reply was unusable. The message never holds the question."""

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


def estimate(system: str, prompt: str, max_tokens: int) -> Usage:
    """A pessimistic guess at a call's cost: about 3 characters a token in, and all of ``max_tokens`` out."""
    return Usage(input_tokens=math.ceil((len(system) + len(prompt)) / 3), output_tokens=max_tokens)


@dataclass
class Reply:
    data: dict
    usage: Usage


class BedrockClaude:
    """Claude on Amazon Bedrock's bedrock-runtime endpoint (``AnthropicBedrock``)."""

    def __init__(self, model: str, region: str):
        self.model = model
        self.region = region
        self._client = None

    def _sdk(self):
        if self._client is None:
            from anthropic import AnthropicBedrock

            self._client = AnthropicBedrock(aws_region=self.region, max_retries=0)
        return self._client

    def complete(
        self, *, purpose: str, system: str, prompt: str, schema: dict, max_tokens: int, timeout: float, context=None
    ) -> Reply:
        import anthropic

        # Bedrock can bill a call that times out or drops, so those are charged the estimate.
        spent = estimate(system, prompt, max_tokens)
        try:
            response = self._sdk().with_options(timeout=timeout).messages.create(
                model=self.model,
                max_tokens=max_tokens,
                # Cached where long enough: the instructions never change.
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APITimeoutError as exc:
            raise ClaudeError("Claude took too long.", usage=spent) from exc
        except anthropic.RateLimitError as exc:
            raise ClaudeError("Claude is busy (429).") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code in (403, 404):
                # Safe to log: these hold nothing of the request (a 400's could).
                logger.warning(
                    "Bedrock answered %s for model %s: %s", exc.status_code, self.model, str(exc.message)[:300]
                )
            # A 4xx was turned away before any work; a 5xx may have done some.
            raise ClaudeError(
                f"Claude refused the request ({exc.status_code}).", usage=spent if exc.status_code >= 500 else None
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise ClaudeError("Claude could not be reached.", usage=spent) from exc
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
    """Answers from ``context`` without a model, for tests and local runs."""

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
def _bedrock(model: str, region: str) -> BedrockClaude:
    return BedrockClaude(model, region)


_fake = FakeClaude()


def get_claude():
    """The configured Claude, or None when AI search uses no model."""
    backend = settings.AI_CLAUDE
    if not backend or backend == "none":
        return None
    if backend == "bedrock":
        return _bedrock(settings.AI_CLAUDE_MODEL, settings.AI_CLAUDE_REGION)
    if backend == "fake":
        return _fake
    raise ImproperlyConfigured(f"AI_CLAUDE {backend!r} is not one of bedrock, fake or none.")
