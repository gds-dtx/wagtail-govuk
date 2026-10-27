"""What a Raw HTML block may put on a page.

A Raw HTML block is the one place an editor writes HTML, and the site's content
security policy already stops most of what could go wrong with it: no inline
scripts or handlers, nothing framed, no stylesheets, scripts or images from
other sites. This allowlist is the second layer, and it takes out what the
policy does not cover:

- ``<meta http-equiv="refresh">``, which the policy does not stop, and which
  would send a reader from this site to any other;
- ``<script src>`` from this site's own origin, which the policy allows, and
  with it the admin's scripts on a public page;
- ``<link>``: ``dns-prefetch`` and ``preconnect`` to another host are not
  fully covered by the policy, and this site's own stylesheets -- the admin's
  among them -- could restyle a page. Extra CSS is the supported way to style;
- markup that does not close, which the parser balances, so a stray ``</main>``
  or an unclosed ``<div>`` cannot take the rest of the page with it.

Everything else the policy would block anyway is dropped too: ``<style>``,
``style`` attributes, iframes and forms.

``class`` is kept, so classes defined in Settings -> Customise -> Extra CSS can
be used, and ``data-*`` is kept for GOV.UK Frontend components (``data-module``).
"""

import re

import nh3

TAGS = {
    # Text and structure.
    "a", "abbr", "address", "article", "aside", "b", "bdi", "bdo", "blockquote",
    "br", "cite", "code", "data", "dd", "del", "details", "dfn", "div", "dl",
    "dt", "em", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5",
    "h6",
    "header", "hr", "i", "ins", "kbd", "li", "mark", "nav", "ol", "p", "pre",
    "q", "s", "samp", "section", "small", "span", "strong", "sub", "summary",
    "sup", "time", "u", "ul", "var", "wbr",
    # Tables.
    "caption", "col", "colgroup", "table", "tbody", "td", "tfoot", "th",
    "thead", "tr",
    # Images, which the policy keeps to this site's own.
    "img", "picture", "source",
    # The markup GOV.UK Frontend components are written in.
    "button",
}

# Drawings: icons such as the start button's arrow, and diagrams with
# gradients, clipping, text, markers and filters. Left out because they reach
# outside the drawing or run code: <script>, <style>, <foreignObject>, <image>
# and <feImage>, and the animation elements, whose attributeName can rewrite a
# link into javascript:.
SVG_TAGS = {
    # Structure.
    "svg", "g", "defs", "symbol", "use", "title", "desc",
    # Shapes.
    "path", "circle", "ellipse", "line", "polygon", "polyline", "rect",
    # Text.
    "text", "tspan", "textPath",
    # Paint, clipping and masking.
    "linearGradient", "radialGradient", "stop", "pattern", "clipPath", "mask",
    "marker",
    # Filters.
    "filter", "feBlend", "feColorMatrix", "feComponentTransfer", "feComposite",
    "feConvolveMatrix", "feDisplacementMap", "feDropShadow", "feFlood",
    "feFuncA", "feFuncB", "feFuncG", "feFuncR", "feGaussianBlur", "feMerge",
    "feMergeNode", "feMorphology", "feOffset", "feTile", "feTurbulence",
}
TAGS |= SVG_TAGS

