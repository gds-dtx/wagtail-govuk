// module style javascript entry point after the main GOV.UK Frontend script has been loaded

// on page load
document.addEventListener("DOMContentLoaded", function () {
  setHyperlinkClasses();
  setListClasses();
  setAutoHeadingNavigation();
  addStartButtonSVG();
  setBackToTop();
  setChangelogToggle();
  openLinkedAccordionSection();
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

// The size a wrapper asks its contents to take, as the hero intro does with
// govuk-body-l and the changelog does with govuk-body-s, or null if none does.
// closest() includes the element itself, so something already sized keeps the
// size it was given.
function wrapperTextSize(el) {
  const wrapper = el.closest("[class*='govuk-body-']");
  return (
    (wrapper &&
      Array.from(wrapper.classList).find((name) =>
        name.startsWith("govuk-body-"),
      )) ||
    null
  );
}

function setListClasses() {
  // Paragraphs. GOV.UK Frontend styles the govuk-body class rather than the
  // element, so rich text paragraphs need it adding. Anything that already
  // asks for a size, such as govuk-body-s, is left as the author set it.
  document.querySelectorAll(".rich-text-content p").forEach((el) => {
    if (
      Array.from(el.classList).some((name) => name.startsWith("govuk-body"))
    ) {
      return;
    }

    // A wrapper can ask for the size instead. Adding govuk-body would override
    // that size from the inside, and adding nothing leaves the paragraph on
    // the browser's own 1em margins: the bundle styles the class rather than
    // the element, so there is nothing else to give a paragraph GOV.UK's
    // spacing, and the intro is pushed down by a margin the Design System does
    // not put there. Repeating the wrapper's class carries the size and the
    // spacing together.
    el.classList.add(wrapperTextSize(el) || "govuk-body");
  });

  // Lists. govuk-list carries the 19px body size of its own accord, so in a
  // wrapper that asked for a smaller one the bullets came out larger than the
  // paragraphs beside them -- reported on the home page, where the update
  // history sits in a govuk-body-s block, and true of a role page whose
  // changelog note has bullets. Repeating the wrapper's size puts the two back
  // in step. govuk-body-s is defined after govuk-list in the Frontend bundle,
  // so it wins on source order and needs no !important; the welcome page's
  // contents list already carries both classes by hand for the same reason.
  const listModifier = { UL: "govuk-list--bullet", OL: "govuk-list--number" };
  document
    .querySelectorAll(".rich-text-content ul, .rich-text-content ol")
    .forEach((el) => {
      el.classList.add("govuk-list", listModifier[el.tagName]);

      const wrapperSize = wrapperTextSize(el);
      if (wrapperSize) {
        el.classList.add(wrapperSize);
      }
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

function setBackToTop() {
  // The button is hidden in CSS and only ever revealed here, so a visitor
  // without JavaScript is not offered a control that cannot work.
  const button = document.getElementById("back-to-top");
  if (!button) {
    return;
  }

  const footer = document.querySelector(".govuk-template__footer");

  function update() {
    const scrolled = window.pageYOffset || document.documentElement.scrollTop;
    button.classList.toggle(
      "back-to-top--visible",
      scrolled > window.innerHeight,
    );

    // Lift the button clear of the footer rather than letting it sit on top.
    let bottom = 30;
    if (footer) {
      const overlap = window.innerHeight - footer.getBoundingClientRect().top;
      if (overlap > 0) {
        bottom = overlap + 30;
      }
    }
    button.style.bottom = bottom + "px";
  }

  button.addEventListener("click", function () {
    window.scrollTo(0, 0);
    // Send keyboard focus back to the top of the page as well as the view.
    const skipLink = document.querySelector(".govuk-skip-link");
    if (skipLink) {
      skipLink.focus();
    }
  });

  window.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update, { passive: true });
  update();
}

function setChangelogToggle() {
  // The update history is long, so it is collapsed once JavaScript can offer a
  // way to open it again. Without JavaScript it stays open and the toggle stays
  // hidden.
  const toggle = document.getElementById("toggle-link");
  const panel = document.getElementById("collapsible-div");
  if (!toggle || !panel) {
    return;
  }

  function setExpanded(expanded) {
    panel.hidden = !expanded;
    toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    toggle.textContent = expanded
      ? toggle.dataset.hideText || "- hide all updates"
      : toggle.dataset.showText || "+ show all updates";
  }

  toggle.hidden = false;
  setExpanded(false);

  toggle.addEventListener("click", function (event) {
    event.preventDefault();
    setExpanded(panel.hidden);
  });

  // "See all updates" at the top of the page opens the history as well as
  // jumping to it, so the anchor still works on its own.
  const jumpLink = document.getElementById("jump-link");
  if (jumpLink) {
    jumpLink.addEventListener("click", function () {
      setExpanded(true);
    });
  }
}

function openLinkedAccordionSection() {
  // Skill names on a role page deep link into the Skills A to Z, which is an
  // accordion, so the section being linked to has to be opened.
  const accordion = document.querySelector(".govuk-accordion");
  if (!accordion) {
    return;
  }

  function openFromHash() {
    const hash = window.location.hash;
    if (hash.length < 2) {
      return;
    }

    let target = null;
    try {
      target = document.getElementById(decodeURIComponent(hash.slice(1)));
    } catch (error) {
      return;
    }
    if (!target) {
      return;
    }

    const section = target.closest(".govuk-accordion__section");
    if (!section) {
      return;
    }

    const button = section.querySelector(".govuk-accordion__section-button");
    if (button && button.getAttribute("aria-expanded") === "false") {
      button.click();
    }
    target.scrollIntoView();
  }

  openFromHash();
  window.addEventListener("hashchange", openFromHash);
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
