/* Decide mode, inbox triage and batch approve (DESK-0285).
   - D from any page opens /decide (never while typing; DESK-0290 owns the full key map and may take this over).
   - On /decide: Y agree, N send back (type why, then Enter), 1-9 pick an option, S snooze a week, E open, Space or J next, K previous, ? keys.
   - On /triage: 1-4 Now, Planned, Later, Park; M merge (type the id, then Enter); E, Space or J, K and ? as above.
   - On Today's Needs me: a box on each ask row and an "Approve N together" bar that opens the dry run at /decide/batch.
   A write redirects with undo=<batch>&msg=<sentence>; DESK-0284's toast reads them. Without it, the page itself says the sentence. */
(function () {
  "use strict";

  function typing(e) {
    var a = e.target;
    if (!a) return false;
    if (a.isContentEditable || /^(TEXTAREA|SELECT)$/.test(a.tagName)) return true;
    return a.tagName === "INPUT" && !/^(checkbox|radio|submit|button|hidden|reset)$/.test(a.type || "");
  }
  function plainKey(e) { return !e.ctrlKey && !e.metaKey && !e.altKey; }
  function byKey(root, k) { return root.querySelector('[data-dm-key="' + k + '"]'); }
  function press(el) { if (el && !el.disabled) el.click(); }

  var dm = document.querySelector("[data-dm]");

  /* ---- the help panel ---------------------------------------------------------------------------------------------- */
  function toggleHelp(force) {
    var h = document.getElementById("dm-help"), b = document.querySelector("[data-dm-help]");
    if (!h) return;
    h.hidden = typeof force === "boolean" ? !force : !h.hidden;
    if (b) b.setAttribute("aria-expanded", String(!h.hidden));
  }
  document.querySelectorAll("[data-dm-help]").forEach(function (b) { b.addEventListener("click", function () { toggleHelp(); }); });

  /* ---- progress on the plain forms (visible progress rule) ---------------------------------------------------------- */
  document.addEventListener("submit", function (e) {
    var f = e.target;
    if (!f.closest || !f.closest(".dm") || !window.DeskProgress) return;
    var slot = f.querySelector(".dp-slot");
    if (slot) window.DeskProgress.start(slot, { steps: window.DeskProgress.stepsOf(f) || ["Sending", "Saving", "Done"] });
  });

  /* ---- keys ---------------------------------------------------------------------------------------------------------- */
  document.addEventListener("keydown", function (e) {
    if (!plainKey(e)) return;
    var k = (e.key || "").toLowerCase();
    if (k === "escape") { toggleHelp(false); if (typing(e)) e.target.blur(); return; }
    if (dm && typing(e) && k === "enter" && e.target.name === "into") { e.preventDefault(); press(byKey(dm, "m")); return; }
    if (dm && typing(e) && k === "enter" && e.target.name === "words" && dm.getAttribute("data-mode") === "changes") { e.preventDefault(); press(byKey(dm, "n")); return; }
    if (typing(e)) return;
    if (!dm) {
      /* Only with nothing focused: a focused check line on a ticket uses D for Done (checks.js), and another handler may have taken it. */
      var idle = e.target === document || e.target === document.body || e.target === document.documentElement;
      if (k === "d" && idle && !e.defaultPrevented && location.pathname !== "/decide") { e.preventDefault(); location.href = "/decide"; }
      return;
    }
    if (k === "?") { e.preventDefault(); toggleHelp(); return; }
    var mode = dm.getAttribute("data-dm");
    if (k === " " && /^(BUTTON|A|SUMMARY)$/.test(e.target.tagName || "")) return;  // Space on a focused control presses it
    if (k === " " || k === "j") { var n = dm.getAttribute("data-next"); if (n) { e.preventDefault(); location.href = "/" + mode + "?at=" + n; } return; }
    if (k === "k") { var p = dm.getAttribute("data-prev"); if (p) { e.preventDefault(); location.href = "/" + mode + "?at=" + p; } return; }
    if (k === "e") { var o = dm.getAttribute("data-open"); if (o) { e.preventDefault(); location.href = o; } return; }
    if (mode === "triage") {
      if (/^[1-4]$/.test(k)) { e.preventDefault(); press(byKey(dm, k)); }
      else if (k === "m") { var into = document.getElementById("dm-into"); if (into) { e.preventDefault(); into.focus(); } }
      return;
    }
    /* decide */
    if (/^[1-9]$/.test(k)) {
      var r = dm.querySelectorAll('input[name="option"]')[parseInt(k, 10) - 1];
      if (r && !r.disabled) { e.preventDefault(); r.checked = true; r.focus(); }
      return;
    }
    if (k === "y") { e.preventDefault(); press(byKey(dm, "y")); return; }
    if (k === "s") { e.preventDefault(); press(byKey(dm, "s")); return; }
    if (k === "n") {
      var w = dm.querySelector('input[name="words"]'), lab = dm.querySelector("[data-dm-label]");
      e.preventDefault();
      if (!w || w.disabled) return;
      dm.setAttribute("data-mode", "changes");
      if (lab) lab.textContent = "Why send it back? Type it, then press Enter";
      w.placeholder = "What is missing or wrong";
      w.focus();
    }
  });

  /* ---- batch approve on Today's Needs me ----------------------------------------------------------------------------- */
  var nm = document.getElementById("needs-me");
  if (nm) {
    var rows = Array.prototype.filter.call(nm.querySelectorAll(".nm-row"), function (row) { return row.querySelector(".nm-ask"); });
    var tools = nm.querySelector(".nm-tools");
    if (rows.length && tools) {
      var one = document.createElement("a");
      one.className = "rowb"; one.href = "/decide"; one.title = "Press D from any page";
      one.textContent = "Decide one by one (D)";
      tools.insertBefore(one, tools.firstChild);
    }
    if (rows.length) {
      var bar = document.createElement("div");
      bar.className = "dm-batchbar"; bar.hidden = true; bar.setAttribute("role", "region"); bar.setAttribute("aria-label", "Approve together");
      var count = document.createElement("span"), go = document.createElement("a"), clear = document.createElement("button");
      go.className = "btn ball"; clear.type = "button"; clear.className = "ghostb"; clear.textContent = "Clear";
      bar.appendChild(count); bar.appendChild(go); bar.appendChild(clear);
      var boxes = [];
      var update = function () {
        var ids = boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
        bar.hidden = !ids.length;
        count.textContent = ids.length + " ticked";
        go.textContent = "Approve " + ids.length + " together";
        go.href = "/decide/batch?" + ids.map(function (i) { return "ids=" + encodeURIComponent(i); }).join("&") + "&back=" + encodeURIComponent(location.pathname);
      };
      rows.forEach(function (row) {
        var id = row.getAttribute("data-ticket"), lab = document.createElement("label"), box = document.createElement("input");
        box.type = "checkbox"; box.value = id; box.className = "dm-tick"; box.setAttribute("aria-label", "Tick " + id + " to approve it with others");
        lab.className = "dm-tickwrap"; lab.appendChild(box);
        row.insertBefore(lab, row.firstChild); row.classList.add("dm-tickrow");
        box.addEventListener("change", update);
        boxes.push(box);
      });
      clear.addEventListener("click", function () { boxes.forEach(function (b) { b.checked = false; }); update(); });
      nm.appendChild(bar);
    }
  }

  /* ---- the sentence after a write, when DESK-0284's toast is not on this build ----------------------------------------- */
  window.addEventListener("load", function () {
    var q = new URLSearchParams(location.search), msg = q.get("msg");
    if (!msg || window.deskToast || document.querySelector(".dm-msg")) return;
    var n = document.createElement("div");
    n.className = "notice dm-msg"; n.setAttribute("role", "status"); n.textContent = msg;
    var main = document.querySelector("main");
    if (main) main.insertBefore(n, main.firstChild);
  });
})();