# Safe SVG attributes
SVG_ATTRIBUTES = {
    # Presentation.
    "alignment-baseline", "clip-path", "clip-rule", "color",
    "color-interpolation-filters", "display", "dominant-baseline", "fill",
    "fill-opacity", "fill-rule", "filter", "flood-color", "flood-opacity",
    "font-family", "font-size", "font-style", "font-weight", "letter-spacing",
    "marker-end", "marker-mid", "marker-start", "mask", "opacity", "paint-order",
    "shape-rendering", "stop-color", "stop-opacity", "stroke",
    "stroke-dasharray", "stroke-dashoffset", "stroke-linecap",
    "stroke-linejoin", "stroke-miterlimit", "stroke-opacity", "stroke-width",
    "text-anchor", "text-decoration", "transform", "vector-effect", "visibility",
    "word-spacing", "writing-mode",
    # Geometry and coordinate systems.
    "cx", "cy", "d", "dx", "dy", "fr", "fx", "fy", "height", "lengthAdjust",
    "offset", "pathLength", "points", "preserveAspectRatio", "r", "rotate",
    "rx", "ry", "startOffset", "textLength", "viewBox", "width", "x", "x1",
    "x2", "y", "y1", "y2",
    # Paint servers, clipping, masking and markers.
    "clipPathUnits", "gradientTransform", "gradientUnits", "markerHeight",
    "markerUnits", "markerWidth", "maskContentUnits", "maskUnits", "orient",
    "patternContentUnits", "patternTransform", "patternUnits", "refX", "refY",
    "spreadMethod",
    # Filters.
    "amplitude", "baseFrequency", "bias", "divisor", "edgeMode", "exponent",
    "filterUnits", "in", "in2", "intercept", "k1", "k2", "k3", "k4",
    "kernelMatrix", "mode", "numOctaves", "operator", "order", "preserveAlpha",
    "primitiveUnits", "radius", "result", "scale", "seed", "slope",
    "stdDeviation", "stitchTiles", "tableValues", "targetX", "targetY", "type",
    "values", "xChannelSelector", "yChannelSelector",
    # The drawing itself.
    "focusable", "version", "xmlns", "xmlns:xlink",
    # A reference to another element in the drawing: <use>, <textPath>, and a
    # gradient, pattern or filter built on another.
    "href", "xlink:href",
}

ATTRIBUTES = {
    "*": {
        "class", "dir", "draggable", "hidden", "id", "lang", "role", "tabindex",
        "title",
    },
    "a": {"download", "href", "hreflang", "rel", "target", "type"},
    "blockquote": {"cite"},
    "button": {"type"},
    "col": {"span"},
    "colgroup": {"span"},
    "data": {"value"},
    "del": {"cite", "datetime"},
    "details": {"open"},
    "img": {"alt", "height", "loading", "sizes", "src", "srcset", "width"},
    "ins": {"cite", "datetime"},
    "li": {"value"},
    "ol": {"reversed", "start", "type"},
    "q": {"cite"},
    "source": {"media", "sizes", "srcset", "type"},
    "td": {"colspan", "headers", "rowspan"},
    "th": {"abbr", "colspan", "headers", "rowspan", "scope"},
    "time": {"datetime"},
    **{tag: SVG_ATTRIBUTES for tag in SVG_TAGS},
}

# Relative URLs are kept as well as these.
URL_SCHEMES = {"http", "https", "mailto", "tel"}

# url(...) in a drawing's paint, clip, mask, marker or filter: only a reference
# to an element in the drawing, url(#id), is kept.
_EXTERNAL_URL_RE = re.compile(r"url\(\s*['\"]?\s*(?!#)", re.IGNORECASE)


def _keep_references_in_the_drawing(tag: str, attribute: str, value: str):
    """Drop an attribute that would point a drawing at anything but itself.

    A drawing's references load another file when they are not to an id in the
    same page -- ``<use href="other.svg#icon">``, ``fill="url(other.svg#g)"``.
    The policy keeps those to this site, and the parser would keep them still,
    so they are held to ``#id`` here. A link (``<a href>``) is not a reference
    of this kind and is checked against ``URL_SCHEMES`` instead.
    """
    if tag in SVG_TAGS:
        if attribute in {"href", "xlink:href"} and not value.strip().startswith("#"):
            return None
        if _EXTERNAL_URL_RE.search(value):
            return None
    return value


def sanitise_raw_html(html: str) -> str:
    """``html`` with everything outside the allowlist removed.

    A tag outside the list is dropped and its text kept, except a script's or a
    style sheet's. Links keep the ``rel`` an editor inserts.
    """
    return nh3.clean(
        html or "",
        tags=TAGS,
        clean_content_tags={"script", "style"},
        attributes=ATTRIBUTES,
        generic_attribute_prefixes={"aria-", "data-"},
        url_schemes=URL_SCHEMES,
        attribute_filter=_keep_references_in_the_drawing,
        link_rel=None,
        strip_comments=True,
    )
