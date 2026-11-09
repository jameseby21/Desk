/* Search palette (DESK-0113). Ctrl K (or Cmd K, or / outside a field) opens it from any page; typing asks /search.json as you type,
   results are grouped by project with counts; arrows move, Enter opens the ticket (or the full results when nothing is picked), Esc closes.
   Without script the header box still submits to /search. */
(function () {
  "use strict";
  var box = document.getElementById("search-open");
  var dlg, input, list, status, items = [], at = -1, timer = null, seq = 0, opener = null, giving = false;
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function mark(s) { return esc(s).replace(/\[/g, "<mark>").replace(/\]/g, "</mark>"); }
  function build() {
    if (dlg) return;
    dlg = document.createElement("div");
    dlg.id = "palette"; dlg.className = "palette"; dlg.setAttribute("role", "dialog"); dlg.setAttribute("aria-label", "Search tickets"); dlg.hidden = true;
    dlg.innerHTML = '<div class="pal-box"><input type="search" id="pal-input" autocomplete="off" aria-label="Search tickets" placeholder="Part of an id or title, then filters like status:review assignee:chaz">' +
      '<div id="pal-status" class="muted pal-status" role="status" aria-live="polite">Type to search. Esc closes.</div><div id="pal-list" class="pal-list" role="listbox"></div></div>';
    document.body.appendChild(dlg);
    input = dlg.querySelector("#pal-input"); list = dlg.querySelector("#pal-list"); status = dlg.querySelector("#pal-status");
    dlg.addEventListener("mousedown", function (e) { if (e.target === dlg) close(); });
    input.addEventListener("input", function () { clearTimeout(timer); timer = setTimeout(run, 90); });
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); pick(at + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); pick(at - 1); }
      else if (e.key === "Enter") { e.preventDefault(); go(e); }
      else if (e.key === "Escape") { e.preventDefault(); close(); }
    });
  }
  function open(seed) {
    build(); if (!dlg.hidden) return;  // already open: a second open (focus then click on the box) changes nothing
    opener = document.activeElement; dlg.hidden = false; document.body.classList.add("pal-open");
    if (seed != null) input.value = seed;
    input.focus(); input.select();
    if (input.value.trim()) run();
  }
  /* Closing gives focus back to what had it. The header box opens the palette when it gets focus, so the hand-back must not count as
     a person focusing it: without `giving` the palette reopened at once and Esc seemed to do nothing (DESK-0206). */
  function close() {
    if (!dlg || dlg.hidden) return;
    dlg.hidden = true; document.body.classList.remove("pal-open");
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
    if (at >= 0 && items[at]) { if (e.ctrlKey || e.metaKey) window.open(items[at].href); else window.location.href = items[at].href; return; }
    window.location.href = "/search?q=" + encodeURIComponent(input.value);
  }
  function run() {
    var q = input.value.trim(), me = ++seq;
    if (!q) { list.innerHTML = ""; items = []; at = -1; status.textContent = "Type to search. Esc closes."; return; }
    fetch("/search.json?q=" + encodeURIComponent(q), { headers: { Accept: "application/json" } }).then(function (r) { return r.json(); }).then(function (d) {
      if (me !== seq) return;
      var html = "";
      d.groups.forEach(function (g) {
        html += '<div class="pal-group">' + esc(g.project) + ' <span class="muted">' + g.count + '</span></div>';
        g.hits.forEach(function (h) {
          html += '<a class="pal-item" role="option" href="/ticket/' + esc(h.id) + '"><span class="chip">' + esc(h.id) + '</span> <span class="chip">' + esc(h.status_label || h.status) + '</span> <span class="chip">' + esc(h.priority) + '</span>' +
            '<span class="pal-title">' + esc(h.title) + (h.fuzzy ? ' <span class="muted">close match</span>' : '') + '</span>' + (h.snippet ? '<span class="pal-snip muted">' + mark(h.snippet) + '</span>' : "") + '</a>';
        });
      });
      list.innerHTML = html || '<div class="muted pal-none">No ticket matches. Check the spelling or drop a filter.</div>';
      items = Array.prototype.slice.call(list.querySelectorAll("a.pal-item")); at = items.length ? 0 : -1; pick(at);
      status.textContent = d.total ? d.total + (d.capped ? "+" : "") + " match" + (d.total === 1 ? "" : "es") + (d.fuzzy ? " (some are the closest spelling)" : "") + ". Enter opens, Ctrl Enter opens in a new tab." : "No match.";
    }).catch(function () { status.textContent = "Search could not reach Desk. Is the UI still running?"; });
  }
  document.addEventListener("keydown", function (e) {
    var k = (e.key || "").toLowerCase();
    if (k === "escape" && dlg && !dlg.hidden) { e.preventDefault(); close(); return; }  // from the input, a result, or anywhere in the page
    if ((e.ctrlKey || e.metaKey) && k === "k") { e.preventDefault(); if (dlg && !dlg.hidden) close(); else open(); return; }
    var el = e.target;
    if (k === "/" && !e.ctrlKey && !e.metaKey && !e.altKey && !(el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable))) { e.preventDefault(); open(); }
  });
  if (box) {
    box.addEventListener("focus", function () { if (!giving) open(box.value); });
    box.addEventListener("click", function () { open(box.value); });  // the box may already have focus after Esc
  }
})();
