(() => {
  "use strict";

  const messagesEl = document.getElementById("messages");
  const emptyStateEl = document.getElementById("empty-state");
  const appShellEl = document.getElementById("app-shell");
  const historySidebarEl = document.getElementById("history-sidebar");
  const conversationListEl = document.getElementById("conversation-list");
  const historyStatusEl = document.getElementById("history-status");
  const newChatButtonEl = document.getElementById("new-chat-button");
  const historyToggleEl = document.getElementById("history-toggle");
  const historyBackdropEl = document.getElementById("history-backdrop");
  const formEl = document.getElementById("chat-form");
  const inputEl = document.getElementById("query-input");
  const fileInputEls = document.querySelectorAll(".upload-input");
  const uploadStatusEl = document.getElementById("upload-status");
  const sendButtonEl = formEl.querySelector("button[type='submit']");
  let activeConversationId = null;

  function escapeHtml(value) {
    return value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  // Recognizes the KIO1 workflow-plan shape so it can be rendered as step
  // cards instead of raw JSON; anything else falls back to highlightJson.
  function isWorkflowPlan(parsed) {
    return Boolean(
      parsed &&
        typeof parsed === "object" &&
        typeof parsed.workflow_id === "string" &&
        typeof parsed.execution_mode === "string" &&
        Array.isArray(parsed.steps)
    );
  }

  function renderPlanStep(step, index) {
    const stepId = escapeHtml(String(step?.step_id ?? index + 1));
    const agentId = escapeHtml(String(step?.agent_id ?? "?"));
    const task = escapeHtml(String(step?.task ?? ""));
    const capability =
      step && step.capability
        ? `<span class="step-capability">${escapeHtml(String(step.capability))}</span>`
        : "";

    return `
      <li class="plan-step">
        <span class="step-index">${index + 1}</span>
        <div class="step-body">
          <div class="step-meta">
            <span class="step-agent">${agentId}</span>
            ${capability}
            <span class="step-id">${stepId}</span>
          </div>
          <p class="step-task">${task}</p>
        </div>
      </li>`;
  }

  function renderWorkflowPlan(plan) {
    const stepsHtml = plan.steps.map(renderPlanStep).join("");
    const explanation =
      typeof plan.explanation === "string" && plan.explanation
        ? `<p class="plan-explanation">${escapeHtml(plan.explanation)}</p>`
        : "";

    return `
      <div class="plan-card">
        ${explanation}
        <div class="plan-header">
          <span class="plan-id">${escapeHtml(plan.workflow_id)}</span>
          <span class="plan-mode">${escapeHtml(plan.execution_mode)}</span>
        </div>
        <ol class="plan-steps">${stepsHtml}</ol>
      </div>
    `;
  }

  function renderStructuredValue(value, depth = 0) {
    if (depth > 6) {
      return '<p class="data-empty">Additional nested details hidden</p>';
    }

    if (Array.isArray(value)) {
      if (value.length === 0) {
        return '<p class="data-empty">No items</p>';
      }
      return `<ul class="data-list">${value
        .map((item) => `<li>${renderStructuredValue(item, depth + 1)}</li>`)
        .join("")}</ul>`;
    }

    if (value && typeof value === "object") {
      const fields = Object.entries(value);
      if (fields.length === 0) {
        return '<p class="data-empty">No details</p>';
      }
      return `<dl class="data-fields">${fields
        .map(([key, fieldValue]) => {
          const label = escapeHtml(key.replace(/[_-]+/g, " "));
          return `<div class="data-field"><dt>${label}</dt><dd>${renderStructuredValue(
            fieldValue,
            depth + 1,
          )}</dd></div>`;
        })
        .join("")}</dl>`;
    }

    if (typeof value === "string") {
      return `<p class="data-string">${escapeHtml(value)}</p>`;
    }

    const valueClass = value === null ? "null" : typeof value;
    return `<span class="data-primitive data-${valueClass}">${escapeHtml(
      String(value),
    )}</span>`;
  }

  function renderGenericJson(parsed) {
    return `
      <div class="structured-response">
        ${renderStructuredValue(parsed)}
      </div>`;
  }

  function renderFormattedResponse(formattedJson) {
    let parsed;
    try {
      parsed = JSON.parse(formattedJson);
    } catch {
      return `<p class="response-text">${escapeHtml(formattedJson)}</p>`;
    }

    if (isWorkflowPlan(parsed)) {
      return renderWorkflowPlan(parsed);
    }
    return renderGenericJson(parsed);
  }

  function appendMessage(role, { text, html, isError = false } = {}) {
    emptyStateEl.hidden = true;

    const wrapper = document.createElement("div");
    wrapper.className = `message message-${role}`;

    const author = document.createElement("span");
    author.className = "message-author";
    author.textContent = role === "user" ? "You" : "KIO1";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (html !== undefined) {
      bubble.classList.add("bubble-json");
      if (isError) bubble.classList.add("bubble-error");
      bubble.innerHTML = html;
    } else {
      if (isError) bubble.classList.add("bubble-error");
      bubble.textContent = text ?? "";
    }

    wrapper.append(author, bubble);
    messagesEl.appendChild(wrapper);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return wrapper;
  }

  function clearMessages() {
    messagesEl.querySelectorAll(".message").forEach((message) => message.remove());
    emptyStateEl.hidden = false;
  }

  function renderConversationMessages(messages) {
    clearMessages();
    messages.forEach((message) => {
      if (!message || typeof message.content !== "string") return;
      if (message.role === "user") {
        appendMessage("user", { text: message.content });
      } else if (message.role === "assistant") {
        appendMessage("assistant", {
          html: renderFormattedResponse(message.content),
        });
      }
    });
  }

  function setHistoryStatus(message, isError = false) {
    historyStatusEl.textContent = message;
    historyStatusEl.classList.toggle("error", Boolean(isError));
  }

  function setInterfaceBusy(isBusy) {
    inputEl.disabled = isBusy;
    sendButtonEl.disabled = isBusy;
    messagesEl.setAttribute("aria-busy", String(isBusy));
    historyToggleEl.disabled = isBusy;
    historySidebarEl.querySelectorAll("button").forEach((button) => {
      button.disabled = isBusy;
    });
  }

  async function fetchJson(url, options = {}) {
    const response = await fetch(url, options);
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error(`Unexpected response (HTTP ${response.status}).`);
    }
    if (!response.ok) {
      throw new Error(data.error ?? `Request failed (HTTP ${response.status}).`);
    }
    return data;
  }

  function formatConversationDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  function renderConversationList(conversations) {
    conversationListEl.replaceChildren();
    conversations.forEach((conversation) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "conversation-item";
      button.disabled = inputEl.disabled;
      button.classList.toggle(
        "active",
        conversation.id === activeConversationId,
      );
      if (conversation.id === activeConversationId) {
        button.setAttribute("aria-current", "page");
      }

      const title = document.createElement("span");
      title.className = "conversation-title";
      title.textContent = conversation.title;
      const date = document.createElement("span");
      date.className = "conversation-date";
      date.textContent = formatConversationDate(conversation.updated_at);
      button.append(title, date);
      button.addEventListener("click", () => {
        activateConversation(conversation.id);
      });
      conversationListEl.appendChild(button);
    });
  }

  async function refreshConversationList() {
    const data = await fetchJson("/api/conversations");
    activeConversationId = data.active_id;
    renderConversationList(data.conversations);
  }

  function closeHistoryDrawer() {
    appShellEl.classList.remove("history-open");
    historyToggleEl.setAttribute("aria-expanded", "false");
    historyBackdropEl.hidden = true;
  }

  async function activateConversation(conversationId) {
    setInterfaceBusy(true);
    setHistoryStatus("");
    try {
      const data = await fetchJson(
        `/api/conversations/${encodeURIComponent(conversationId)}/activate`,
        { method: "POST" },
      );
      activeConversationId = data.id;
      renderConversationMessages(data.messages);
      closeHistoryDrawer();
      await refreshConversationList();
    } catch (error) {
      setHistoryStatus(error.message, true);
    } finally {
      setInterfaceBusy(false);
      inputEl.focus();
    }
  }

  async function initializeConversationHistory() {
    setInterfaceBusy(true);
    try {
      await refreshConversationList();
      if (activeConversationId) {
        const data = await fetchJson(
          `/api/conversations/${encodeURIComponent(activeConversationId)}/activate`,
          { method: "POST" },
        );
        renderConversationMessages(data.messages);
      }
    } catch (error) {
      setHistoryStatus(error.message, true);
    } finally {
      setInterfaceBusy(false);
    }
  }

  async function createConversation() {
    setInterfaceBusy(true);
    setHistoryStatus("");
    try {
      const data = await fetchJson("/api/conversations", { method: "POST" });
      activeConversationId = data.id;
      renderConversationMessages(data.messages);
      closeHistoryDrawer();
      await refreshConversationList();
    } catch (error) {
      setHistoryStatus(error.message, true);
    } finally {
      setInterfaceBusy(false);
      inputEl.focus();
    }
  }

  function resizeInput() {
    inputEl.style.height = "auto";
    inputEl.style.height = `${Math.min(inputEl.scrollHeight, 128)}px`;
    inputEl.style.overflowY = inputEl.scrollHeight > 128 ? "auto" : "hidden";
  }

  function setUploadStatus(message, isError) {
    uploadStatusEl.textContent = message;
    uploadStatusEl.classList.toggle("error", Boolean(isError));
  }

  async function sendQuery(query) {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query }),
    });

    let data;
    try {
      data = await response.json();
    } catch {
      data = { error: `Unexpected response (HTTP ${response.status}).` };
    }

    if (data.formatted) {
      return { html: renderFormattedResponse(data.formatted) };
    }

    if (data.raw) {
      return {
        text: data.error ?? "Could not format the model response.",
        isError: true,
      };
    }

    return {
      text: data.error ?? `Request failed (HTTP ${response.status}).`,
      isError: true,
    };
  }

  formEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    const query = inputEl.value.trim();
    if (!query) return;

    appendMessage("user", { text: query });
    inputEl.value = "";
    resizeInput();
    setInterfaceBusy(true);
    const pendingMessage = appendMessage("assistant", { text: "Thinking..." });
    pendingMessage.classList.add("message-pending");

    try {
      const response = await sendQuery(query);
      pendingMessage.remove();
      appendMessage("assistant", response);
      try {
        await refreshConversationList();
      } catch (error) {
        setHistoryStatus(error.message, true);
      }
    } catch {
      pendingMessage.remove();
      appendMessage("assistant", {
        text: "Network error: could not reach the orchestrator.",
        isError: true,
      });
    } finally {
      setInterfaceBusy(false);
      inputEl.focus();
    }
  });

  inputEl.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      formEl.requestSubmit();
    }
  });

  inputEl.addEventListener("input", resizeInput);

  newChatButtonEl.addEventListener("click", createConversation);
  historyToggleEl.addEventListener("click", () => {
    const isOpen = appShellEl.classList.toggle("history-open");
    historyToggleEl.setAttribute("aria-expanded", String(isOpen));
    historyBackdropEl.hidden = !isOpen;
  });
  historyBackdropEl.addEventListener("click", closeHistoryDrawer);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeHistoryDrawer();
  });

  async function uploadFile(file, category, fileInputEl) {
    const body = new FormData();
    body.append("file", file);
    body.append("category", category);

    setUploadStatus(`Uploading ${file.name}...`, false);

    try {
      let response;
      try {
        response = await fetch("/api/upload", { method: "POST", body });
      } catch {
        setUploadStatus("Network error: could not reach the orchestrator.", true);
        return;
      }

      let data;
      try {
        data = await response.json();
      } catch {
        data = { error: `Upload failed (HTTP ${response.status}).` };
      }

      if (!response.ok || data.error) {
        setUploadStatus(data.error ?? "Upload failed.", true);
      } else {
        setUploadStatus(`Saved as assets/${data.filename}`, false);
      }
    } finally {
      fileInputEl.value = "";
    }
  }

  fileInputEls.forEach((fileInputEl) => {
    fileInputEl.addEventListener("change", () => {
      const file = fileInputEl.files[0];
      if (!file) return;
      uploadFile(file, fileInputEl.dataset.category, fileInputEl);
    });
  });

  initializeConversationHistory();
})();
