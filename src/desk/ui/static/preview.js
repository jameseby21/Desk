/* File preview panel (DESK-0291). A click on an attachment or a local-file link (any link with data-preview) opens the file in a side
   panel instead of a new tab: PDF in the browser's viewer, Markdown and text rendered, CSV as a table, pictures and video inline, Word,
   Excel and PowerPoint as text. Esc or Close shuts it and puts focus back on the link. Ctrl, Shift or a middle click still opens the
   file in a new tab, and without script the link opens the file as before. Open in Explorer posts to /local/reveal. */
(function () {
  "use strict";
  var panel = null, back = null, opener = null;

  function ensure() {
    if (panel) return panel;
    back = document.createElement("div"); back.className = "pv-back"; back.hidden = true;
    back.addEventListener("click", close);
    panel = document.createElement("aside"); panel.className = "pv"; panel.id = "pv"; panel.hidden = true;
    panel.setAttribute("role", "dialog"); panel.setAttribute("aria-modal", "false"); panel.setAttribute("aria-labelledby", "pv-title"); panel.tabIndex = -1;
    panel.addEventListener("click", onPanelClick);
    document.body.appendChild(back); document.body.appendChild(panel);
    return panel;
  }
  function close() {
    if (!panel || panel.hidden) return;
    panel.hidden = true; back.hidden = true; panel.innerHTML = "";
    document.documentElement.classList.remove("pv-open");
    if (opener && opener.focus) opener.focus();
    opener = null;
  }
  function open(a) {
    var url = a.getAttribute("data-preview"), p = ensure();
    opener = a;
    p.innerHTML = '<div class="pv-head"><div class="pv-name"><b id="pv-title"></b></div><div class="pv-acts"><button type="button" class="btn small pv-close" data-pv-close>Close</button></div></div><div class="pv-loading"></div>';
    p.querySelector("#pv-title").textContent = (a.textContent || "").trim() || "File";
    p.hidden = false; back.hidden = false;
    document.documentElement.classList.add("pv-open");
    p.focus();
    var host = p.querySelector(".pv-loading"), prog = window.DeskProgress ? DeskProgress.start(host, { steps: ["Asking Desk", "Reading the file", "Done"] }) : null;
    fetch(url, { headers: { "Accept": "text/html" }, credentials: "same-origin" })
      .then(function (r) { if (prog) prog.step(1); return r.text().then(function (t) { return { ok: r.ok, text: t }; }); })
      .then(function (res) {
        if (opener !== a) return;
        if (!res.ok) {
          var tmp = document.createElement("div"); tmp.innerHTML = res.text;
          var why = (tmp.textContent || "").trim() || "Desk could not open that file.";
          if (prog) prog.fail(why); else host.textContent = why;
          return;
        }
        p.innerHTML = res.text;
        var c = p.querySelector("[data-pv-close]"); if (c) c.focus();
      }, function () { if (prog) prog.fail("Desk did not answer. Is it still running?"); });
  }
  function onPanelClick(e) {
    var t = e.target;
    if (t.closest("[data-pv-close]")) { close(); return; }
    var b = t.closest("[data-pv-reveal]");
    if (!b) return;
    var msg = panel.querySelector(".pv-msg") || panel;
    var prog = window.DeskProgress ? DeskProgress.start(msg, { steps: ["Sending", "Opening Explorer", "Done"] }) : null;
    b.disabled = true;
    fetch("/local/reveal", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "Accept": "application/json" },
                             body: JSON.stringify({ path: b.getAttribute("data-pv-reveal") }) })
      .then(function (r) { if (prog) prog.step(1); return r.json(); })
      .then(function (j) {
        b.disabled = false;
        if (j && j.ok) { if (prog) prog.done(j.message); else msg.textContent = j.message; }
        else { var why = (j && j.error) || "Explorer could not be opened."; if (prog) prog.fail(why); else msg.textContent = why; }
      }, function () { b.disabled = false; if (prog) prog.fail("Desk did not answer. Is it still running?"); });
  }
  document.addEventListener("click", function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    var a = e.target.closest ? e.target.closest("a[data-preview]") : null;
    if (!a || (panel && panel.contains(a))) return;
    e.preventDefault();
    open(a);
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && panel && !panel.hidden) { e.stopPropagation(); close(); } }, true);
})();
