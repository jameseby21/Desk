/* Undo (DESK-0284). After every write, a toast for ten seconds with Undo; Z undoes while it shows (outside a text field).
   window.deskToast({text, batch})      show the toast for a write; batch is the write's batch id ('' = no Undo button)
   window.deskConfirmCount({count, noun, action}) -> Promise<boolean>
                                        the dry-run count before a bulk action: "This changes 12 tickets." Cancel or Go ahead.
   Where the toast comes from: a redirect's `undo` and `msg` query parameters (read on load, then taken out of the address), or the
   X-Desk-Undo headers of an in-place post (every XMLHttpRequest and fetch answer is looked at). Undo posts /undo/batch/<id> with the
   X-Desk-Undo-Request header; its answer brings a toast of its own whose button is Redo (an undo of the undo).
   Forms with class undo-form (the /undo page) post in place the same way; a change that was changed since asks "Undo anyway". */
(function () {
  "use strict";
  var SHOW_MS = 10000, current = null;

  (function css() {  // the stylesheet sits beside this script, at the same build version
    var me = document.currentScript || document.querySelector('script[src*="undo.js"]');
    var href = me ? me.src.replace("undo.js", "undo.css") : "/static/undo.css";
    var l = document.createElement("link"); l.rel = "stylesheet"; l.href = href; document.head.appendChild(l);
  })();

  function mk(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }

  function post(url, body) {
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest(); x.open("POST", url);
      x.setRequestHeader("Accept", "application/json"); x.setRequestHeader("X-Desk-Undo-Request", "1"); x._deskUndoOwn = true;
      x.onload = function () { var j = {}; try { j = JSON.parse(x.responseText); } catch (e) { j = {}; } resolve({ ok: x.status >= 200 && x.status < 300, status: x.status, j: j }); };
      x.onerror = function () { reject(new Error("network")); };
      x.send(body || new FormData());
    });
  }

  function refresh() {
    if (window.DeskProgress && DeskProgress.softRefresh) return DeskProgress.softRefresh().catch(function () { location.reload(); });
    location.reload();
    return Promise.resolve();
  }

  function close() {
    if (!current) return;
    clearTimeout(current.timer); current.el.remove(); current = null;
  }

  function undoBatch(batch, redo, force) {
    var t = current; if (!t || t.busy) return;
    t.busy = true; t.btn.disabled = true; t.text.textContent = redo ? "Redoing…" : "Undoing…";
    t.bar.classList.add("busy"); clearTimeout(t.timer);
    var fd = new FormData(); if (force) fd.set("force", "1");
    post("/undo/batch/" + encodeURIComponent(batch), fd).then(function (x) {
      if (x.ok) {
        refresh().then(function () { show({ text: x.j.message || "Undone.", batch: x.j.batch || "", redo: !redo }); });
        return;
      }
      fail(x.j.error || ("Could not undo (HTTP " + x.status + "). Nothing was changed."), x.j.conflict ? function () { undoBatch(batch, redo, true); } : null);
    }, function () { fail("Could not reach Desk. Is drew-ui still running? Nothing was changed.", null); });
  }

  function fail(msg, anyway) {
    show({ text: msg, batch: "", error: true });
    if (anyway && current) {
      var b = mk("button", "btn small", "Undo anyway"); b.type = "button";
      b.addEventListener("click", function () { current.busy = false; current.btn = b; anyway(); });
      current.actions.insertBefore(b, current.actions.firstChild);
    }
  }

  /* The toast. One at a time: a new one replaces the old. */
  function show(o) {
    close();
    o = o || {};
    var el = mk("div", "undo-toast" + (o.error ? " err" : "")); el.setAttribute("role", o.error ? "alert" : "status"); el.setAttribute("aria-live", "polite");
    var text = mk("span", "undo-text", o.text || "Saved."), actions = mk("span", "undo-actions");
    var btn = mk("button", "btn small ball", o.redo ? "Redo" : "Undo"); btn.type = "button";
    if (o.batch) { btn.title = "Press Z"; actions.appendChild(btn); btn.addEventListener("click", function () { undoBatch(o.batch, !!o.redo); }); }
    var x = mk("button", "undo-x", "✕"); x.type = "button"; x.setAttribute("aria-label", "Close"); x.addEventListener("click", close); actions.appendChild(x);
    var bar = mk("span", "undo-bar"); bar.appendChild(mk("i"));
    if (o.batch) actions.insertBefore(mk("a", "undo-log", "Log"), x).href = "/undo";
    el.appendChild(text); el.appendChild(actions); el.appendChild(bar);
    document.body.appendChild(el);
    current = { el: el, text: text, btn: btn, bar: bar, actions: actions, batch: o.batch || "", redo: !!o.redo, busy: false };
    bar.firstChild.style.animationDuration = SHOW_MS + "ms";
    current.timer = setTimeout(close, o.error ? SHOW_MS * 2 : SHOW_MS);
    el.addEventListener("mouseenter", function () { if (current && current.el === el) { clearTimeout(current.timer); el.classList.add("held"); } });
    el.addEventListener("mouseleave", function () { if (current && current.el === el && !current.busy) { el.classList.remove("held"); current.timer = setTimeout(close, SHOW_MS / 2); } });
    return el;
  }
  window.deskToast = function (o) { return show(o); };

  /* Z (no Ctrl, Alt or Meta) undoes while the toast shows, unless you are typing. Esc closes it. */
  document.addEventListener("keydown", function (e) {
    if (!current || e.ctrlKey || e.metaKey || e.altKey) return;
    var a = document.activeElement;
    if (a && (/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) || a.isContentEditable)) return;
    if ((e.key === "z" || e.key === "Z") && current.batch && !current.busy) { e.preventDefault(); undoBatch(current.batch, current.redo); }
    else if (e.key === "Escape") close();
  });

  /* An in-place post that wrote something says so in its headers. */
  function fromHeaders(get) {
    var b = get("X-Desk-Undo"); if (!b) return;
    var m = get("X-Desk-Undo-Msg"); try { m = decodeURIComponent(m || ""); } catch (e) { m = ""; }
    setTimeout(function () { show({ text: m || "Saved.", batch: b }); }, 0);
  }
  var send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () {
    var x = this;
    if (!x._deskUndoOwn) x.addEventListener("load", function () { fromHeaders(function (h) { try { return x.getResponseHeader(h); } catch (e) { return null; } }); });
    return send.apply(this, arguments);
  };
  if (window.fetch) {
    var f = window.fetch;
    window.fetch = function () {
      return f.apply(this, arguments).then(function (r) { try { if (r.headers) fromHeaders(function (h) { return r.headers.get(h); }); } catch (e) { /* no headers */ } return r; });
    };
  }

  /* A redirect after a plain form post: ?undo=<batch>&msg=<sentence>. Shown once; the address is cleaned so a reload does not repeat it. */
  (function fromAddress() {
    var q = new URLSearchParams(location.search), b = q.get("undo"), m = q.get("msg");
    if (!b && !m) return;
    q.delete("undo"); q.delete("msg");
    try { history.replaceState(null, "", location.pathname + (q.toString() ? "?" + q.toString() : "") + location.hash); } catch (e) { /* old browser */ }
    show({ text: m || "Saved.", batch: b || "" });
  })();

  /* The /undo page's forms: post in place, then the page refreshes and the toast offers Redo. */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (!form.classList || !form.classList.contains("undo-form") || !window.FormData) return;
    e.preventDefault();
    var b = form.querySelector("button"); if (b.disabled) return;
    b.disabled = true; var label = b.textContent; b.textContent = "Undoing…";
    var DP = window.DeskProgress, p = DP ? DP.start(DP.slotFor(b), { steps: DP.stepsOf(form) || ["Sending", "Undoing", "Done"] }) : null;  // the bar where you clicked
    if (p) p.step(1);
    post(form.getAttribute("action"), new FormData(form)).then(function (x) {
      if (x.ok) { if (p) p.done(x.j.message || "Undone."); refresh().then(function () { show({ text: x.j.message || "Undone.", batch: x.j.batch || "", redo: true }); }); return; }
      if (p) p.fail(x.j.error || "Could not undo. Nothing was changed.");
      b.disabled = false; b.textContent = label;
      var t = form.querySelector("[name=token]"); if (t && x.j.token) t.value = x.j.token;
      fail(x.j.error || ("Could not undo (HTTP " + x.status + "). Nothing was changed."), x.j.conflict ? function () {
        var fd = new FormData(form); fd.set("force", "1");
        post(form.getAttribute("action"), fd).then(function (y) {
          if (y.ok) refresh().then(function () { show({ text: y.j.message || "Undone.", batch: y.j.batch || "", redo: true }); });
          else fail(y.j.error || "Could not undo. Nothing was changed.", null);
        });
      } : null);
    }, function () { b.disabled = false; b.textContent = label; fail("Could not reach Desk. Is drew-ui still running? Nothing was changed.", null); });
  }, true);

  /* The /undo page's Who filter applies at once. */
  document.addEventListener("change", function (e) { var s = e.target; if (s.hasAttribute && s.hasAttribute("data-autosubmit") && s.form) s.form.submit(); });

  /* The dry-run count before a bulk action (DESK-0285 calls it). Resolves true only when the person says go ahead.
     Also declarative: a form with data-confirm-count="12" (and data-confirm-noun, data-confirm-action) asks before it posts. */
  function confirmCount(o) {
    o = o || {};
    var n = Math.max(0, parseInt(o.count, 10) || 0), noun = o.noun || "ticket", nouns = n === 1 ? noun : (o.nouns || noun + "s");
    return new Promise(function (resolve) {
      var d = mk("dialog", "undo-confirm"); d.setAttribute("aria-label", "Check the count first");
      d.appendChild(mk("p", "undo-count", String(n)));
      d.appendChild(mk("p", "", (o.action || "This changes") + " " + n + " " + nouns + ". You can undo it afterwards."));
      var row = mk("div", "undo-actions"), no = mk("button", "btn small", "Cancel"), yes = mk("button", "btn small ball", "Go ahead (" + n + ")");
      no.type = yes.type = "button"; row.appendChild(no); row.appendChild(yes); d.appendChild(row);
      function done(v) { try { d.close(); } catch (e) { /* closed */ } d.remove(); resolve(v); }
      no.addEventListener("click", function () { done(false); }); yes.addEventListener("click", function () { done(true); });
      d.addEventListener("cancel", function (e) { e.preventDefault(); done(false); });
      document.body.appendChild(d);
      if (d.showModal) { d.showModal(); yes.focus(); } else { d.remove(); resolve(window.confirm((o.action || "This changes") + " " + n + " " + nouns + ".")); }
    });
  }
  window.deskConfirmCount = confirmCount;
  window.addEventListener("submit", function (e) {  // on window, so it runs before the in-place posting (progress.js) sees the form
    var form = e.target;
    if (!form.getAttribute || !form.hasAttribute("data-confirm-count") || form._deskConfirmed) return;
    e.preventDefault(); e.stopImmediatePropagation();
    confirmCount({ count: form.getAttribute("data-confirm-count"), noun: form.getAttribute("data-confirm-noun") || "ticket", action: form.getAttribute("data-confirm-action") || "" })
      .then(function (go) { if (!go) return; form._deskConfirmed = true; if (form.requestSubmit) form.requestSubmit(); else form.submit(); form._deskConfirmed = false; });
  }, true);
})();
