"""The content index: what the site publishes, as Markdown an AI tool can read.

``extract`` reads each page, role and skill into a document; ``chunking``
splits it into sections at its headings; ``indexer`` stores them as
``IndexedDocument`` and ``IndexedChunk`` rows and keeps them current, called
from the signal handlers in ``signals`` as editors publish. The rows are what
the MCP server reads.

Gated by ``FEATURE_FLAGS["MCP"]``.
"""

from django.conf import settings


def content_index_enabled() -> bool:
    return bool(settings.FEATURE_FLAGS.get("MCP"))
