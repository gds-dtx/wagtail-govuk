"""Helpers for building content-style pages in tests."""

from __future__ import annotations


def text_body(html: str) -> list[dict]:
    """A page body holding one Text block of ``html``.

    The body of a content page is a stream of blocks. A test that used to pass
    its HTML straight in passes it through here, which is the shape migration
    0087 gave every page's rich text body.
    """
    return [{"type": "text", "value": html}]


def body_html(page) -> str:
    """The stored HTML of every Text block in a page's body, run together.

    For tests that ask whether some wording reached a page, which used to read
    the rich text body field directly.
    """
    return "".join(
        block.value.source for block in page.body if block.block_type == "text"
    )
