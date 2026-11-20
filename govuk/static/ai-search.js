// AI search (FEATURE_AI); the matches and summary come from the ASGI service.

document.addEventListener("DOMContentLoaded", function () {
  setAiSearchBoxes();
  setAiSearchForms();
  setAiSearchToggle();
  setAiSearchSummary();
  setAiSearchMatches();
});

function setAiSearchBoxes() {
  document.querySelectorAll("form[data-ai-search-box]").forEach((form) => {
    const input = form.querySelector('input[name="question"]');
    const message = form.dataset.emptyError;
    if (!input || !message) {
      return;
    }
    form.addEventListener("submit", (event) => {
      if (input.value.trim() !== "") {
        return;
      }
      event.preventDefault();
      const errorId = form.dataset.errorId;
      let error = document.getElementById(errorId);
      if (!error) {
        error = document.createElement("p");
        error.id = errorId;
        error.className = "govuk-error-message app-ai-search-box__error";
        form.querySelector(".app-ai-search-box__bar").after(error);
      }
      // An alert, as the box already has focus when sent with Enter.
      error.setAttribute("role", "alert");
      error.innerHTML = '<span class="govuk-visually-hidden">Error:</span> ';
      error.append(message);
      form.classList.add("app-ai-search-box--error");
      if (!form.classList.contains("app-ai-search-box--inverse")) {
        input.classList.add("govuk-input--error");
      }
      input.setAttribute("aria-invalid", "true");
      const described = (input.getAttribute("aria-describedby") || "").split(" ").filter(Boolean);
      if (!described.includes(errorId)) {
        input.setAttribute("aria-describedby", [errorId, ...described].join(" "));
      }
      input.value = "";
      input.focus();
    });
  });
}

function setAiSearchToggle() {
  // Without JavaScript, or on a page with no search area, the link to /search/ stays.
  const button = document.querySelector(".js-ai-search-toggle");
  if (!button) {
    return;
  }
  const panel = document.getElementById(button.getAttribute("aria-controls"));
  const link = document.querySelector(".js-ai-search-link");
  if (!panel) {
    return;
  }
  const input = panel.querySelector('input[name="question"]');
  const setOpen = (open) => {
    button.setAttribute("aria-expanded", open ? "true" : "false");
    panel.hidden = !open;
    if (open && input) {
      input.focus();
    }
  };
  if (link) {
    link.hidden = true;
  }
  button.hidden = false;
  button.addEventListener("click", () => {
    setOpen(button.getAttribute("aria-expanded") !== "true");
  });
  panel.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) {
      setOpen(false);
      button.focus();
    }
  });
}

function setAiSearchForms() {
  // "js" makes /search/ answer at once, leaving the matches to setAiSearchMatches.
  // Registered after setAiSearchBoxes, so an empty submit it stopped is left alone.
  document.querySelectorAll("[data-ai-search-choice]").forEach((choice) => {
    choice.hidden = false;
  });
  document.querySelectorAll("form[data-ai-search-form]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented || form.querySelector('input[name="js"]')) {
        return;
      }
      const flag = document.createElement("input");
      flag.type = "hidden";
      flag.name = "js";
      flag.value = "1";
      form.append(flag);
    });
  });
}

function aiSearchPost(url, fields) {
  const data = new URLSearchParams();
  Object.entries(fields).forEach(([name, value]) => data.set(name, value));
  return fetch(url, {
    method: "POST",
    body: data,
    credentials: "omit",
    headers: { Accept: "application/json" },
  });
}

function fetchAiSearchSummary(region) {
  // Shown whole, so the live region reads it out once rather than word by word.
  const status = region.querySelector(".js-ai-search-summary-status");
  const body = region.querySelector(".js-ai-search-summary-body");
  const label = region.querySelector(".js-ai-search-summary-label");
  aiSearchPost(region.dataset.url, { ticket: region.dataset.ticket || "" })
    .then((response) => (response.ok ? response.json() : null))
    .then((result) => {
      if (!result || result.status !== "ok" || !result.html) {
        region.remove();
        return;
      }
      // The server built this HTML from a fixed set of tags and its own links.
      body.innerHTML = result.html;
      status.hidden = true;
      body.hidden = false;
      label.hidden = false;
    })
    .catch(() => {
      region.remove();
    });
}

function setAiSearchSummary() {
  // With no ticket yet, setAiSearchMatches fetches the summary once it has the matches.
  const region = document.querySelector("[data-ai-search-summary]");
  if (!region) {
    return;
  }
  region.hidden = false;
  if (region.dataset.ticket) {
    fetchAiSearchSummary(region);
  }
}

function setAiSearchMatches() {
  const container = document.querySelector("[data-ai-search-matches]");
  if (!container) {
    return;
  }
  const announce = document.querySelector("[data-ai-search-matches-status]");
  const region = document.querySelector("[data-ai-search-summary]");
  const limit = document.querySelector("[data-ai-search-limit]");
  const show = (html) => {
    // The server rendered this from its own template.
    container.innerHTML = html;
    container.removeAttribute("aria-busy");
    if (announce) {
      const heading = container.querySelector("h2, h3");
      announce.textContent = heading ? heading.textContent : "";
    }
  };
  aiSearchPost(container.dataset.url, {
    question: container.dataset.question || "",
    access: container.dataset.access || "",
  })
    .then((response) => response.json().catch(() => null))
    .then((result) => {
      if (!result || !result.html) {
        throw new Error("No matches");
      }
      if (result.warning && limit) {
        // The server built this from its own fixed wording.
        limit.innerHTML = result.warning;
      }
      show(result.html);
      if (!region) {
        return;
      }
      if (result.ticket) {
        region.dataset.ticket = result.ticket;
        fetchAiSearchSummary(region);
      } else {
        region.remove();
      }
    })
    .catch(() => {
      show(
        '<h2 class="govuk-heading-l">Sorry, there is a problem</h2>' +
          '<p class="govuk-body">The matches could not be found. Try again later.</p>',
      );
      if (region) {
        region.remove();
      }
    });
}
