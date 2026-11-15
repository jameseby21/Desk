/* Search palette (DESK-0113). Ctrl K (or Cmd K, or / outside a field) opens it from any page; typing asks /search.json as you type,
   results are grouped by project with counts; arrows move, Enter opens the ticket (or the full results when nothing is picked), Esc closes.
   Without script the header box still submits to /search.
   DESK-0290: on a ticket page the palette also lists what can be done to that ticket (Approve, Send back, Snooze 1 week, Move to Later,
   Assign to ...). Each action posts the same form fields to the same route as the page's own button (GET /palette/actions/<ID> hands
   over the route, the fields and a fresh one-time token), so there is one code path. Empty, it lists the last tickets you opened on
   this device; a search also lists matching docs and intake files. */
(function () {
  "use strict";
  var box = document.getElementById("search-open");
  var dlg, input, list, status, runEl, items = [], at = -1, timer = null, seq = 0, opener = null, giving = false;
  var acts = [], actsFor = "", wordsFor = null, busy = false;
  var tm = /^\/ticket\/([A-Za-z][A-Za-z0-9]*-\d+)\/?$/.exec(location.pathname), here = tm ? tm[1].toUpperCase() : "";
  var HELP = "Type to search. Esc closes." + (here ? " Actions for " + here + " are listed first." : "");
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function mark(s) { return esc(s).replace(/\[/g, "<mark>").replace(/\]/g, "</mark>"); }
  function recent() { try { return JSON.parse(localStorage.getItem("desk:recent") || "[]") || []; } catch (e) { return []; } }
  function build() {
    if (dlg) return;
    dlg = document.createElement("div");
    dlg.id = "palette"; dlg.className = "palette"; dlg.setAttribute("role", "dialog"); dlg.setAttribute("aria-label", "Search tickets"); dlg.hidden = true;
    dlg.innerHTML = '<div class="pal-box"><input type="search" id="pal-input" autocomplete="off" aria-label="Search tickets" placeholder="Part of an id or title, then filters like status:review assignee:chaz">' +
      '<div id="pal-status" class="muted pal-status" role="status" aria-live="polite">' + esc(HELP) + '</div><div id="pal-run" class="pal-run"></div><div id="pal-list" class="pal-list" role="listbox"></div></div>';
    document.body.appendChild(dlg);
    input = dlg.querySelector("#pal-input"); list = dlg.querySelector("#pal-list"); status = dlg.querySelector("#pal-status"); runEl = dlg.querySelector("#pal-run");
    dlg.addEventListener("mousedown", function (e) { if (e.target === dlg) close(); });
    list.addEventListener("click", function (e) {
      var a = e.target.closest && e.target.closest("a.pal-item[data-act]");
      if (a) { e.preventDefault(); act(acts[+a.getAttribute("data-act")]); }
    });
    input.addEventListener("input", function () { if (wordsFor) return; clearTimeout(timer); timer = setTimeout(run, 90); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" && !wordsFor) { e.preventDefault(); pick(at + 1); }
      else if (e.key === "ArrowUp" && !wordsFor) { e.preventDefault(); pick(at - 1); }
      else if (e.key === "Enter") { e.preventDefault(); if (wordsFor) send(wordsFor, input.value); else go(e); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); if (wordsFor) back(); else close(); }  // handled once, here
    });
  }
  /* The open ticket's actions, fetched on every open: each carries a one-time token, and a token is spent by its post. */
  function loadActs() {
    if (!here) return Promise.resolve();
    return fetch("/palette/actions/" + encodeURIComponent(here), { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : { actions: [] }; })
      .then(function (d) { acts = d.actions || []; actsFor = d.id || here; }, function () { acts = []; });
  }
  function open(seed) {
    build(); if (!dlg.hidden) return;  // already open: a second open (focus then click on the box) changes nothing
    opener = document.activeElement; dlg.hidden = false; document.body.classList.add("pal-open");
    wordsFor = null; runEl.textContent = "";
    if (seed != null) input.value = seed;
    input.focus(); input.select();
    run();
    loadActs().then(function () { if (!dlg.hidden && !wordsFor) run(); });
  }
  /* Closing gives focus back to what had it. The header box opens the palette when it gets focus, so the hand-back must not count as
     a person focusing it: without `giving` the palette reopened at once and Esc seemed to do nothing (DESK-0206). */
  function close() {
    if (!dlg || dlg.hidden) return;
    dlg.hidden = true; document.body.classList.remove("pal-open"); wordsFor = null;
    if (opener && opener.focus && document.contains(opener)) { giving = true; try { opener.focus(); } finally { giving = false; } }
    opener = null;
  }
  function pick(n) {
    if (!items.length) return;
    at = (n + items.length) % items.length;
    items.forEach(function (a, i) { a.classList.toggle("sel", i === at); a.setAttribute("aria-selected", i === at ? "true" : "false"); });
    items[at].scrollIntoView({ block: "nearest" });
  }
  function go(e) {
    var it = at >= 0 && items[at];
    if (it && it.hasAttribute("data-act")) { act(acts[+it.getAttribute("data-act")]); return; }
    if (it) { if (e.ctrlKey || e.metaKey) window.open(it.href); else window.location.href = it.href; return; }
    if (input.value.trim()) window.location.href = "/search?q=" + encodeURIComponent(input.value);
  }
  /* An action's words match when every typed word is in its label or keywords: "snooze", "later", "assign chaz", "app". */
  function matching(q) {
    var words = q.toLowerCase().split(/\s+/).filter(Boolean);
    return acts.map(function (a, i) { return [a, i]; }).filter(function (p) { return words.every(function (w) { return p[0].keywords.indexOf(w) >= 0; }); });
  }
  function actHtml(pairs) {
    if (!pairs.length) return "";
    var h = '<div class="pal-group">On ' + esc(actsFor || here) + ' <span class="muted">' + pairs.length + '</span></div>';
    pairs.forEach(function (p) {
      h += '<a class="pal-item pal-act" role="option" href="#" data-act="' + p[1] + '"><span class="pal-title">' + esc(p[0].label) + '</span><span class="pal-hint muted">' + esc(p[0].hint) + '</span></a>';
    });
    return h;
  }
  function recentHtml() {
    var r = recent().filter(function (x) { return x && x.id && x.id !== here; }).slice(0, 10);
    if (!r.length) return "";
    var h = '<div class="pal-group">Recently viewed <span class="muted">' + r.length + '</span></div>';
    r.forEach(function (x) { h += '<a class="pal-item" role="option" href="/ticket/' + esc(x.id) + '"><span class="chip">' + esc(x.id) + '</span><span class="pal-title">' + esc(x.title || "") + '</span></a>'; });
    return h;
  }
  function show(html, none) {
    list.innerHTML = html || (none ? '<div class="muted pal-none">' + none + '</div>' : "");
    items = Array.prototype.slice.call(list.querySelectorAll("a.pal-item")); at = items.length ? 0 : -1; pick(at);
  }
  function run() {
    if (wordsFor) return;
    var q = input.value.trim(), me = ++seq;
    if (!q) { show(actHtml(acts.map(function (a, i) { return [a, i]; })) + recentHtml()); status.textContent = HELP; return; }
    var top = actHtml(matching(q));
    fetch("/search.json?q=" + encodeURIComponent(q), { headers: { Accept: "application/json" } }).then(function (r) { return r.json(); }).then(function (d) {
      if (me !== seq || wordsFor) return;
      var html = top;
      d.groups.forEach(function (g) {
        html += '<div class="pal-group">' + esc(g.project) + ' <span class="muted">' + g.count + '</span></div>';
        g.hits.forEach(function (h) {
          html += '<a class="pal-item" role="option" href="/ticket/' + esc(h.id) + '"><span class="chip">' + esc(h.id) + '</span> <span class="chip">' + esc(h.status_label || h.status) + '</span> <span class="chip">' + esc(h.priority) + '</span>' +
            '<span class="pal-title">' + esc(h.title) + (h.fuzzy ? ' <span class="muted">close match</span>' : '') + '</span>' + (h.part_of ? '<span class="pal-part muted">' + esc(h.part_of) + '</span>' : "") + (h.snippet ? '<span class="pal-snip muted">' + mark(h.snippet) + '</span>' : "") + '</a>';
        });
      });
      var files = d.files || [];
      if (files.length) {
        html += '<div class="pal-group">Docs and intake <span class="muted">' + files.length + '</span></div>';
        files.forEach(function (f) {
          html += '<a class="pal-item" role="option" href="/doc/' + encodeURI(f.path) + '"><span class="pal-title">' + esc(f.title) + '</span><span class="pal-part muted">' + esc(f.path) + '</span>' + (f.snippet ? '<span class="pal-snip muted">' + mark(f.snippet) + '</span>' : "") + '</a>';
        });
      }
      show(html, "No ticket matches. Check the spelling or drop a filter.");
      var n = d.total + files.length;
      status.textContent = n ? d.total + (d.capped ? "+" : "") + " ticket" + (d.total === 1 ? "" : "s") + (files.length ? " and " + files.length + " file" + (files.length === 1 ? "" : "s") : "") + (d.fuzzy ? " (some are the closest spelling)" : "") + ". Enter opens, Ctrl Enter opens in a new tab." : "No match.";
    }).catch(function () { status.textContent = "Search could not reach Desk. Is the UI still running?"; });
  }
  /* An action that needs words (Approve, Send back, Park) turns the box into a words box: Enter sends, Esc goes back to the list. */
  function act(a) {
    if (!a || busy) return;
    if (a.words) {
      wordsFor = a; input.value = ""; input.setAttribute("placeholder", a.words);
      show('<div class="pal-words"><b>' + esc(a.label) + ' ' + esc(actsFor || here) + '</b><span class="muted">' + esc(a.hint) + '</span></div>');
      status.textContent = "Type your words. Enter sends, Esc goes back."; input.focus(); return;
    }
    send(a, "");
  }
  function back() {
    wordsFor = null; input.value = ""; input.setAttribute("placeholder", "Part of an id or title, then filters like status:review assignee:chaz"); run();
  }
  function send(a, words) {
    if (busy) return;
    if (a.words && words.trim().length < (a.words_field === "reason" ? 3 : 4)) { status.textContent = "Write a few words first. They are recorded as you typed them."; return; }
    var P = window.DeskProgress, fd = new FormData();
    Object.keys(a.fields).forEach(function (k) { fd.append(k, a.fields[k]); });
    if (a.words) fd.append(a.words_field, words.trim());
    busy = true; status.textContent = a.label + ": sending.";
    var p = P ? P.start(runEl, { steps: ["Sending", "Saving", "Done"] }) : null;
    var done = function (ok, msg, x, err) {
      busy = false;
      if (!ok) {
        var why = P ? P.failText(x, err) : "That did not go through. Nothing was changed.";
        if (p) p.fail(why, P.failDetail(x, err)); status.textContent = why;
        loadActs();  // the token was spent by the try; fresh ones for the next
        return;
      }
      if (p) p.done(msg); status.textContent = msg;
      if (window.DeskLive && x.j.stamp) window.DeskLive.mine(x.j.stamp);
      if (window.deskToast) window.deskToast({ text: msg, batch: x.j.batch || "" });
      setTimeout(function () { close(); if (P && P.reload) P.reload(); else location.reload(); }, 900);
    };
    var req = P ? P.post(a.url, fd, { progress: p, timeoutMs: 15000 })
      : fetch(a.url, { method: "POST", body: fd, headers: { Accept: "application/json" }, credentials: "same-origin" }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, status: r.status, j: j }; }); });
    req.then(function (x) { done(x.ok, (x.j && x.j.message) || "Done.", x); }, function (err) { done(false, "", null, err); });
  }
  document.addEventListener("keydown", function (e) {
    var k = (e.key || "").toLowerCase();
    if (k === "escape" && dlg && !dlg.hidden) { e.preventDefault(); if (wordsFor) back(); else close(); return; }  // from the input, a result, or anywhere in the page
    if ((e.ctrlKey || e.metaKey) && k === "k") { e.preventDefault(); if (dlg && !dlg.hidden) close(); else open(); return; }
    var el = e.target;
    if (k === "/" && !e.ctrlKey && !e.metaKey && !e.altKey && !(el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable))) { e.preventDefault(); open(); }
  });
  if (box) {
    box.addEventListener("focus", function () { if (!giving) open(box.value); });
    box.addEventListener("click", function () { open(box.value); });  // the box may already have focus after Esc
  }
  window.DeskPalette = { open: open, close: close };
})();
