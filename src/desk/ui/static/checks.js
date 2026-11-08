/* Check column (DESK-0117 to DESK-0121): Done and Not accepted per line. The server renders every state; this file only opens and
   closes the reason panel and handles the keyboard. Without it the Done boxes still work and the panels are shown (see ticket.html). */
(function () {
  "use strict";
  function lineOf(el) { return el.closest("li.task-list-item"); }
  function panelOf(li) {
    var p = li && li.querySelector(".nr-panel");
    return p && lineOf(p) === li ? p : null;
  }
  function openPanel(li) {
    var p = panelOf(li); if (!p) return false;
    p.hidden = false;
    var b = li.querySelector('.hit[data-act="no"]'); if (b) b.setAttribute("aria-expanded", "true");
    var ta = p.querySelector("textarea"); if (ta) ta.focus();
    return true;
  }
  function closePanel(li) {
    var p = panelOf(li); if (!p || p.hidden) return false;
    p.hidden = true;
    var b = li.querySelector('.hit[data-act="no"]');
    if (b) { b.setAttribute("aria-expanded", "false"); b.focus(); }
    return true;
  }
  document.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest("[data-act]") : null;
    if (!t) return;
    var li = lineOf(t); if (!li) return;
    if (t.getAttribute("data-act") === "no" && t.type === "button") { e.preventDefault(); var p = panelOf(li); if (p && !p.hidden) closePanel(li); else openPanel(li); }
    else if (t.getAttribute("data-act") === "cancel") { e.preventDefault(); closePanel(li); }
  });
  /* Keyboard (DESK-0118): with focus on a line, D toggles Done and N toggles Not accepted (opens the reason panel, or clears it);
     Esc closes the panel. Keys typed into a field, or with Ctrl, Alt or Meta held, are not ours. */
  document.addEventListener("keydown", function (e) {
    var el = e.target, li = el && el.closest ? lineOf(el) : null;
    if (!li || e.ctrlKey || e.altKey || e.metaKey) return;
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
    if (e.key === "Escape") { if (closePanel(li)) e.preventDefault(); return; }
    if (typing) return;
    var k = e.key.toLowerCase();
    if (k !== "d" && k !== "n") return;
    var btn = li.querySelector(':scope > .chk2 [data-act="' + (k === "d" ? "done" : "no") + '"]');
    if (btn) { e.preventDefault(); btn.click(); }
  });
  window.DeskChecks = { openPanel: openPanel, closePanel: closePanel };
})();
