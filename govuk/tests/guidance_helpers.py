"""Small source repos for the guidance-as-code tests.

Built in memory, so the tests never touch the network: ``tech_docs_files`` is a
cut-down The GDS Way (the layout, ERB and navigation partials it really uses),
``manifest_files`` the same guidance in the ``guidance.yml`` format, and
``tarball`` wraps either the way GitHub's codeload does.
"""

import io
import tarfile
import textwrap

COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40


def _dedent(text: str) -> bytes:
    return textwrap.dedent(text).lstrip("\n").encode()


def tech_docs_files() -> dict[str, bytes | None]:
    return {
        "config/tech-docs.yml": _dedent(
            """
            host: https://example-way.gov.uk
            service_name: The Example Way
            description: Standards for example teams
            default_owner_slack: '#example-way'
            owner_slack_workspace: example
            redirects:
              /standards/old-alerting.html: /standards/alerting.html#sending-alerts
            """
        ),
        "config.rb": b"set :layout, 'custom'\n",
        "source/layouts/custom.erb": _dedent(
            """
            <% wrap_layout :layout do %>
              <%= partial 'partials/site-banner' %>
              <%= yield %>
            <% end %>
            """
        ),
        "source/partials/_site-banner.html.erb": _dedent(
            """
            <div class="page-banner"><p><strong>For internal use.</strong></p></div>
            """
        ),
        "source/partials/_nav-operating.html.erb": _dedent(
            """
            <ul>
              <li><a href="/standards/alerting.html">Manage alerts</a></li>
              <li><a href="/standards/source-code/index.html">Store source code</a></li>
            </ul>
            """
        ),
        "source/index.html.md.erb": _dedent(
            """
            ---
            layout: core
            title: The Example Way
            ---

            <% content_for :sidebar do %>
            <ul>
              <li><a href='#the-example-way'>The Example Way</a>
                <ul><li><a href='#about'>About</a></li></ul>
              </li>
              <li><a href="#operating">Operating a service</a>
                <%= partial 'partials/nav-operating' %>
              </li>
            </ul>
            <% end %>

            <%= partial 'partials/site-banner' %>

            # <%= current_page.data.title %>

            ## About

            Example text with a [link to alerting](/standards/alerting.html).

            ## Withdrawn guidance

            <ul>
                <% sitemap.resources.each do |resource| %>
                    <% if resource.options[:layout].eql? "withdrawn" %>
                        <li><a href="<%= resource.url =%>"><%= resource.data.title =%></a></li>
                    <% end %>
                <% end %>
            </ul>
            """
        ),
        "source/standards/alerting.html.md.erb": _dedent(
            """
            ---
            title: How to manage alerts
            last_reviewed_on: 2026-01-31
            review_in: 1 month
            owner_slack: '#alerts'
            ---

            # <%= current_page.data.title %>

            Read about [monitoring][] first.

            ## Sending alerts

            <%= warning_text('Do not page people <a href="https://example.com">at night</a>.') %>

            ### Overview

            ## Prioritising alerts

            ### Overview

            | Priority | Meaning |
            |----------|---------|
            | # P1     | Now     |

            The year is <%= Time.current.year %>. <%= some_unknown_helper %>

            [monitoring]: monitoring.html
            """
        ),
        "source/standards/monitoring.html.md.erb": _dedent(
            """
            ---
            title: How to monitor
            last_reviewed_on: 2026-01-01
            review_in: 6 months
            ---

            # <%= current_page.data.title %>

            See the [editorconfig](../manuals/editorconfig) and a [missing page](/nope.html).

            <script>alert("x")</script><p onclick="steal()" style="color:red" class="evil">Styled</p>
            """
        ),
        "source/standards/source-code/index.html.md.erb": _dedent(
            """
            ---
            layout: core
            title: How to store source code
            ---

            <% content_for :sidebar do %>
            <ul>
              <li><a href='#how-to-store-source-code'>How to store source code</a>
                <ul><li><a href="use-github.html">Use GitHub</a></li></ul>
              </li>
            </ul>
            <% end %>

            # <%= current_page.data.title %>

            Go and [use GitHub](use-github.html#rules).
            """
        ),
        "source/standards/source-code/use-github.html.md.erb": _dedent(
            """
            ---
            title: Use GitHub
            ---

            # <%= current_page.data.title %>

            ## Rules
            """
        ),
        "source/standards/operating-systems.html.md.erb": _dedent(
            """
            ---
            title: Operating systems
            layout: withdrawn
            ---

            # <%= current_page.data.title %>

            Old advice that is no longer followed.
            """
        ),
        "source/accessibility.html.md.erb": _dedent(
            """
            ---
            title: Accessibility statement
            hide_in_navigation: true
            ---

            # Accessibility statement
            """
        ),
        "source/manuals/editorconfig": None,
        "source/images/logo.png": None,
    }


def manifest_files() -> dict[str, bytes | None]:
    return {
        "guidance.yml": _dedent(
            """
            schema: guidance-as-code/v0
            title: The Example Way
            description: Standards for example teams
            banner: "**For internal use.**"
            owner: {default: "#example-way", slack_workspace: example}
            content_dir: docs
            navigation:
              - title: The Example Way
                pages: [index.md#about]
              - title: Operating a service
                pages:
                  - path: standards/alerting.md
                    pages:
                      - standards/alerting.md#sending-alerts
                      - standards/alerting/escalation.md
                  - {path: standards/missing.md, title: Missing}
                  - {url: "https://www.gov.uk/service-manual", title: Service Manual}
            sections:
              - page: standards/orphan.md
                title: Orphaned section
                pages: [standards/alerting.md]
            redirects:
              old/alerting.html: standards/alerting.md
            """
        ),
        "docs/index.md": _dedent(
            """
            ---
            title: The Example Way
            ---

            # The Example Way

            Start with [alerting](standards/alerting.md).
            """
        ),
        "docs/standards/alerting.md": _dedent(
            """
            ---
            title: How to manage alerts
            last_reviewed_on: 2026-01-31
            review_in: 12 months
            ---

            # How to manage alerts

            ## Sending alerts

            ## Sending alerts

            > [!WARNING]
            > Do not page people at night.

            > [!NOTE]
            > A note.

            ![Diagram](../images/diagram.png)
            """
        ),
        "docs/standards/alerting/escalation.md": b"# Escalating alerts\n",
        "docs/standards/orphan.md": b"# An orphan\n",
        "docs/_drafts/draft.md": b"# Draft\n",
        "README.md": b"# The repo's own readme, outside content_dir\n",
    }


def tarball(files: dict[str, bytes | None], *, top="example-way-" + COMMIT, extra=()) -> bytes:
    """``files`` as a GitHub codeload tarball. ``extra`` adds raw members."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in files.items():
            data = data if data is not None else b"binary"
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for info, data in extra:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return buffer.getvalue()


def fake_fetch(files: dict[str, bytes | None], commit: str = COMMIT):
    """A ``sync_guidance_page(fetch=...)`` that returns ``files`` at ``commit``."""

    def fetch(repository):
        return commit, files

    return fetch


def guidance_flags(*, guidance_enabled: bool = True) -> dict[str, bool]:
    return {"SKILLS": False, "FEEDBACK": False, "GUIDANCE": guidance_enabled}
