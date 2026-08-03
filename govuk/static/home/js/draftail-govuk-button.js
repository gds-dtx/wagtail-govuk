(function initGovukButtonDraftailPlugin() {
  if (!window.draftail || !window.draftail.registerPlugin) {
    return;
  }

  if (!window.React || !window.draftail.TooltipEntity) {
    return;
  }

  var LinkModalWorkflowSource = window.draftail.LinkModalWorkflowSource;
  if (!LinkModalWorkflowSource) {
    return;
  }

  var BUTTON_STYLES = [
    { value: "default", label: "Default" },
    { value: "start", label: "Start" },
    { value: "secondary", label: "Secondary" },
    { value: "warning", label: "Warning" },
  ];
  var VALID_STYLES = BUTTON_STYLES.map(function (choice) {
    return choice.value;
  });

  function readExistingOptions(props) {
    var options = { style: "default", newTab: false };
    if (props && props.entity && typeof props.entity.getData === "function") {
      var data = props.entity.getData() || {};
      if (VALID_STYLES.indexOf(data.style) !== -1) {
        options.style = data.style;
      }
      options.newTab = data.newTab === true || data.newTab === "true";
    }
    return options;
  }

  // Presents a small modal to collect the button variant and target before the
  // standard Wagtail link chooser opens. Kept as plain DOM so it does not depend
  // on Wagtail's internal modal components.
  function showOptionsDialog(initial, onConfirm, onCancel) {
    var overlay = document.createElement("div");
    overlay.className = "govuk-button-options-overlay";
    overlay.style.cssText =
      "position:fixed;inset:0;z-index:20000;display:flex;align-items:flex-start;" +
      "justify-content:center;background:rgba(0,0,0,0.5);padding-top:10vh;";

    var dialog = document.createElement("div");
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-label", "Button options");
    dialog.style.cssText =
      "background:#fff;border-radius:4px;padding:2rem;width:360px;max-width:90vw;" +
      "box-shadow:0 2px 8px rgba(0,0,0,0.3);font-family:inherit;";

    var heading = document.createElement("h2");
    heading.textContent = "Button options";
    heading.style.cssText = "margin:0 0 1rem;font-size:1.2rem;";

    var styleLabel = document.createElement("label");
    styleLabel.textContent = "Style";
    styleLabel.style.cssText = "display:block;font-weight:bold;margin-bottom:0.25rem;";
    styleLabel.setAttribute("for", "govuk-button-style-select");

    var styleSelect = document.createElement("select");
    styleSelect.id = "govuk-button-style-select";
    styleSelect.style.cssText = "width:100%;margin-bottom:1rem;padding:0.4rem;";
    BUTTON_STYLES.forEach(function (choice) {
      var option = document.createElement("option");
      option.value = choice.value;
      option.textContent = choice.label;
      if (choice.value === initial.style) {
        option.selected = true;
      }
      styleSelect.appendChild(option);
    });

    var newTabWrapper = document.createElement("label");
    newTabWrapper.style.cssText =
      "display:flex;align-items:center;gap:0.5rem;margin-bottom:1.5rem;";
    var newTabCheckbox = document.createElement("input");
    newTabCheckbox.type = "checkbox";
    newTabCheckbox.id = "govuk-button-new-tab";
    newTabCheckbox.checked = !!initial.newTab;
    var newTabText = document.createElement("span");
    newTabText.textContent = "Open in a new tab";
    newTabWrapper.appendChild(newTabCheckbox);
    newTabWrapper.appendChild(newTabText);

    var actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:0.75rem;justify-content:flex-end;";

    var cancelButton = document.createElement("button");
    cancelButton.type = "button";
    cancelButton.className = "button button-secondary";
    cancelButton.textContent = "Cancel";

    var continueButton = document.createElement("button");
    continueButton.type = "button";
    continueButton.className = "button";
    continueButton.textContent = "Continue";

    actions.appendChild(cancelButton);
    actions.appendChild(continueButton);

    dialog.appendChild(heading);
    dialog.appendChild(styleLabel);
    dialog.appendChild(styleSelect);
    dialog.appendChild(newTabWrapper);
    dialog.appendChild(actions);
    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    var previouslyFocused = document.activeElement;

    function cleanup() {
      document.removeEventListener("keydown", onKeyDown, true);
      if (overlay.parentNode) {
        overlay.parentNode.removeChild(overlay);
      }
      if (previouslyFocused && typeof previouslyFocused.focus === "function") {
        previouslyFocused.focus();
      }
    }

    function confirm() {
      var options = {
        style:
          VALID_STYLES.indexOf(styleSelect.value) !== -1
            ? styleSelect.value
            : "default",
        newTab: newTabCheckbox.checked,
      };
      cleanup();
      onConfirm(options);
    }

    function cancel() {
      cleanup();
      onCancel();
    }

    function onKeyDown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        cancel();
      }
    }

    continueButton.addEventListener("click", confirm);
    cancelButton.addEventListener("click", cancel);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) {
        cancel();
      }
    });
    document.addEventListener("keydown", onKeyDown, true);

    styleSelect.focus();
  }

  // One entity type covers every variant: collect the options first, then defer to
  // Wagtail's link chooser and merge the options into the stored entity data.
  class GovukButtonSource extends LinkModalWorkflowSource {
    componentDidMount() {
      var self = this;
      var initial = readExistingOptions(this.props);
      showOptionsDialog(
        initial,
        function (options) {
          self.buttonOptions = options;
          super_componentDidMount.call(self);
        },
        function () {
          self.props.onClose();
        }
      );
    }

    filterEntityData(data) {
      var base = super.filterEntityData(data);
      var options = this.buttonOptions || { style: "default", newTab: false };
      return Object.assign({}, base, {
        style: options.style,
        newTab: options.newTab,
      });
    }
  }

  // Captured because the arrow callback above cannot use `super`.
  var super_componentDidMount = LinkModalWorkflowSource.prototype.componentDidMount;

  function styleLabel(style) {
    var match = BUTTON_STYLES.filter(function (choice) {
      return choice.value === style;
    })[0];
    return match ? match.label : "Default";
  }

  function buttonDecorator(props) {
    var entity = props.contentState.getEntity(props.entityKey);
    var data = entity.getData() || {};
    var label = "Button link";
    if (data.style && data.style !== "default") {
      label += " (" + styleLabel(data.style) + ")";
    }
    return window.React.createElement(
      window.draftail.TooltipEntity,
      Object.assign({}, props, {
        icon: "link",
        label: label,
        url: data.url || "",
      }),
      props.children
    );
  }

  window.draftail.registerPlugin(
    {
      type: "GOVUK_BUTTON_LINK",
      source: GovukButtonSource,
      decorator: buttonDecorator,
    },
    "entityTypes"
  );
})();
