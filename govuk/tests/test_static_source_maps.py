"""Every vendored script or stylesheet that names a source map ships with it.

The image build runs ``collectstatic`` under whitenoise's manifest storage,
which follows a file's ``sourceMappingURL`` comment and refuses to build if the
map it names is not there. The tests run under the plain storage, which does
not look, so a vendored file missing its map passes every test and then fails
the build -- which is what happened with accessible-autocomplete on
10 September 2026. This is the check the build would otherwise make for us.
"""

import re
from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"
SOURCE_MAP = re.compile(r"sourceMappingURL=([^\s*]+)")


class VendoredSourceMapsTests(SimpleTestCase):
    def test_every_named_source_map_is_present(self):
        missing = []
        for path in list(STATIC_ROOT.rglob("*.js")) + list(STATIC_ROOT.rglob("*.css")):
            tail = path.read_text(errors="ignore")[-400:]
            for name in SOURCE_MAP.findall(tail):
                relative = str(path.relative_to(STATIC_ROOT).parent / name).lstrip("./")
                if finders.find(relative) is None:
                    missing.append(f"{path.relative_to(STATIC_ROOT)} -> {name}")
        self.assertEqual(missing, [], "source maps the build would fail on")

    def test_the_autocomplete_library_names_its_own_versioned_map(self):
        """The upstream file names an unversioned map; ours is renamed to carry
        the version, so the comment has to follow."""
        library = (STATIC_ROOT / "accessible-autocomplete-3.0.1.min.js").read_text()
        self.assertIn("sourceMappingURL=accessible-autocomplete-3.0.1.min.js.map", library)
        self.assertIsNotNone(finders.find("accessible-autocomplete-3.0.1.min.js.map"))
