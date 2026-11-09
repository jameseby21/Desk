/* Check column (DESK-0117 to DESK-0121): Done and Not accepted per line. The server renders every state; this file only opens and
   closes the reason panel and handles the keyboard. Without it the Done boxes still work and the panels are shown (see ticket.html). */
(function () {
  "use strict";
  function lineOf(el) { return el.closest("li.task-list-item"); }
  function panelOf(li) {
    var p = li && li.querySelector(".nr-panel");
    return p && lineOf(p) === li ? p : null;
  }
  function boxes(li) {
    return { done: li.querySelector(':scope > .chk2 [data-act="done"] .box'), no: li.querySelector(':scope > .chk2 [data-act="no"] .box') };
  }
  /* While the panel is open on a line that is not yet Not accepted, show the box as set and the other cleared (what a click does);
     closing without saving puts it back. The file only changes on Save, or on Cancel from a Done line (which returns it to open). */
  function pending(li, on) {
    var b = boxes(li); if (!b.no || b.no.classList.contains("on-no")) return;
    if (on) { b.no.classList.add("pending"); if (b.done && b.done.classList.contains("on-done")) { b.done.classList.remove("on-done"); b.done.setAttribute("data-was", "1"); } }
    else { b.no.classList.remove("pending"); if (b.done && b.done.getAttribute("data-was")) { b.done.classList.add("on-done"); b.done.removeAttribute("data-was"); } }
  }
  function openPanel(li) {
    var p = panelOf(li); if (!p) return false;
    p.hidden = false; pending(li, true);
    var b = li.querySelector('.hit[data-act="no"]'); if (b) b.setAttribute("aria-expanded", "true");
    var ta = p.querySelector("textarea"); if (ta) ta.focus();
    return true;
  }
  function closePanel(li, quiet) {
    var p = panelOf(li); if (!p || p.hidden) return false;
    p.hidden = true; pending(li, false);
    var err = p.querySelector(".nr-err"); if (err) err.textContent = "";
    var b = li.querySelector('.hit[data-act="no"]');
    if (b) { b.setAttribute("aria-expanded", "false"); if (!quiet) b.focus(); }
    return true;
  }
  document.addEventListener("click", function (e) {
    /* A click anywhere outside an open reason panel closes it (what was typed stays in the box for next time). */
    document.querySelectorAll(".nr-panel:not([hidden])").forEach(function (p) {
      var li = lineOf(p), opener = e.target.closest ? e.target.closest('[data-act="no"],[data-act="edit"]') : null;
      if (p.contains(e.target) || (opener && lineOf(opener) === li)) return;
      closePanel(li, true);
    });
    var t = e.target.closest ? e.target.closest("[data-act]") : null;
    if (!t) return;
    var li = lineOf(t); if (!li) return;
    if (t.getAttribute("data-act") === "no" && t.type === "button") { e.preventDefault(); var p = panelOf(li); if (p && !p.hidden) closePanel(li); else openPanel(li); }
    else if (t.getAttribute("data-act") === "cancel") { e.preventDefault(); closePanel(li); }
    else if (t.getAttribute("data-act") === "edit") { e.preventDefault(); openPanel(li); }
  });
  /* Save reason needs 3 or more non-space characters (the server says the same with a 422). Cancel from a Done line is allowed through. */
  document.addEventListener("submit", function (e) {
    var f = e.target; if (!f.closest || !f.closest(".nr-panel")) return;
    if (e.submitter && e.submitter.getAttribute("data-act") === "cancel-clear") return;
    var ta = f.querySelector("textarea"), err = f.querySelector(".nr-err");
    if (ta && ta.value.replace(/\s/g, "").length < 3) {
      e.preventDefault(); e.stopImmediatePropagation();
      if (err) err.textContent = "Say what is wrong, in at least 3 characters.";
      ta.focus();
    }
  }, true);
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
  /* Save in place (DESK-0121): a box click posts with Accept: application/json and the reply carries the section again (new states,
     new one-time tokens) and the summary line, so the page changes without a reload. A failure leaves the page as it was and says why.
     Without fetch, or if anything here throws, the plain form post and redirect still work. */
  function say(host, msg) {
    var s = document.getElementById("chk-status");
    if (!s) { s = document.createElement("div"); s.id = "chk-status"; s.className = "chk-status"; s.setAttribute("role", "status"); host.parentNode.insertBefore(s, host); }
    s.textContent = msg || "";
  }
  document.addEventListener("submit", function (e) {
    var f = e.target;
    var P = window.DeskProgress;
    if (!window.fetch || !P || !f.classList || !f.classList.contains("chk")) return;
    var host = f.closest("[data-check-section]"); if (!host) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (f.getAttribute("data-busy")) return;  // a second click while the first is out does nothing
    f.setAttribute("data-busy", "1");
    var idx = f.querySelector('[name="index"]').value, sub = e.submitter, act = sub ? sub.getAttribute("data-act") : "";
    var fd = new FormData(f); if (sub && sub.name) fd.set(sub.name, sub.value);
    var btns = host.querySelectorAll("button"); btns.forEach(function (b) { b.disabled = true; });
    host.setAttribute("aria-busy", "true"); say(host, "");
    function done() { f.removeAttribute("data-busy"); host.removeAttribute("aria-busy"); }
    var prog = P.start(P.floater(), { steps: P.stepsOf(f), floating: true });  // a pill at the bottom of the window: it never moves the page
    P.post(f.getAttribute("action"), fd, { progress: prog, timeoutMs: 30000 })
      .then(function (x) {
        if (!x.ok) {
          btns.forEach(function (b) { b.disabled = false; });
          var err = f.querySelector(".nr-err"), msg = x.j.error || "Desk could not save that. Nothing was changed.";
          if (err) { err.textContent = msg; } else { say(host, msg); }
          prog.fail(msg, P.failDetail(x));
          return done();
        }
        prog.done("Saved.");
        host.innerHTML = x.j.html;
        var sum = document.getElementById("chk-summary"); if (sum && host.getAttribute("data-check-section") === "Acceptance") sum.innerHTML = x.j.summary_html;
        if (window.DeskLive) window.DeskLive.mine(x.j.stamp);
        var li = host.querySelectorAll("li.task-list-item")[parseInt(idx, 10)];
        if (li) li.focus({ preventScroll: true });
        done();
      })
      .catch(function (err) {
        btns.forEach(function (b) { b.disabled = false; });
        var msg = P.failText(null, err);
        say(host, msg); prog.fail(msg, P.failDetail(null, err));
        done();
      });
  }, true);
  /* The same paste or drop works in the reason: the screenshot is saved with the ticket at once, shown as a thumbnail, and its name goes
     to the server with Save as evidence under the line. */
  if (window.DeskShots) {
    DeskShots.watch(".nr-panel textarea", function (el, shot) {
      var f = el.closest("form"), box = f.querySelector(".shots"), err = f.querySelector(".nr-err");
      var h = document.createElement("input"); h.type = "hidden"; h.name = "evidence"; h.value = shot.name;
      var t = DeskShots.thumb(shot, function () {
        DeskShots.remove(el, shot.name).then(function () { t.remove(); h.remove(); box.hidden = !box.querySelector(".shot"); },
                                              function (e) { if (err) err.textContent = e.message; });
      });
      box.appendChild(t); box.appendChild(h); box.hidden = false; if (err) err.textContent = "";
    }, function (el, msg) { var err = el.closest("form").querySelector(".nr-err"); if (err) err.textContent = msg; });
  }
  window.DeskChecks = { openPanel: openPanel, closePanel: closePanel };
})();
