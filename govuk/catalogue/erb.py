"""Just enough ERB to read a tech-docs repo's pages.

A fixed list of helpers is translated; there is no Ruby evaluation. Anything
else is dropped with a warning, so editors see what went missing.
"""

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

# ``<%% ... %>`` is ERB's escape for a literal tag; ``-%>`` and ``=%>`` trim.
_TAG = re.compile(r"<%(?P<kind>%|=|#|-)?(?P<code>.*?)[-=]?%>", re.S)
_BLOCK_OPENER = re.compile(r"(\bdo\b\s*(\|[^|]*\|)?\s*$)|^\s*(if|unless|case|while|until|for)\b")
_PARTIAL = re.compile(r"""^partial\s*\(?\s*(['"])(?P<name>[^'"]+)\1""")
_WARNING_TEXT = re.compile(r"""^warning_text\s*\(\s*(?P<arg>(['"]).*\2)\s*\)\s*$""", re.S)
_TITLE = re.compile(r"^current_page\.data\.title$")
_YEAR = re.compile(r"^(Time\.(current|now)|Date\.(today|current))\.year$")
_CONTENT_FOR = re.compile(r"^content_for\s*\(?\s*:(?P<name>\w+)")

# Stands in for the index page's withdrawn-guidance sitemap loop; the adapter fills it in.
WITHDRAWN_LIST_MARKER = "<!--guidance:withdrawn-list-->"

MAX_PARTIAL_DEPTH = 5


@dataclass
class ErbResult:
    text: str
    captures: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def warning_text_html(text: str) -> str:
    """The marker the renderer turns into GOV.UK warning text (``text`` is sanitised later)."""
    return f'<div data-guidance-component="warning">{text}</div>'


def _ruby_string(literal: str) -> str | None:
    """A quoted Ruby string literal, or None (Python's rules suffice for warning sentences)."""
    try:
        value = ast.literal_eval(literal)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


def translate(
    text: str,
    *,
    title: str,
    load_partial: Callable[[str], str | None],
    skip_partials: frozenset[str] = frozenset(),
    source_name: str = "",
    _depth: int = 0,
) -> ErbResult:
    """Translate the ERB in ``text`` into plain Markdown/HTML; ``skip_partials`` render as nothing."""
    result = ErbResult(text="")
    # Open blocks: a ``content_for`` frame keeps its output, an unknown block drops it.
    stack: list[dict] = []
    out: list[str] = []
    position = 0

    def emit(value: str) -> None:
        (stack[-1]["buffer"] if stack else out).append(value)

    def warn(message: str) -> None:
        result.warnings.append(f"{source_name}: {message}" if source_name else message)

    for match in _TAG.finditer(text):
        emit(text[position : match.start()])
        position = match.end()
        kind, code = match.group("kind") or "", match.group("code").strip()
        dropping = any(frame["drop"] for frame in stack)

        if kind == "%":
            emit(match.group(0).replace("<%%", "<%", 1))
            continue
        if kind == "#":
            continue

        if kind == "=":
            if dropping:
                continue
            emit(
                _output(
                    code,
                    title=title,
                    load_partial=load_partial,
                    skip_partials=skip_partials,
                    source_name=source_name,
                    depth=_depth,
                    result=result,
                    warn=warn,
                )
            )
            continue

        if code == "end":
            if not stack:
                warn("unmatched <% end %> ignored")
                continue
            frame = stack.pop()
            if frame["capture"]:
                result.captures[frame["capture"]] = "".join(frame["buffer"])
            elif frame["drop"] and not any(f["drop"] for f in stack):
                body = text[frame["start"] : match.start()]
                if "sitemap.resources" in frame["code"] and "withdrawn" in body:
                    emit(WITHDRAWN_LIST_MARKER)
                else:
                    warn(f"dropped an ERB block it cannot translate: <% {frame['code']} %>")
            continue
        if re.match(r"^(else|elsif|when)\b", code):
            continue
        if _BLOCK_OPENER.search(code):
            capture = _CONTENT_FOR.match(code)
            stack.append(
                {
                    "code": code,
                    "start": match.end(),
                    "capture": capture.group("name") if capture and not dropping else "",
                    "drop": not (capture and not dropping),
                    "buffer": [],
                }
            )
            continue
        if not dropping:
            warn(f"ignored ERB it cannot translate: <% {code} %>")

    emit(text[position:])
    if stack:
        warn("an ERB block was never closed; its content was dropped")
    result.text = "".join(out)
    return result


def _output(code, *, title, load_partial, skip_partials, source_name, depth, result, warn):
    """What one ``<%= ... %>`` tag renders as."""
    if _TITLE.match(code):
        return title
    if _YEAR.match(code):
        return str(date.today().year)

    partial = _PARTIAL.match(code)
    if partial:
        name = partial.group("name")
        if name in skip_partials:
            return ""
        if depth >= MAX_PARTIAL_DEPTH:
            warn(f"partial '{name}' nested too deeply; left out")
            return ""
        source = load_partial(name)
        if source is None:
            warn(f"partial '{name}' not found")
            return ""
        nested = translate(
            source,
            title=title,
            load_partial=load_partial,
            skip_partials=skip_partials,
            source_name=source_name,
            _depth=depth + 1,
        )
        result.warnings.extend(nested.warnings)
        return nested.text

    warning = _WARNING_TEXT.match(code)
    if warning:
        value = _ruby_string(warning.group("arg"))
        if value is not None:
            return warning_text_html(value)

    warn(f"left out ERB output it cannot translate: <%= {code} %>")
    return ""
