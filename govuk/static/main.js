// module style javascript entry point after the main GOV.UK Frontend script has been loaded

// on page load
document.addEventListener("DOMContentLoaded", function () {
  setHyperlinkClasses();
  setListClasses();
  setAutoHeadingNavigation();
  addStartButtonSVG();
});

function setHyperlinkClasses() {
  const richTextContents = document.querySelectorAll(".rich-text-content");
  const masthead = document.querySelectorAll(".masthead");
  [richTextContents, masthead].forEach((content) => {
    content.forEach((element) => {
      const links = element.querySelectorAll("a");
      links.forEach((link) => {
        if (link.classList.contains("govuk-button")) {
          return;
        }
        if (element.classList.contains("masthead")) {
          if (element.classList.contains("masthead--combined")) {
            link.classList.add("govuk-link--inverse");
          }
        }
        link.classList.add("govuk-link");
      });
    });
  });
}

function setListClasses() {
  // Paragraphs. GOV.UK Frontend styles the govuk-body class rather than the
  // element, so rich text paragraphs need it adding. Anything that already
  // asks for a size, such as govuk-body-s, is left as the author set it.
  document.querySelectorAll(".rich-text-content p").forEach((el) => {
    const sized = Array.from(el.classList).some((name) =>
      name.startsWith("govuk-body"),
    );
    if (!sized) {
      el.classList.add("govuk-body");
    }
  });

  // Unordered lists
  document.querySelectorAll(".rich-text-content ul").forEach((el) => {
    el.classList.add("govuk-list", "govuk-list--bullet");
  });

  // Ordered lists
  document.querySelectorAll(".rich-text-content ol").forEach((el) => {
    el.classList.add("govuk-list", "govuk-list--number");
  });
}

function addStartButtonSVG() {
  document.querySelectorAll(".govuk-button--start").forEach((button) => {
    if (!button.querySelector("svg")) {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("class", "govuk-button__start-icon");
      svg.setAttribute("xmlns", "http://www.w3.org/2000/svg");
      svg.setAttribute("width", "17.5");
      svg.setAttribute("height", "19");
      svg.setAttribute("viewBox", "0 0 33 40");
      svg.setAttribute("aria-hidden", "true");
      svg.setAttribute("focusable", "false");
      svg.innerHTML =
        '<path fill="currentColor" d="M0 0h13l20 20-20 20H0l20-20z" />';
      button.appendChild(svg);
    }
  });
}

function setAutoHeadingNavigation() {
  const headingLayouts = document.querySelectorAll(
    "[data-auto-heading-layout]",
  );
  headingLayouts.forEach((layout) => {
    const headingSource = layout.querySelector("[data-auto-heading-source]");
    const headingNav = layout.querySelector("[data-auto-heading-nav]");
    const mainColumn = layout.querySelector("[data-auto-heading-main-column]");
    const sideColumn = layout.querySelector("[data-auto-heading-side-column]");

    if (!headingSource || !headingNav || !mainColumn || !sideColumn) {
      return;
    }

    const headings = headingSource.querySelectorAll("h2, h3, h4");
    const existingIds = new Set(
      Array.from(document.querySelectorAll("[id]"), (element) => element.id),
    );
    const headingItems = [];

    headings.forEach((heading, index) => {
      const text = (heading.textContent || "").trim();
      if (!text) {
        return;
      }

      if (!heading.id) {
        heading.id = getUniqueHeadingId(text, existingIds, index + 1);
      } else {
        existingIds.add(heading.id);
      }

      headingItems.push({
        level: heading.tagName.toLowerCase(),
        text,
        id: heading.id,
      });
    });

    if (!headingItems.length) {
      sideColumn.hidden = true;
      mainColumn.classList.remove("govuk-grid-column-two-thirds");
      mainColumn.classList.add("govuk-grid-column-full");
      return;
    }

    const list = document.createElement("ul");
    list.className = "govuk-list free-text-heading-nav__list";

    headingItems.forEach((item) => {
      const listItem = document.createElement("li");
      listItem.className = "free-text-heading-nav__item";
      if (item.level !== "h2") {
        listItem.classList.add("free-text-heading-nav__item--nested");
      }

      const link = document.createElement("a");
      link.className = "govuk-link govuk-link--no-visited-state";
      link.href = "#" + item.id;
      link.textContent = item.text;

      listItem.appendChild(link);
      list.appendChild(listItem);
    });

    headingNav.appendChild(list);
  });
}

function getUniqueHeadingId(text, existingIds, fallbackIndex) {
  const baseId = text
    .toLowerCase()
    .replace(/['’"]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  const safeBaseId = baseId || "section-" + fallbackIndex;

  let candidate = safeBaseId;
  let counter = 2;
  while (existingIds.has(candidate)) {
    candidate = safeBaseId + "-" + counter;
    counter += 1;
  }

  existingIds.add(candidate);
  return candidate;
}
