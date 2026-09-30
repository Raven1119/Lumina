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
  var memoryListEl = document.getElementById("memory-list");
  var memoryRefreshEl = document.getElementById("memory-refresh");
  var luminaStateEl = document.getElementById("lumina-state");
  var helperListEl = document.getElementById("helper-list");
  var chatBusy = false;
  var pendingChats = 0;
  var mindThinking = false;
  var helpersRunning = false;
  var statePollTimer = null;
  var statePollMilliseconds = 5000;
  var latestHistoryRequest = null;
  var dreamBusy = false;
  var dreamAvailable = false;
  var compactionPollTimer = null;
  var chatRequestGeneration = 0;
  var activeCompactionPollGeneration = null;
  var historyLoading = false;
  var historyHasMore = true;
  var historyNextBefore = null;
  var motionButton = document.getElementById("motion-toggle");
  var Motion = window.LuminaMotion;
  var motionAllowed = true;

  function syncMotionButton() {
    var reduced = Motion.state.reduced;
    motionButton.disabled = reduced;
    motionButton.setAttribute("aria-pressed", String(Motion.canMove()));
    motionButton.textContent = reduced ? "系统减少动效" :
      (Motion.canMove() ? "动效开启" : "动效关闭");
  }

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
    sendButton.disabled = dreamBusy;
    chatInput.disabled = dreamBusy;
    dreamButton.disabled = chatBusy || dreamBusy || !dreamAvailable;
  }

  function setDreamResult(text) {
    dreamResultEl.textContent = text || "";
  }

  function updateMemoryStatus(payload) {
    var lumina = payload && payload.lumina;
    mindThinking = Boolean(lumina && lumina.thinking);
    var helpers = lumina && Array.isArray(lumina.helpers) ? lumina.helpers : [];
    helpersRunning = helpers.some(function (item) {
      return item.status === "进行中" || item.status === "在等答复";
    });
    helperListEl.replaceChildren();
    helpers.forEach(function (item) {
      if (!item || typeof item.id !== "string" || typeof item.goal !== "string" ||
          typeof item.status !== "string") return;
      var line = document.createElement("li");
      line.textContent = item.id + "｜" + item.goal + "｜" + item.status;
      helperListEl.appendChild(line);
    });
    if (payload && Number.isInteger(payload.frontend_poll_interval_s)) {
      statePollMilliseconds = payload.frontend_poll_interval_s * 1000;
    }
    luminaStateEl.textContent = lumina && Array.isArray(lumina.states)
      ? lumina.states.join(" · ") + (lumina.focus ? " · " + lumina.focus : "")
      : "状态暂不可用";
    var dream = payload && payload.dream;
    if (!dream || typeof dream !== "object") {
      dreamAvailable = false;
      memoryStatusEl.textContent = "\u957f\u671f\u8bb0\u5fc6\uff1a\u72b6\u6001\u4e0d\u53ef\u7528";
      recallStatusEl.textContent = "Recall\uff1a\u72b6\u6001\u4e0d\u53ef\u7528";
      updateControls();
      return;
    }
    var count = Number.isInteger(dream.unintegrated_cold_turns)
      ? dream.unintegrated_cold_turns
      : 0;
    var memory = payload.memory || {};
    memoryStatusEl.textContent = "Memory：" + (memory.memory_count || 0) +
      " 条记忆 · " + (memory.pattern_count || 0) +
      " 个模式 · " + count + " 条 Cold 待处理" +
      (dream.auto_paused ? " · 自动 Dream 已暂停" : "");
    recallStatusEl.textContent = payload.recall_enabled
      ? "Recall\uff1a\u5df2\u5f00\u542f"
      : "Recall\uff1a\u672a\u5f00\u542f";
    dreamAvailable = dream.available === true;
    updateControls();
  }

  function createMessageBubble(text, role, phase, responseType) {
    var bubble = document.createElement("div");
    bubble.className = "chat-message is-" + role;
    bubble.dataset.role = role;
    bubble.dataset.content = text;

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
    return bubble;
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
    Motion.enter(bubble);
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
        if (!bubbleForTurn(turn.turn_id)) {
          var bubble = optimisticBubble(turn) || createMessageBubble(turn.content, turn.role);
          bubble.dataset.turnId = turn.turn_id;
          fragment.appendChild(bubble);
        }
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
        setNotice("历史记录暂不可用，仍可继续对话。");
      }
    }).then(function () {
      historyLoading = false;
    });
  }

  function bubbleForTurn(id) {
    return Array.prototype.find.call(chatLog.children, function (bubble) {
      return bubble.dataset.turnId === id;
    });
  }

  function optimisticBubble(turn) {
    return Array.prototype.find.call(chatLog.children, function (item) {
      return !item.dataset.turnId && item.dataset.role === turn.role &&
        item.dataset.content === turn.content;
    });
  }

  function refreshLatestHistory() {
    if (latestHistoryRequest) return latestHistoryRequest;
    latestHistoryRequest = fetch("/api/history?limit=100").then(function (response) {
      if (!response.ok) throw new Error("history_unavailable");
      return response.json();
    }).then(function (payload) {
      if (!isValidHistoryPayload(payload)) throw new Error("history_invalid");
      var nearEnd = chatLog.scrollHeight - chatLog.scrollTop - chatLog.clientHeight < 100;
      payload.turns.forEach(function (turn) {
        // Server order is authoritative when a new input overlaps unseen speech.
        var bubble = bubbleForTurn(turn.turn_id) || optimisticBubble(turn);
        if (!bubble) {
          bubble = createMessageBubble(turn.content, turn.role);
          chatLog.appendChild(bubble);
          if (turn.role === "assistant") Motion.enter(bubble);
        }
        bubble.dataset.turnId = turn.turn_id;
        chatLog.appendChild(bubble);
      });
      Array.prototype.filter.call(chatLog.children, function (bubble) {
        return bubble.dataset.role && !bubble.dataset.turnId;
      }).forEach(function (bubble) { chatLog.appendChild(bubble); });
      if (nearEnd) chatLog.scrollTop = chatLog.scrollHeight;
      return true;
    }).catch(function () { return false; }).then(function (success) {
      latestHistoryRequest = null;
      return success;
    });
    return latestHistoryRequest;
  }

  function replyVisibleAfter(userBubble, text) {
    var bubble = userBubble.nextElementSibling;
    while (bubble) {
      if (bubble.dataset.role === "assistant" && bubble.dataset.content === text) return true;
      bubble = bubble.nextElementSibling;
    }
    return false;
  }

  function scheduleStatePoll() {
    clearTimeout(statePollTimer);
    if (document.hidden) return;
    statePollTimer = setTimeout(function () {
      var wasThinking = mindThinking;
      checkBackend().then(function () {
      if (mindThinking || wasThinking || chatBusy || helpersRunning) return refreshLatestHistory();
      }).then(scheduleStatePoll);
    }, statePollMilliseconds);
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
    return fetch("/api/status").then(function (response) {
      if (!response.ok) {
        setBackendStatus("连接不可用", "down");
        updateMemoryStatus(null);
        return;
      }
      return response.json().then(function (payload) {
        if (payload && payload.status === "ok") {
          setBackendStatus("连接正常", "ok");
          updateMemoryStatus(payload);
        } else {
          setBackendStatus("连接不可用", "down");
          updateMemoryStatus(null);
        }
      });
    }).catch(function () {
      setBackendStatus("连接不可用", "down");
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
    if (document.hidden) return;
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
      typeof payload.status === "string" &&
      Number.isInteger(payload.window_turns) &&
      Number.isInteger(payload.patterns);
  }

  function handleDream() {
    dreamBusy = true;
    setDreamResult("Dream \u8fd0\u884c\u4e2d");
    updateControls();

    runDream().then(function (payload) {
      if (!validDreamResult(payload)) {
        setDreamResult("Dream \u8fd4\u56de\u4e86\u65e0\u6cd5\u8bc6\u522b\u7684\u7ed3\u679c");
      } else if (payload.status === "no_window") {
        setDreamResult("\u6ca1\u6709\u5f85\u5904\u7406\u7684\u957f\u671f\u8bb0\u5fc6");
      } else {
        setDreamResult("Dream：" + payload.status + " · " +
          payload.window_turns + " 条对话 · " + payload.patterns + " 个模式");
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

  function refreshMemory() {
    fetch("/api/memory").then(function (response) {
      if (!response.ok) throw new Error("memory_unavailable");
      return response.json();
    }).then(function (payload) {
      var lines = (payload.memories || []).map(function (item) {
        return item.id + " | " + item.time_label + " | π=" +
          item.pi.toFixed(2) + " | sources=" + item.source_count +
          (item.pattern ? " | pattern" : "") + "\n" + item.text;
      });
      memoryListEl.textContent = lines.join("\n\n") || "暂无记忆";
    }).catch(function () { memoryListEl.textContent = "记忆暂不可用"; });
  }

  function handleSubmit(event) {
    event.preventDefault();
    var raw = chatInput.value;
    var message = raw == null ? "" : raw.trim();
    if (!message) {
      return;
    }

    setNotice("");
    var userBubble = appendUserMessage(message);
    chatInput.value = "";
    pendingChats += 1;
    chatBusy = true;
    updateControls();
    setNotice("正在发送…");
    var generation = ++chatRequestGeneration;
    startCompactionPolling(generation);

    sendMessage(message).then(function (payload) {
      stopCompactionPolling(generation);
      if (!isValidChatPayload(payload)) {
        setNotice("响应格式无法识别。");
        return;
      }
      return refreshLatestHistory().then(function () {
        if (!replyVisibleAfter(userBubble, payload.response.text)) {
          appendAssistantMessage(payload.response.text, payload.phase, payload.response.type);
        }
      }).then(function () {
      var compacted = compactionNotice(payload.compaction);
      if (compacted) {
        setNotice(compacted);
        if (payload.compaction.status === "completed") {
          return checkBackend();
        }
      } else if (payload.response.type === "fallback") {
        setNotice("当前显示降级响应。");
      } else {
        setNotice("");
      }
      });
    }).catch(function (err) {
      stopCompactionPolling(generation);
      if (err && err.kind === "http") {
        setNotice("请求失败（HTTP " + err.status + "）。");
      } else if (err && err.kind === "parse") {
        setNotice("响应格式无法识别。");
      } else {
        setNotice("连接不可用，请确认服务已启动。");
      }
    }).then(function () {
      stopCompactionPolling(generation);
      pendingChats = Math.max(0, pendingChats - 1);
      chatBusy = pendingChats > 0;
      updateControls();
      chatInput.focus();
    });
  }

  chatForm.addEventListener("submit", handleSubmit);
  motionButton.addEventListener("click", function () {
    motionAllowed = !motionAllowed;
    Motion.setEnabled(motionAllowed);
    Motion.setAmbient(false);
  });
  window.addEventListener("pageshow", function () {
    Motion.setEnabled(motionAllowed);
    Motion.setAmbient(false);
  });
  document.addEventListener("motion:state", syncMotionButton);
  chatInput.addEventListener("focus", function () { Motion.setWriting(true); });
  chatInput.addEventListener("blur", function () { Motion.setWriting(false); });
  // Enter sends; Shift+Enter inserts a newline. Ignore Enter while an IME
  // composition (e.g. Chinese input) is in progress so it never mis-sends.
  chatInput.addEventListener("keydown", function (event) {
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    if (event.isComposing || event.keyCode === 229) {
      return;
    }
    handleSubmit(event);
  });
  dreamButton.addEventListener("click", handleDream);
  memoryRefreshEl.addEventListener("click", refreshMemory);
  syncMotionButton();
  updateControls();
  loadHistoryPage(true).then(function () {
    chatLog.addEventListener("scroll", handleHistoryScroll);
  });
  checkBackend().then(scheduleStatePoll);
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      clearTimeout(statePollTimer);
    } else {
      checkBackend().then(refreshLatestHistory).then(scheduleStatePoll);
    }
  });
  window.addEventListener("pagehide", function () {
    clearTimeout(statePollTimer);
    stopCompactionPolling();
  });
})();
