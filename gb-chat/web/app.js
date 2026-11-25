/* GB Chat web client. Plain JavaScript, no build step, no dependencies. */
(() => {
  "use strict";

  // ---------------------------------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------------------------------
  const $ = (sel) => document.querySelector(sel);
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const svg = (paths, extra = "") => {
    const span = document.createElement("span");
    span.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true" ${extra}>${paths}</svg>`;
    return span.firstChild;
  };
  const ICON = {
    check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    cross: '<path d="M7 7l10 10M17 7L7 17"/>',
    chevron: '<path d="M6 9l6 6 6-6"/>',
    open: '<path d="M14 5h5v5M19 5l-8 8M18 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h4"/>',
    download: '<path d="M12 4v11M7 10.5l5 5 5-5M5 20h14"/>',
    x: '<path d="M7 7l10 10M17 7L7 17"/>',
  };

  const api = async (method, path, body) => {
    const opts = { method, headers: { "X-GB-Chat": "1" } };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
    return data;
  };

  const formatSize = (bytes) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  const formatWhen = (seconds) => {
    if (!seconds) return "";
    const d = new Date(seconds * 1000);
    const now = new Date();
    const day = 24 * 3600 * 1000;
    const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    if (d.getTime() >= startToday) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (d.getTime() >= startToday - day) return "Yesterday";
    if (d.getTime() >= startToday - 6 * day) return d.toLocaleDateString([], { weekday: "long" });
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
  };

  let toastTimer = null;
  const toast = (message) => {
    const box = $("#toast");
    box.textContent = message;
    box.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { box.hidden = true; }, 3500);
  };

  // ---------------------------------------------------------------------------------------
  // Markdown (safe subset: everything is escaped first, then known syntax is turned into tags)
  // ---------------------------------------------------------------------------------------
  const escapeHtml = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

  const safeUrl = (url) => {
    const u = url.trim();
    if (/^https?:\/\//i.test(u) || /^\/files\//.test(u) || /^mailto:/i.test(u)) return u;
    return null;
  };

  const inline = (text) => {
    const codes = [];
    let s = text.replace(/`([^`\n]+)`/g, (_, c) => {
      codes.push(c);
      return `\u0000${codes.length - 1}\u0000`;
    });
    s = escapeHtml(s);
    s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
      const href = safeUrl(url.replace(/&amp;/g, "&"));
      if (!href) return label;
      return `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, (m, pre, url) =>
      `${pre}<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`);
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
    s = s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${escapeHtml(codes[Number(i)])}</code>`);
    return s;
  };

  const splitRow = (line) => {
    let row = line.trim();
    if (row.startsWith("|")) row = row.slice(1);
    if (row.endsWith("|")) row = row.slice(0, -1);
    return row.split("|").map((c) => c.trim());
  };

  const renderMarkdown = (src) => {
    const lines = src.replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let i = 0;
    const isTableSep = (l) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
    while (i < lines.length) {
      const line = lines[i];
      const fence = line.match(/^\s*```\s*([\w+-]*)\s*$/);
      if (fence) {
        const buf = [];
        i += 1;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { buf.push(lines[i]); i += 1; }
        i += 1;
        out.push(`<pre><code>${escapeHtml(buf.join("\n"))}</code></pre>`);
        continue;
      }
      if (!line.trim()) { i += 1; continue; }
      const heading = line.match(/^(#{1,4})\s+(.*)$/);
      if (heading) {
        const level = heading[1].length;
        out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
        i += 1;
        continue;
      }
      if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) { out.push("<hr>"); i += 1; continue; }
      if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        const head = splitRow(line);
        i += 2;
        const rows = [];
        while (i < lines.length && lines[i].includes("|") && lines[i].trim()) { rows.push(splitRow(lines[i])); i += 1; }
        const th = head.map((c) => `<th>${inline(c)}</th>`).join("");
        const tb = rows.map((r) => `<tr>${head.map((_, k) => `<td>${inline(r[k] || "")}</td>`).join("")}</tr>`).join("");
        out.push(`<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table></div>`);
        continue;
      }
      if (/^\s*>/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) { buf.push(lines[i].replace(/^\s*>\s?/, "")); i += 1; }
        out.push(`<blockquote>${renderMarkdown(buf.join("\n"))}</blockquote>`);
        continue;
      }
      const listMatch = line.match(/^(\s*)([-*+]|\d+[.)])\s+/);
      if (listMatch) {
        const ordered = /\d/.test(listMatch[2]);
        const items = [];
        while (i < lines.length) {
          const m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
          if (m && (/\d/.test(m[2]) === ordered || m[1].length >= 2)) {
            if (m[1].length >= 2 && items.length) {
              items[items.length - 1].sub.push(lines[i].slice(Math.min(m[1].length, 4)));
            } else {
              items.push({ text: m[3], sub: [] });
            }
            i += 1;
          } else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) {
            items[items.length - 1].sub.push(lines[i].trim());
            i += 1;
          } else {
            break;
          }
        }
        const tag = ordered ? "ol" : "ul";
        const lis = items.map((it) => {
          const sub = it.sub.length ? renderMarkdown(it.sub.join("\n")) : "";
          return `<li>${inline(it.text)}${sub}</li>`;
        }).join("");
        out.push(`<${tag}>${lis}</${tag}>`);
        continue;
      }
      const para = [];
      while (i < lines.length && lines[i].trim() && !/^(#{1,4})\s|^\s*```|^\s*>|^(\s*)([-*+]|\d+[.)])\s+/.test(lines[i])
        && !(lines[i].includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1]))) {
        para.push(lines[i]);
        i += 1;
      }
      if (para.length) out.push(`<p>${para.map(inline).join("<br>")}</p>`);
      else i += 1;
    }
    return out.join("");
  };

  // ---------------------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------------------
  const state = {
    chats: [],
    chatId: null,
    view: "chat",
    running: null, // { runId, controller }
    attachments: [], // { id, name, status: uploading|ready|error }
    me: { name: "" },
  };

  // ---------------------------------------------------------------------------------------
  // Sidebar and views
  // ---------------------------------------------------------------------------------------
  const renderChatList = () => {
    const list = $("#chat-list");
    const filter = $("#chat-filter").value.trim().toLowerCase();
    list.replaceChildren();
    const chats = state.chats.filter((c) => !filter || (c.title || c.preview || "").toLowerCase().includes(filter));
    if (!chats.length) {
      list.append(el("li", "chat-empty", filter ? "No chats match." : "Your chats will appear here."));
      return;
    }
    for (const chat of chats) {
      const li = el("li");
      const btn = el("button", "chat-item" + (chat.id === state.chatId && state.view === "chat" ? " active" : ""));
      btn.type = "button";
      btn.append(el("span", "chat-item-title", chat.title || chat.preview || "Untitled chat"));
      btn.append(el("span", "chat-item-when", formatWhen(chat.updated)));
      btn.addEventListener("click", () => openChat(chat.id));
      li.append(btn);
      list.append(li);
    }
  };

  const refreshChats = async () => {
    try {
      const data = await api("GET", "/api/chats");
      state.chats = data.chats || [];
      renderChatList();
    } catch (err) {
      toast(err.message);
    }
  };

  const setView = (view) => {
    state.view = view;
    $("#view-chat").hidden = view !== "chat";
    $("#view-files").hidden = view !== "files";
    $("#view-skills").hidden = view !== "skills";
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    const chat = state.chats.find((c) => c.id === state.chatId);
    $("#view-title").textContent = view === "files" ? "My files" : view === "skills" ? "What I can do"
      : (chat && (chat.title || chat.preview)) || "New chat";
    $("#rename-chat").hidden = $("#delete-chat").hidden = !(view === "chat" && state.chatId);
    renderChatList();
    closeSidebar();
    if (view === "files") loadFiles();
    if (view === "skills") loadSkills();
  };

  const openSidebar = () => { $("#app").classList.add("sidebar-open"); $("#scrim").hidden = false; };
  const closeSidebar = () => { $("#app").classList.remove("sidebar-open"); $("#scrim").hidden = true; };

  // ---------------------------------------------------------------------------------------
  // Messages
  // ---------------------------------------------------------------------------------------
  const thread = () => $("#thread");
  const scrollToEnd = () => { const t = thread(); t.scrollTop = t.scrollHeight; };
  const nearBottom = () => { const t = thread(); return t.scrollHeight - t.scrollTop - t.clientHeight < 120; };

  const showWelcome = (show) => { $("#welcome").hidden = !show; };

  const clearThread = () => {
    thread().querySelectorAll(".msg").forEach((n) => n.remove());
  };

  const addUserMessage = (text, attachments = []) => {
    const row = el("div", "msg msg-user");
    const bubble = el("div", "bubble", text);
    if (attachments.length) {
      const files = el("div", "bubble-files");
      attachments.forEach((n) => files.append(el("span", "bubble-file", n)));
      bubble.append(files);
    }
    row.append(bubble);
    thread().append(row);
    return row;
  };

  const fileCard = (file) => {
    const card = el("div", "file-card");
    const ext = (file.name.split(".").pop() || "").toUpperCase().slice(0, 4);
    const badge = el("span", "file-badge", ext || "FILE");
    badge.dataset.kind = file.kind || "";
    const meta = el("div", "file-meta");
    meta.append(el("span", "file-name", file.name));
    meta.append(el("span", "file-sub", `${file.kind || "File"} · ${formatSize(file.size || 0)}`));
    const actions = el("div", "file-actions");
    const open = el("a");
    open.href = file.url; open.target = "_blank"; open.rel = "noopener"; open.title = "Open"; open.setAttribute("aria-label", `Open ${file.name}`);
    open.append(svg(ICON.open));
    const dl = el("a");
    dl.href = `${file.url}?download=1`; dl.title = "Download"; dl.setAttribute("aria-label", `Download ${file.name}`);
    dl.append(svg(ICON.download));
    actions.append(open, dl);
    card.append(badge, meta, actions);
    return card;
  };

  class AssistantMessage {
    constructor() {
      this.row = el("div", "msg msg-assistant");
      const avatar = el("img", "avatar-bot");
      avatar.src = "/assets/gb-mark.svg"; avatar.alt = "";
      this.body = el("div", "msg-body");
      this.notes = el("div");
      this.content = el("div", "md");
      this.typing = el("div", "typing");
      this.typing.append(el("span"), el("span"), el("span"));
      this.files = el("div", "file-row");
      this.files.hidden = true;
      this.extra = el("div");
      this.body.append(this.notes, this.content, this.typing, this.files, this.extra);
      this.row.append(avatar, this.body);
      this.text = "";
      this.steps = new Map();
      this.activity = null;
      this.renderPending = false;
      this.fileNames = new Set();
    }

    mount() { thread().append(this.row); return this; }

    setText(text) {
      this.text = text;
      if (this.renderPending) return;
      this.renderPending = true;
      requestAnimationFrame(() => {
        const stick = nearBottom();
        this.renderPending = false;
        this.content.innerHTML = renderMarkdown(this.text);
        if (this.text) this.typing.hidden = true;
        if (stick) scrollToEnd();
      });
    }

    ensureActivity() {
      if (this.activity) return;
      this.activity = el("details", "activity");
      const summary = el("summary");
      this.activitySpinner = el("span", "spinner");
      this.activityLabel = el("span", null, "Working…");
      summary.append(this.activitySpinner, this.activityLabel, svg(ICON.chevron, 'class="chev"'));
      this.stepList = el("ol");
      this.activity.append(summary, this.stepList);
      this.body.insertBefore(this.activity, this.notes);
    }

    step(ev) {
      this.ensureActivity();
      let item = this.steps.get(ev.id);
      if (!item) {
        item = el("li", "step");
        item.append(el("span", "step-icon"), el("span", "step-label"));
        this.steps.set(ev.id, item);
        this.stepList.append(item);
      }
      item.className = `step ${ev.status}`;
      item.querySelector(".step-label").textContent = ev.label;
      const icon = item.querySelector(".step-icon");
      icon.replaceChildren(ev.status === "running" ? el("span", "spinner")
        : svg(ev.status === "failed" ? ICON.cross : ICON.check));
      this.updateActivitySummary(false);
    }

    updateActivitySummary(finished) {
      if (!this.activity) return;
      const n = this.steps.size;
      const running = [...this.steps.values()].find((s) => s.classList.contains("running"));
      if (finished || !running) {
        this.activitySpinner.hidden = true;
        this.activityLabel.textContent = `${n} step${n === 1 ? "" : "s"} completed`;
      } else {
        this.activitySpinner.hidden = false;
        this.activityLabel.textContent = running.querySelector(".step-label").textContent + "…";
      }
    }

    note(text) { this.notes.append(el("p", "note", text)); }

    file(file) {
      if (this.fileNames.has(file.name)) return;
      this.fileNames.add(file.name);
      this.files.hidden = false;
      this.files.append(fileCard(file));
      if (nearBottom()) scrollToEnd();
    }

    error(message) { this.extra.append(el("div", "error-box", message)); this.typing.hidden = true; }

    approval(ev) {
      const box = el("div", "approval");
      box.append(el("p", "approval-title", ev.title || "GB Chat needs your permission"));
      if (ev.detail) box.append(el("pre", "approval-detail", ev.detail));
      const actions = el("div", "approval-actions");
      const labels = { once: "Allow once", session: "Allow for this chat", always: "Always allow", deny: "Deny" };
      const choices = (ev.choices || ["once", "deny"]).filter((c) => labels[c] && c !== "always");
      for (const choice of choices) {
        const btn = el("button", "btn" + (choice === "once" ? " btn-primary" : ""), labels[choice]);
        btn.type = "button";
        btn.addEventListener("click", async () => {
          actions.querySelectorAll("button").forEach((b) => { b.disabled = true; });
          try {
            await api("POST", `/api/runs/${encodeURIComponent(ev.run_id)}/approval`,
              { choice, request_id: ev.request_id, generation: ev.generation });
            actions.replaceWith(el("p", "approval-result", choice === "deny" ? "You denied this." : "You allowed this."));
          } catch (err) {
            toast(err.message);
            actions.querySelectorAll("button").forEach((b) => { b.disabled = false; });
          }
        });
        actions.append(btn);
      }
      box.append(actions);
      this.extra.append(box);
      this.typing.hidden = true;
      scrollToEnd();
    }

    finish() {
      this.typing.hidden = true;
      this.updateActivitySummary(true);
      if (!this.text && !this.files.childElementCount && !this.extra.childElementCount) {
        this.content.innerHTML = renderMarkdown("_No answer this time. Try asking again._");
      }
    }
  }

  const renderHistory = (messages) => {
    clearThread();
    showWelcome(!messages.length);
    for (const m of messages) {
      if (m.role === "user") {
        addUserMessage(m.text);
      } else {
        const msg = new AssistantMessage().mount();
        msg.typing.hidden = true;
        msg.text = m.text;
        msg.content.innerHTML = renderMarkdown(m.text);
        for (const match of m.text.matchAll(/\b([\w().\- ]+\.(?:docx|pptx|xlsx|csv|pdf|html))\b/gi)) {
          const name = match[1].trim();
          const known = state.outputs && state.outputs.find((f) => f.name === name);
          if (known) msg.file(known);
        }
      }
    }
    requestAnimationFrame(scrollToEnd);
  };

  const openChat = async (chatId) => {
    if (state.running) { toast("Wait for the current answer to finish, or stop it."); return; }
    state.chatId = chatId;
    setView("chat");
    clearThread();
    showWelcome(false);
    try {
      const [msgs, filesData] = await Promise.all([
        api("GET", `/api/chats/${encodeURIComponent(chatId)}/messages`),
        api("GET", "/api/files").catch(() => ({ files: [] })),
      ]);
      state.outputs = filesData.files || [];
      renderHistory(msgs.messages || []);
    } catch (err) {
      toast(err.message);
    }
  };

  const newChat = () => {
    if (state.running) { toast("Wait for the current answer to finish, or stop it."); return; }
    state.chatId = null;
    clearThread();
    showWelcome(true);
    setView("chat");
    $("#input").focus();
  };

  // ---------------------------------------------------------------------------------------
  // Sending a turn (server-sent events over fetch)
  // ---------------------------------------------------------------------------------------
  const setRunning = (running) => {
    state.running = running;
    $("#send").hidden = !!running;
    $("#stop").hidden = !running;
    updateSendEnabled();
  };

  const updateSendEnabled = () => {
    const hasText = $("#input").value.trim().length > 0;
    const uploading = state.attachments.some((a) => a.status === "uploading");
    const ready = state.attachments.some((a) => a.status === "ready");
    $("#send").disabled = !!state.running || uploading || (!hasText && !ready);
  };

  async function* readEvents(response) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trimStart()).join("\n");
        if (data) {
          try { yield JSON.parse(data); } catch { /* ignore malformed frames */ }
        }
      }
    }
  }

  const send = async (text) => {
    text = text.trim();
    const ready = state.attachments.filter((a) => a.status === "ready").map((a) => a.name);
    if ((!text && !ready.length) || state.running) return;

    showWelcome(false);
    $("#input").value = "";
    autosize();
    state.attachments = [];
    renderAttachments();

    addUserMessage(text, ready);
    const msg = new AssistantMessage().mount();
    scrollToEnd();
    const controller = new AbortController();
    setRunning({ runId: null, controller });

    try {
      if (!state.chatId) {
        const title = (text || ready.join(", ")).replace(/\s+/g, " ").slice(0, 60);
        const created = await api("POST", "/api/chats", { title });
        state.chatId = created.chat.id;
        state.chats.unshift({ ...created.chat, title: created.chat.title || title, updated: Date.now() / 1000 });
        setView("chat");
      }
      const response = await fetch(`/api/chats/${encodeURIComponent(state.chatId)}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-GB-Chat": "1" },
        body: JSON.stringify({ text, attachments: ready }),
        signal: controller.signal,
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `Request failed (${response.status})`);
      }
      for await (const ev of readEvents(response)) {
        switch (ev.type) {
          case "run": state.running.runId = ev.run_id; break;
          case "text": msg.setText(msg.text + ev.delta); break;
          case "note": msg.note(ev.text); break;
          case "step": msg.step(ev); break;
          case "thinking": break;
          case "approval": msg.approval(ev); break;
          case "final": if (ev.content && ev.content !== msg.text) msg.setText(ev.content); break;
          case "file": msg.file(ev); break;
          case "error": msg.error(ev.message); break;
          case "end":
            if (ev.status === "cancelled") msg.note("Stopped.");
            break;
          default: break;
        }
      }
    } catch (err) {
      if (err.name === "AbortError") msg.note("Stopped.");
      else msg.error(err.message);
    } finally {
      msg.finish();
      setRunning(null);
      refreshChats();
    }
  };

  const stop = async () => {
    const run = state.running;
    if (!run) return;
    if (run.runId) {
      try { await api("POST", `/api/runs/${encodeURIComponent(run.runId)}/stop`, {}); return; } catch { /* fall through */ }
    }
    run.controller.abort();
  };

  // ---------------------------------------------------------------------------------------
  // Attachments
  // ---------------------------------------------------------------------------------------
  const renderAttachments = () => {
    const box = $("#attachments");
    box.replaceChildren();
    box.hidden = !state.attachments.length;
    for (const a of state.attachments) {
      const chip = el("span", "chip" + (a.status === "uploading" ? " uploading" : ""));
      chip.append(el("span", null, a.status === "uploading" ? `Uploading ${a.name}…` : a.name));
      const remove = el("button");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${a.name}`);
      remove.append(svg(ICON.x));
      remove.addEventListener("click", () => {
        state.attachments = state.attachments.filter((x) => x !== a);
        renderAttachments();
      });
      chip.append(remove);
      box.append(chip);
    }
    updateSendEnabled();
  };

  const upload = async (file) => {
    const entry = { name: file.name, status: "uploading" };
    state.attachments.push(entry);
    renderAttachments();
    try {
      const res = await fetch(`/api/uploads?name=${encodeURIComponent(file.name)}`, {
        method: "POST", headers: { "X-GB-Chat": "1", "Content-Type": "application/octet-stream" }, body: file,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || "Upload failed");
      entry.name = data.file.name;
      entry.status = "ready";
    } catch (err) {
      state.attachments = state.attachments.filter((x) => x !== entry);
      toast(`${file.name}: ${err.message}`);
    }
    renderAttachments();
  };

  // ---------------------------------------------------------------------------------------
  // Files and skills
  // ---------------------------------------------------------------------------------------
  const loadFiles = async () => {
    const grid = $("#file-grid");
    grid.replaceChildren(el("div", "empty-state", "Loading…"));
    try {
      const data = await api("GET", "/api/files");
      state.outputs = data.files || [];
      grid.replaceChildren();
      if (!state.outputs.length) {
        grid.append(el("div", "empty-state", "No files yet. Ask GB Chat for a report, deck or spreadsheet."));
        return;
      }
      state.outputs.forEach((f) => grid.append(fileCard(f)));
    } catch (err) {
      grid.replaceChildren(el("div", "empty-state", err.message));
    }
  };

  const loadSkills = async () => {
    const grid = $("#skill-grid");
    grid.replaceChildren(el("div", "empty-state", "Loading…"));
    try {
      const data = await api("GET", "/api/skills");
      grid.replaceChildren();
      for (const s of data.skills || []) {
        const card = el("div", "skill-card");
        card.append(el("div", "skill-cat", s.category), el("h3", "skill-title", s.title), el("p", "skill-desc", s.description));
        grid.append(card);
      }
      if (!grid.childElementCount) grid.append(el("div", "empty-state", "No skills are available right now."));
    } catch (err) {
      grid.replaceChildren(el("div", "empty-state", err.message));
    }
  };

  // ---------------------------------------------------------------------------------------
  // Chat actions
  // ---------------------------------------------------------------------------------------
  const renameChat = async () => {
    const chat = state.chats.find((c) => c.id === state.chatId);
    const title = $("#view-title");
    title.contentEditable = "true";
    title.focus();
    document.getSelection().selectAllChildren(title);
    const done = async (save) => {
      title.contentEditable = "false";
      title.removeEventListener("keydown", onKey);
      title.removeEventListener("blur", onBlur);
      const value = title.textContent.trim();
      if (!save || !value || (chat && value === chat.title)) { setView("chat"); return; }
      try {
        await api("PATCH", `/api/chats/${encodeURIComponent(state.chatId)}`, { title: value });
        if (chat) chat.title = value;
      } catch (err) { toast(err.message); }
      setView("chat");
    };
    const onKey = (e) => {
      if (e.key === "Enter") { e.preventDefault(); done(true); }
      if (e.key === "Escape") { e.preventDefault(); done(false); }
    };
    const onBlur = () => done(true);
    title.addEventListener("keydown", onKey);
    title.addEventListener("blur", onBlur);
  };

  const deleteChat = async () => {
    if (!state.chatId || !window.confirm("Delete this chat? This can't be undone.")) return;
    try {
      await api("DELETE", `/api/chats/${encodeURIComponent(state.chatId)}`);
      state.chats = state.chats.filter((c) => c.id !== state.chatId);
      newChat();
    } catch (err) { toast(err.message); }
  };

  // ---------------------------------------------------------------------------------------
  // Wiring
  // ---------------------------------------------------------------------------------------
  const autosize = () => {
    const input = $("#input");
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 220)}px`;
    updateSendEnabled();
  };

  const greet = () => {
    const h = new Date().getHours();
    const part = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    const first = (state.me.name || "").split(/[\s@.]/)[0];
    $("#greeting").textContent = first ? `${part}, ${first[0].toUpperCase()}${first.slice(1)}` : part;
  };

  const init = async () => {
    $("#composer").addEventListener("submit", (e) => { e.preventDefault(); send($("#input").value); });
    $("#input").addEventListener("input", autosize);
    $("#input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send($("#input").value); }
    });
    $("#stop").addEventListener("click", stop);
    $("#new-chat").addEventListener("click", newChat);
    $("#open-sidebar").addEventListener("click", openSidebar);
    $("#close-sidebar").addEventListener("click", closeSidebar);
    $("#scrim").addEventListener("click", closeSidebar);
    $("#chat-filter").addEventListener("input", renderChatList);
    $("#rename-chat").addEventListener("click", renameChat);
    $("#delete-chat").addEventListener("click", deleteChat);
    document.querySelectorAll(".nav-item").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
    document.querySelectorAll(".suggestion").forEach((b) => b.addEventListener("click", () => {
      $("#input").value = b.dataset.prompt;
      autosize();
      $("#input").focus();
    }));
    $("#file-input").addEventListener("change", (e) => {
      [...e.target.files].forEach(upload);
      e.target.value = "";
    });

    let dragDepth = 0;
    window.addEventListener("dragenter", (e) => {
      if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
      dragDepth += 1; $("#drop-overlay").hidden = false;
    });
    window.addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $("#drop-overlay").hidden = true; });
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => {
      e.preventDefault(); dragDepth = 0; $("#drop-overlay").hidden = true;
      [...(e.dataTransfer?.files || [])].forEach(upload);
    });

    autosize();
    greet();
    try {
      const [me, status] = await Promise.all([api("GET", "/api/me"), api("GET", "/api/status")]);
      state.me = me;
      if (me.name) {
        $("#user-name").textContent = me.name;
        const initials = me.name.split(/[\s@.]/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");
        $("#user-avatar").textContent = initials || "GB";
      }
      $("#engine-pill").hidden = status.engine !== "mock";
      if (status.engine !== "mock" && !status.healthy) toast("GB Chat's engine is offline. Ask IT to check it.");
      greet();
    } catch (err) {
      toast(err.message);
    }
    refreshChats();
    $("#input").focus();
  };

  // Exposed for tests only.
  window.GBChat = { renderMarkdown };
  document.addEventListener("DOMContentLoaded", init);
})();
