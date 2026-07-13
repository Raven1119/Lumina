(function () {
  "use strict";

  var chatLog = document.getElementById("chat-log");
  var chatForm = document.getElementById("chat-form");
  var chatInput = document.getElementById("chat-input");
  var sendButton = document.getElementById("send-button");
  var noticeEl = document.getElementById("chat-notice");
  var backendStatusEl = document.getElementById("backend-status");

  function setNotice(text) {
    noticeEl.textContent = text || "";
  }

  function setBackendStatus(label, kind) {
    backendStatusEl.textContent = label;
    backendStatusEl.classList.remove("is-ok", "is-down");
    if (kind === "ok") {
      backendStatusEl.classList.add("is-ok");
    } else if (kind === "down") {
      backendStatusEl.classList.add("is-down");
    }
  }

  function appendUserMessage(text) {
    var bubble = document.createElement("div");
    bubble.className = "chat-message is-user";
    bubble.textContent = text;
    chatLog.appendChild(bubble);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function badgeKind(responseType) {
    if (responseType === "model") return "is-model";
    if (responseType === "fallback") return "is-fallback";
    return "is-mock";
  }

  function appendAssistantMessage(text, phase, responseType) {
    var bubble = document.createElement("div");
    bubble.className = "chat-message is-assistant";

    var body = document.createElement("div");
    body.textContent = text;
    bubble.appendChild(body);

    if (phase && responseType) {
      var badge = document.createElement("span");
      badge.className = "chat-badge " + badgeKind(responseType);
      badge.textContent = phase + " / " + responseType;
      bubble.appendChild(badge);
    }

    chatLog.appendChild(bubble);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function isValidChatPayload(payload) {
    return (
      payload &&
      typeof payload === "object" &&
      payload.response &&
      typeof payload.response === "object" &&
      typeof payload.response.text === "string" &&
      typeof payload.phase === "string" &&
      typeof payload.response.type === "string"
    );
  }

  function sendMessage(message) {
    return fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: message })
    }).then(function (response) {
      if (!response.ok) {
        var err = new Error("http_error");
        err.kind = "http";
        err.status = response.status;
        throw err;
      }
      return response.json().catch(function () {
        var err = new Error("parse_error");
        err.kind = "parse";
        throw err;
      });
    });
  }

  function checkBackend() {
    setBackendStatus("Checking backend...");
    fetch("/api/status").then(function (response) {
      if (!response.ok) {
        setBackendStatus("Backend unavailable", "down");
        return;
      }
      return response.json().then(function (payload) {
        if (payload && payload.status === "ok") {
          setBackendStatus("Backend ready", "ok");
        } else {
          setBackendStatus("Backend unavailable", "down");
        }
      });
    }).catch(function () {
      setBackendStatus("Backend unavailable", "down");
    });
  }

  function handleSubmit(event) {
    event.preventDefault();
    var raw = chatInput.value;
    var message = raw == null ? "" : raw.trim();
    if (!message) {
      return;
    }

    setNotice("");
    appendUserMessage(message);
    chatInput.value = "";
    sendButton.disabled = true;
    chatInput.disabled = true;
    setNotice("Sending...");

    sendMessage(message).then(function (payload) {
      if (!isValidChatPayload(payload)) {
        setNotice("Unexpected response format.");
        return;
      }
      appendAssistantMessage(
        payload.response.text,
        payload.phase,
        payload.response.type
      );
      if (payload.response.type === "fallback") {
        setNotice("Fallback response shown.");
      } else {
        setNotice("");
      }
    }).catch(function (err) {
      if (err && err.kind === "http") {
        setNotice("Request failed (HTTP " + err.status + ").");
      } else if (err && err.kind === "parse") {
        setNotice("Unexpected response format.");
      } else {
        setNotice("Backend unavailable. Is uvicorn running?");
      }
    }).then(function () {
      sendButton.disabled = false;
      chatInput.disabled = false;
      chatInput.focus();
    });
  }

  chatForm.addEventListener("submit", handleSubmit);
  checkBackend();
})();
