(function () {
  "use strict";

  var chatLog = document.getElementById("chat-log");
  var chatForm = document.getElementById("chat-form");
  var chatInput = document.getElementById("chat-input");
  var sendButton = document.getElementById("send-button");
  var noticeEl = document.getElementById("chat-notice");
  var backendStatusEl = document.getElementById("backend-status");
  var memoryStatusEl = document.getElementById("memory-status");
  var recallStatusEl = document.getElementById("recall-status");
  var dreamButton = document.getElementById("dream-button");
  var dreamResultEl = document.getElementById("dream-result");
  var chatBusy = false;
  var dreamBusy = false;
  var dreamAvailable = false;
  var compactionPollTimer = null;
  var chatRequestGeneration = 0;
  var activeCompactionPollGeneration = null;
  var historyLoading = false;
  var historyHasMore = true;
  var historyNextBefore = null;

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

  function updateControls() {
    var writing = chatBusy || dreamBusy;
    sendButton.disabled = writing;
    chatInput.disabled = writing;
    dreamButton.disabled = writing || !dreamAvailable;
  }

  function setDreamResult(text) {
    dreamResultEl.textContent = text || "";
  }

  function updateMemoryStatus(payload) {
    var dream = payload && payload.dream;
    if (!dream || typeof dream !== "object") {
      dreamAvailable = false;
      memoryStatusEl.textContent = "\u957f\u671f\u8bb0\u5fc6\uff1a\u72b6\u6001\u4e0d\u53ef\u7528";
      recallStatusEl.textContent = "Recall\uff1a\u72b6\u6001\u4e0d\u53ef\u7528";
      updateControls();
      return;
    }
    var count = Number.isInteger(dream.pending_segments)
      ? dream.pending_segments
      : 0;
    memoryStatusEl.textContent = dream.pending_truncated
      ? "\u957f\u671f\u8bb0\u5fc6\uff1a\u81f3\u5c11 " + count + " \u4e2a\u5f85\u5904\u7406\u7247\u6bb5"
      : "\u957f\u671f\u8bb0\u5fc6\uff1a" + count + " \u4e2a\u5f85\u5904\u7406\u7247\u6bb5";
    recallStatusEl.textContent = payload.recall_enabled
      ? "Recall\uff1a\u5df2\u5f00\u542f"
      : "Recall\uff1a\u672a\u5f00\u542f";
    dreamAvailable = dream.available === true;
    updateControls();
  }

  function createMessageBubble(text, role, phase, responseType) {
    var bubble = document.createElement("div");
    bubble.className = "chat-message is-" + role;

    if (role === "assistant") {
      var body = document.createElement("div");
      body.textContent = text;
      bubble.appendChild(body);

      if (phase && responseType) {
        var badge = document.createElement("span");
        badge.className = "chat-badge " + badgeKind(responseType);
        badge.textContent = phase + " / " + responseType;
        bubble.appendChild(badge);
      }
    } else {
      bubble.textContent = text;
    }
    return bubble;
  }

  function appendUserMessage(text) {
    var bubble = createMessageBubble(text, "user");
    chatLog.appendChild(bubble);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function badgeKind(responseType) {
    if (responseType === "model") return "is-model";
    if (responseType === "fallback") return "is-fallback";
    return "is-mock";
  }

  function appendAssistantMessage(text, phase, responseType) {
    var bubble = createMessageBubble(
      text,
      "assistant",
      phase,
      responseType
    );
    chatLog.appendChild(bubble);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  function isValidHistoryPayload(payload) {
    if (
      !payload ||
      typeof payload !== "object" ||
      !Array.isArray(payload.turns) ||
      typeof payload.has_more !== "boolean" ||
      !(
        payload.next_before === null ||
        typeof payload.next_before === "string"
      )
    ) {
      return false;
    }
    return payload.turns.every(function (turn) {
      return turn &&
        typeof turn === "object" &&
        typeof turn.turn_id === "string" &&
        (turn.role === "user" || turn.role === "assistant") &&
        typeof turn.content === "string" &&
        typeof turn.timestamp === "string";
    });
  }

  function historyUrl() {
    var url = "/api/history?limit=40";
    if (historyNextBefore) {
      url += "&before=" + encodeURIComponent(historyNextBefore);
    }
    return url;
  }

  function loadHistoryPage(initial) {
    if (historyLoading || (!initial && !historyHasMore)) {
      return Promise.resolve();
    }
    historyLoading = true;
    var oldHeight = chatLog.scrollHeight;
    var oldTop = chatLog.scrollTop;

    return fetch(historyUrl()).then(function (response) {
      if (!response.ok) {
        throw new Error("history_http_error");
      }
      return response.json();
    }).then(function (payload) {
      if (!isValidHistoryPayload(payload)) {
        throw new Error("history_parse_error");
      }
      var fragment = document.createDocumentFragment();
      payload.turns.forEach(function (turn) {
        fragment.appendChild(
          createMessageBubble(turn.content, turn.role)
        );
      });
      chatLog.insertBefore(fragment, chatLog.firstChild);
      historyHasMore = payload.has_more;
      historyNextBefore = payload.next_before;
      if (initial) {
        chatLog.scrollTop = chatLog.scrollHeight;
      } else {
        chatLog.scrollTop =
          oldTop + (chatLog.scrollHeight - oldHeight);
      }
    }).catch(function () {
      historyHasMore = false;
      historyNextBefore = null;
      if (!chatBusy) {
        setNotice("History unavailable. Chat is still available.");
      }
    }).then(function () {
      historyLoading = false;
    });
  }

  function handleHistoryScroll() {
    if (
      chatLog.scrollTop <= 125 &&
      historyHasMore &&
      !historyLoading
    ) {
      loadHistoryPage(false);
    }
  }

  function isValidChatPayload(payload) {
    return (
      payload &&
      typeof payload === "object" &&
      payload.response &&
      typeof payload.response === "object" &&
      typeof payload.response.text === "string" &&
      typeof payload.phase === "string" &&
      typeof payload.response.type === "string" &&
      payload.compaction &&
      typeof payload.compaction === "object" &&
      ["not_needed", "completed", "failed"].indexOf(
        payload.compaction.status
      ) !== -1 &&
      Number.isInteger(payload.compaction.archived_turns) &&
      typeof payload.compaction.summary_updated === "boolean"
    );
  }

  function compactionNotice(compaction) {
    if (compaction.status === "completed") {
      return (
        "Hot Draft \u5df2\u538b\u7f29\uff1a" +
        compaction.archived_turns +
        " \u6761\u8f83\u65e9\u5bf9\u8bdd\u5df2\u5f52\u6863\uff0c\u6eda\u52a8\u6458\u8981\u5df2\u66f4\u65b0\u3002"
      );
    }
    if (compaction.status === "failed") {
      return "Hot Draft \u538b\u7f29\u672a\u5b8c\u6210\uff0c\u539f\u59cb\u8bb0\u5f55\u5df2\u4fdd\u7559\uff0c\u7cfb\u7edf\u5c06\u5728\u540e\u7eed\u5bf9\u8bdd\u4e2d\u91cd\u8bd5\u3002";
    }
    return "";
  }

  function sendMessage(message) {
    var clientTimezone;
    try {
      clientTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch (_error) {
      clientTimezone = undefined;
    }
    var payload = { message: message };
    if (typeof clientTimezone === "string" && clientTimezone) {
      payload.client_timezone = clientTimezone;
    }
    return fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
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
    return fetch("/api/status").then(function (response) {
      if (!response.ok) {
        setBackendStatus("Backend unavailable", "down");
        updateMemoryStatus(null);
        return;
      }
      return response.json().then(function (payload) {
        if (payload && payload.status === "ok") {
          setBackendStatus("Backend ready", "ok");
          updateMemoryStatus(payload);
        } else {
          setBackendStatus("Backend unavailable", "down");
          updateMemoryStatus(null);
        }
      });
    }).catch(function () {
      setBackendStatus("Backend unavailable", "down");
      updateMemoryStatus(null);
    });
  }

  function stopCompactionPolling(generation) {
    if (
      generation !== undefined &&
      activeCompactionPollGeneration !== generation
    ) {
      return;
    }
    activeCompactionPollGeneration = null;
    if (compactionPollTimer !== null) {
      clearInterval(compactionPollTimer);
      compactionPollTimer = null;
    }
  }

  function pollCompactionStatus(generation) {
    if (
      activeCompactionPollGeneration !== generation ||
      !chatBusy
    ) {
      return;
    }
    fetch("/api/status").then(function (response) {
      if (!response.ok) {
        return null;
      }
      return response.json().catch(function () {
        return null;
      });
    }).then(function (payload) {
      if (
        activeCompactionPollGeneration !== generation ||
        !chatBusy
      ) {
        return;
      }
      if (
        payload &&
        payload.compaction &&
        payload.compaction.running === true
      ) {
        setNotice(
          "Hot Draft \u6b63\u5728\u538b\u7f29\uff0c\u8bf7\u7a0d\u5019\u2026\u2026"
        );
      }
    }).catch(function () {
      // A temporary status failure must not affect the in-flight chat request.
    });
  }

  function startCompactionPolling(generation) {
    stopCompactionPolling();
    activeCompactionPollGeneration = generation;
    compactionPollTimer = setInterval(function () {
      pollCompactionStatus(generation);
    }, 500);
  }

  function runDream() {
    return fetch("/api/dream/run", { method: "POST" }).then(function (response) {
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

  function validDreamResult(payload) {
    return payload &&
      Number.isInteger(payload.attempted) &&
      Number.isInteger(payload.ingested) &&
      Number.isInteger(payload.consumed) &&
      Number.isInteger(payload.skipped) &&
      Number.isInteger(payload.failed);
  }

  function handleDream() {
    dreamBusy = true;
    setDreamResult("Dream \u8fd0\u884c\u4e2d");
    updateControls();

    runDream().then(function (payload) {
      if (!validDreamResult(payload)) {
        setDreamResult("Dream \u8fd4\u56de\u4e86\u65e0\u6cd5\u8bc6\u522b\u7684\u7ed3\u679c");
      } else if (payload.attempted === 0) {
        setDreamResult("\u6ca1\u6709\u5f85\u5904\u7406\u7684\u957f\u671f\u8bb0\u5fc6");
      } else {
        setDreamResult(
          "\u672c\u6b21\uff1a\u5c1d\u8bd5 " + payload.attempted +
          " / \u5199\u5165 " + payload.ingested +
          " / \u6d88\u8d39 " + payload.consumed +
          " / \u8df3\u8fc7 " + payload.skipped +
          " / \u5931\u8d25 " + payload.failed
        );
      }
    }).catch(function (err) {
      if (err && err.kind === "http" && err.status === 409) {
        setDreamResult("\u7cfb\u7edf\u6b63\u5728\u5904\u7406\u53e6\u4e00\u9879\u5199\u5165\u64cd\u4f5c");
      } else if (err && err.kind === "http" && err.status === 503) {
        setDreamResult("Dream \u5f53\u524d\u4e0d\u53ef\u7528");
      } else {
        setDreamResult("Dream \u8fd0\u884c\u5931\u8d25");
      }
    }).then(function () {
      return checkBackend();
    }).then(function () {
      dreamBusy = false;
      updateControls();
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
    chatBusy = true;
    updateControls();
    setNotice("Sending...");
    var generation = ++chatRequestGeneration;
    startCompactionPolling(generation);

    sendMessage(message).then(function (payload) {
      stopCompactionPolling(generation);
      if (!isValidChatPayload(payload)) {
        setNotice("Unexpected response format.");
        return;
      }
      appendAssistantMessage(
        payload.response.text,
        payload.phase,
        payload.response.type
      );
      var compacted = compactionNotice(payload.compaction);
      if (compacted) {
        setNotice(compacted);
        if (payload.compaction.status === "completed") {
          return checkBackend();
        }
      } else if (payload.response.type === "fallback") {
        setNotice("Fallback response shown.");
      } else {
        setNotice("");
      }
    }).catch(function (err) {
      stopCompactionPolling(generation);
      if (err && err.kind === "http") {
        setNotice("Request failed (HTTP " + err.status + ").");
      } else if (err && err.kind === "parse") {
        setNotice("Unexpected response format.");
      } else {
        setNotice("Backend unavailable. Is uvicorn running?");
      }
    }).then(function () {
      stopCompactionPolling(generation);
      chatBusy = false;
      updateControls();
      chatInput.focus();
    });
  }

  chatForm.addEventListener("submit", handleSubmit);
  dreamButton.addEventListener("click", handleDream);
  updateControls();
  loadHistoryPage(true).then(function () {
    chatLog.addEventListener("scroll", handleHistoryScroll);
  });
  checkBackend();
})();
