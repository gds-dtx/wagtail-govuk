(function initRawHtmlDraftailPlugin() {
  if (!window.draftail || !window.draftail.registerPlugin) {
    return;
  }

  if (!window.React || !window.ReactDOM || !window.DraftJS) {
    return;
  }

  if (!window.React.useEffect || !window.React.useRef || !window.React.useState) {
    return;
  }

  if (!window.ReactDOM.createPortal) {
    return;
  }

  if (!window.DraftJS.AtomicBlockUtils || !window.DraftJS.EditorState) {
    return;
  }

  var React = window.React;
  var ReactDOM = window.ReactDOM;
  var AtomicBlockUtils = window.DraftJS.AtomicBlockUtils;
  var EditorState = window.DraftJS.EditorState;

  var modalOverlayStyle = {
    alignItems: "center",
    backgroundColor: "rgba(11, 12, 12, 0.66)",
    bottom: "0",
    display: "flex",
    justifyContent: "center",
    left: "0",
    padding: "1.5rem",
    position: "fixed",
    right: "0",
    top: "0",
    zIndex: 200000,
  };

  var modalCardStyle = {
    backgroundColor: "#ffffff",
    border: "1px solid #b1b4b6",
    borderRadius: "4px",
    boxShadow: "0 8px 24px rgba(11, 12, 12, 0.24)",
    display: "flex",
    flexDirection: "column",
    maxHeight: "calc(100vh - 3rem)",
    maxWidth: "1080px",
    width: "100%",
  };

  var modalHeaderStyle = {
    borderBottom: "1px solid #d8dde0",
    padding: "1rem 1.25rem 0.75rem",
  };

  var modalTitleStyle = {
    color: "#0b0c0c",
    fontSize: "1.25rem",
    fontWeight: "700",
    margin: "0",
  };

  var modalHintStyle = {
    color: "#505a5f",
    fontSize: "0.95rem",
    margin: "0.5rem 0 0",
  };

  var modalBodyStyle = {
    padding: "1rem 1.25rem 1.25rem",
  };

  var modalErrorStyle = {
    color: "#d4351c",
    fontSize: "0.95rem",
    marginBottom: "0.5rem",
  };

  var textareaStyle = {
    border: "1px solid #505a5f",
    borderRadius: "2px",
    boxSizing: "border-box",
    fontFamily:
      "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, Liberation Mono, monospace",
    fontSize: "0.9rem",
    lineHeight: "1.45",
    minHeight: "420px",
    padding: "0.75rem",
    resize: "vertical",
    width: "100%",
  };

  var modalActionsStyle = {
    alignItems: "center",
    borderTop: "1px solid #d8dde0",
    display: "flex",
    gap: "0.75rem",
    justifyContent: "flex-end",
    padding: "0.75rem 1.25rem",
  };

  function truncateForPreview(value) {
    var collapsed = (value || "").replace(/\s+/g, " ").trim();
    if (collapsed.length <= 120) {
      return collapsed;
    }
    return collapsed.slice(0, 117) + "...";
  }

  function getEntityHtml(editorState, entityKey) {
    if (!editorState || !entityKey) {
      return "";
    }

    try {
      var entity = editorState.getCurrentContent().getEntity(entityKey);
      var data = entity && entity.getData ? entity.getData() : {};
      return (data && data.html) || "";
    } catch (error) {
      return "";
    }
  }

  function RawHtmlModal(props) {
    var initialHtml = props.initialHtml || "";
    var modeLabel = props.modeLabel || "Insert";
    var onCancel = props.onCancel;
    var onSave = props.onSave;
    var textAreaRef = React.useRef(null);
    var _a = React.useState(initialHtml);
    var htmlValue = _a[0];
    var setHtmlValue = _a[1];
    var _b = React.useState("");
    var validationError = _b[0];
    var setValidationError = _b[1];

    React.useEffect(
      function () {
        setHtmlValue(initialHtml);
      },
      [initialHtml]
    );

    React.useEffect(function () {
      var originalOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return function cleanupBodyStyle() {
        document.body.style.overflow = originalOverflow;
      };
    }, []);

    React.useEffect(function () {
      if (textAreaRef.current) {
        textAreaRef.current.focus();
      }
    }, []);

    function submitHtml() {
      if (!htmlValue.trim()) {
        setValidationError("Enter some HTML before saving.");
        return;
      }

      setValidationError("");
      onSave(htmlValue);
    }

    function onTextAreaKeyDown(event) {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        submitHtml();
        return;
      }

      if (event.key === "Escape") {
        event.preventDefault();
        onCancel();
      }
    }

    return ReactDOM.createPortal(
      React.createElement(
        "div",
        {
          style: modalOverlayStyle,
          onClick: onCancel,
          role: "presentation",
        },
        React.createElement(
          "div",
          {
            style: modalCardStyle,
            onClick: function stopPropagation(event) {
              event.stopPropagation();
            },
            role: "dialog",
            "aria-modal": "true",
            "aria-label": "Raw HTML editor",
          },
          React.createElement(
            "div",
            {
              style: modalHeaderStyle,
            },
            React.createElement(
              "h2",
              {
                style: modalTitleStyle,
              },
              modeLabel + " Raw HTML Block"
            ),
            React.createElement(
              "p",
              {
                style: modalHintStyle,
              },
              "Paste or edit HTML. Save with Ctrl/Cmd+Enter."
            )
          ),
          React.createElement(
            "div",
            {
              style: modalBodyStyle,
            },
            validationError
              ? React.createElement(
                  "div",
                  {
                    style: modalErrorStyle,
                    role: "alert",
                  },
                  validationError
                )
              : null,
            React.createElement("textarea", {
              ref: textAreaRef,
              style: textareaStyle,
              value: htmlValue,
              rows: 20,
              onKeyDown: onTextAreaKeyDown,
              onChange: function onTextAreaChange(event) {
                setHtmlValue(event.target.value);
              },
              spellCheck: false,
              "aria-label": "Raw HTML",
            })
          ),
          React.createElement(
            "div",
            {
              style: modalActionsStyle,
            },
            React.createElement(
              "button",
              {
                type: "button",
                className: "button button-secondary",
                onClick: onCancel,
              },
              "Cancel"
            ),
            React.createElement(
              "button",
              {
                type: "button",
                className: "button button-primary",
                onClick: submitHtml,
              },
              modeLabel
            )
          )
        )
      ),
      document.body
    );
  }

  function RawHtmlSource(props) {
    var editorState = props.editorState;
    var entityType = props.entityType;
    var entityKey = props.entityKey;
    var onComplete = props.onComplete;
    var onClose = props.onClose;
    var isEditing = Boolean(entityKey);

    if (!editorState || !entityType || !entityType.type || !onComplete) {
      if (onClose) {
        onClose();
      }
      return null;
    }

    function closeSource() {
      if (onClose) {
        onClose();
      } else {
        onComplete(editorState);
      }
    }

    function upsertRawHtml(rawHtml) {
      var html = rawHtml || "";
      if (!html.trim()) {
        closeSource();
        return;
      }

      if (isEditing) {
        var content = editorState.getCurrentContent();
        var updatedContent = content;

        if (typeof updatedContent.replaceEntityData === "function") {
          updatedContent = updatedContent.replaceEntityData(entityKey, {
            html: html,
          });
        } else if (typeof updatedContent.mergeEntityData === "function") {
          updatedContent = updatedContent.mergeEntityData(entityKey, {
            html: html,
          });
        }

        var editedState = EditorState.push(
          editorState,
          updatedContent,
          "apply-entity"
        );
        onComplete(editedState);
        return;
      }

      var currentContent = editorState.getCurrentContent();
      var contentWithEntity = currentContent.createEntity(
        entityType.type,
        "MUTABLE",
        {
          html: html,
        }
      );
      var createdEntityKey = contentWithEntity.getLastCreatedEntityKey();
      var withEntityState = EditorState.set(editorState, {
        currentContent: contentWithEntity,
      });
      var insertedState = AtomicBlockUtils.insertAtomicBlock(
        withEntityState,
        createdEntityKey,
        " "
      );
      onComplete(insertedState);
    }

    return React.createElement(RawHtmlModal, {
      initialHtml: getEntityHtml(editorState, entityKey),
      modeLabel: isEditing ? "Save" : "Insert block",
      onCancel: closeSource,
      onSave: upsertRawHtml,
    });
  }

  function RawHtmlBlock(props) {
    var blockProps = props.blockProps || {};
    var entity = blockProps.entity;
    var onEditEntity = blockProps.onEditEntity;
    var onRemoveEntity = blockProps.onRemoveEntity;
    var data = entity && entity.getData ? entity.getData() : {};
    var preview = truncateForPreview(data.html || "");

    function onEditClick(event) {
      event.preventDefault();
      event.stopPropagation();
      if (onEditEntity) {
        onEditEntity(event);
      }
    }

    function onDeleteClick(event) {
      event.preventDefault();
      event.stopPropagation();
      if (onRemoveEntity) {
        onRemoveEntity(event);
      }
    }

    return React.createElement(
      "div",
      {
        className: "Draftail-block--raw-html",
        style: {
          border: "1px solid #b1b4b6",
          borderRadius: "3px",
          padding: "0.75rem",
        },
      },
      React.createElement(
        "strong",
        {
          className: "Draftail-block__title",
        },
        "Raw HTML"
      ),
      preview
        ? React.createElement(
            "code",
            {
              className: "Draftail-block__code",
              style: { display: "block", marginTop: "0.25rem", whiteSpace: "normal" },
            },
            preview
          )
        : null,
      React.createElement(
        "div",
        {
          style: { display: "flex", gap: "0.5rem", marginTop: "0.6rem" },
        },
        onEditEntity
          ? React.createElement(
              "button",
              {
                type: "button",
                className: "button button-small",
                onClick: onEditClick,
              },
              "Edit HTML"
            )
          : null,
        onRemoveEntity
          ? React.createElement(
              "button",
              {
                type: "button",
                className: "button button-small button-secondary no",
                onClick: onDeleteClick,
              },
              "Delete"
            )
          : null
      )
    );
  }

  window.draftail.registerPlugin(
    {
      type: "RAW_HTML",
      source: RawHtmlSource,
      block: RawHtmlBlock,
    },
    "entityTypes"
  );
})();
