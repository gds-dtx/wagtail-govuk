"""The content index: what the site publishes, as Markdown an AI tool can read.

``extract`` reads each page, role and skill into a document; ``chunking``
splits it into sections at its headings; ``indexer`` stores them as
``IndexedDocument`` and ``IndexedChunk`` rows and keeps them current, called
from the signal handlers in ``signals`` as editors publish. ``embeddings``
adds a vector of what each section means, and ``search`` finds them by their
words and their meaning. The MCP server (``govuk.mcp``) and Ask
(``govuk.ask``) read it.

Kept while either reader is on: ``FEATURE_FLAGS["MCP"]`` or ``["ASK"]``.
"""

from django.conf import settings


def mcp_enabled() -> bool:
    return bool(settings.FEATURE_FLAGS.get("MCP"))


def ask_enabled() -> bool:
    return bool(settings.FEATURE_FLAGS.get("ASK"))


def content_index_enabled() -> bool:
    """Whether the index is kept: something reads it."""
    return mcp_enabled() or ask_enabled()


# What a refusal names when the index is off.
FLAGS_OFF = "The content index is off (FEATURE_MCP and FEATURE_ASK)."
