// The catalogue filter dropdowns' counts follow the form as the reader changes
// it, before they apply it. The form still only applies on Apply: changing a
// select must not reload the page (WCAG 3.2.2). Without JavaScript the counts
// are those of the applied filters.

const SEARCH_DELAY_MS = 400;

function formParams(form) {
  const params = new URLSearchParams();
  for (const [name, value] of new FormData(form)) {
    if (value.trim()) {
      params.append(name, value.trim());
    }
  }
  return params;
}

// The applied filters are the URL's, not the form's at load: going back can
// restore form values that were never applied.
function appliedParams(form) {
  const url = new URLSearchParams(window.location.search);
  const params = new URLSearchParams();
  for (const element of form.elements) {
    const value = element.name ? (url.get(element.name) || "").trim() : "";
    if (value) {
      params.append(element.name, value);
    }
  }
  return params;
}

function updateOptions(select, options) {
  const existing = new Map(
    Array.from(select.options, (option) => [option.value, option]),
  );
  // After "All". A value with nothing is added only once it has something,
  // and kept at (0) afterwards rather than taken out from under the reader.
  let index = 1;
  for (const { value, label, count } of options) {
    let option = existing.get(value);
    if (!option) {
      if (!count) {
        continue;
      }
      option = new Option("", value);
      select.add(option, index);
    }
    option.text = `${label} (${count})`;
    index += 1;
  }
}

function setCatalogueFilterCounts(form) {
  const status = form.querySelector("[data-catalogue-filters-status]");
  const applied = appliedParams(form).toString();
  let controller = null;
  let timer = null;

  async function refresh() {
    const params = formParams(form);
    const pending = params.toString() !== applied;
    params.set("counts", "1");

    if (controller) {
      controller.abort();
    }
    controller = new AbortController();
    let data;
    try {
      const response = await fetch(`${window.location.pathname}?${params}`, {
        credentials: "same-origin",
        headers: { Accept: "application/json" },
        signal: controller.signal,
      });
      if (!response.ok) {
        return;
      }
      data = await response.json();
    } catch (error) {
      // Superseded by a newer request, or offline: the counts stay as they are.
      return;
    }

    for (const [name, options] of Object.entries(data.fields)) {
      const select = form.querySelector(`select[name="${name}"]`);
      if (select) {
        updateOptions(select, options);
      }
    }
    if (status) {
      const results = `${data.count} result${data.count === 1 ? "" : "s"}`;
      status.textContent = pending ? `${results}. Select Apply to show them.` : "";
    }
  }

  form.addEventListener("change", (event) => {
    if (event.target.tagName === "SELECT") {
      refresh();
    }
  });
  form.addEventListener("input", (event) => {
    if (event.target.type === "search") {
      clearTimeout(timer);
      timer = setTimeout(refresh, SEARCH_DELAY_MS);
    }
  });

  if (formParams(form).toString() !== applied) {
    refresh();
  }
}

document
  .querySelectorAll("form[data-catalogue-filters]")
  .forEach(setCatalogueFilterCounts);
