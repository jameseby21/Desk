/* DESK-0171 and DESK-0305: the ticket timeline. Everything is already on the page, so nothing waits on the server.
   The status strip: a segment's label shows only when it fits whole; hovering or focusing a segment names it under the strip, and
   the key under it names every period in words (status, from, for how long, on you), so no colour needs hover to be read.
   The event rail: the chips (All, Decisions, Status, Comments, Changes, Git) and the More filters boxes (ANDed across
   dimensions, either within one) narrow the events; the first 12 that match show until Show all is pressed. Consecutive git saves
   by one actor on one day are one row (a fold) that opens in place. */
(function () {
  var root = document.getElementById("timeline");
  if (!root) return;

  // -- the status strip ---------------------------------------------------------------
  var segs = Array.prototype.slice.call(root.querySelectorAll(".tl-seg")), info = document.getElementById("tl-st-info");
  function fit() {
    segs.forEach(function (s) {
      var lab = s.querySelector(".tl-lab");
      if (!lab) return;
      lab.classList.remove("tl-hide");
      if (lab.offsetWidth > s.clientWidth - 4) lab.classList.add("tl-hide");  // a label that does not fit whole is not drawn
    });
  }
  function say(s) { if (info) info.textContent = s ? s.getAttribute("data-text") : info.getAttribute("data-now"); }
  segs.forEach(function (s) {
    s.addEventListener("mouseenter", function () { say(s); });
    s.addEventListener("focus", function () { say(s); });
    s.addEventListener("mouseleave", function () { say(null); });
    s.addEventListener("blur", function () { say(null); });
  });
  fit();
  var keyMore = document.getElementById("tl-key-more");
  if (keyMore) keyMore.addEventListener("click", function () {  // the key lists the newest 12 periods; older ones open in place
    var on = keyMore.getAttribute("aria-expanded") !== "true";
    root.querySelectorAll(".tl-k[data-old]").forEach(function (li) { li.hidden = !on; });
    keyMore.setAttribute("aria-expanded", on ? "true" : "false");
    keyMore.textContent = on ? "Show only the newest periods" : keyMore.getAttribute("data-label") || keyMore.textContent;
  });
  if (keyMore) keyMore.setAttribute("data-label", keyMore.textContent);
  var tick;
  window.addEventListener("resize", function () { clearTimeout(tick); tick = setTimeout(fit, 100); });

  // -- the event rail -------------------------------------------------------------------
  var list = document.getElementById("tl-list");
  if (!list) return;
  var first = parseInt(list.getAttribute("data-first"), 10) || 12, open = false, group = "all";
  // a rail position is an event row, or a fold of consecutive git saves (one row until it is opened, then its saves count one each)
  var units = Array.prototype.slice.call(list.querySelectorAll(".tl-ev,.tl-foldh")), days = Array.prototype.slice.call(list.querySelectorAll(".tl-day"));
  var runs = {};
  units.forEach(function (li) {
    var id = li.getAttribute("data-run");
    if (!id) return;
    var r = runs[id] = runs[id] || { head: null, members: [], open: false };
    if (li.classList.contains("tl-foldh")) r.head = li; else r.members.push(li);
  });
  var chips = Array.prototype.slice.call(root.querySelectorAll(".tl-chip")), boxes = Array.prototype.slice.call(root.querySelectorAll(".tl-f input[type=checkbox]"));
  var more = document.getElementById("tl-showall"), shown = document.getElementById("tl-shown"), clear = document.getElementById("tl-clear");

  function chosen() {
    var f = {};
    boxes.forEach(function (b) { if (b.checked) (f[b.getAttribute("data-dim")] = f[b.getAttribute("data-dim")] || []).push(b.value); });
    return f;
  }

  function match(li, f, keys) {
    return (group === "all" || li.getAttribute("data-group") === group) &&
           keys.every(function (k) { return f[k].indexOf(li.getAttribute("data-" + k)) >= 0; });
  }

  function apply() {
    var f = chosen(), keys = Object.keys(f), n = 0, total = 0, visible = 0;  // n: rail positions that match; total: events that match
    units.forEach(function (li) {
      var id = li.getAttribute("data-run"), r = id ? runs[id] : null;
      if (r && li === r.head) {
        var ok = r.members.filter(function (m) { return match(m, f, keys); }).length;
        total += ok;
        var lab = li.querySelector(".tl-fn");
        if (lab) lab.textContent = lab.textContent.replace(/^\d+ git saves?/, ok + (ok === 1 ? " git save" : " git saves"));
        if (!ok) { li.hidden = true; return; }
        n++;  // a fold is one position, open or not: opening it shows its saves under it and pushes nothing out of the newest 12
        li.hidden = !open && n > first;
        if (!li.hidden) visible += ok;
        return;
      }
      var hit = match(li, f, keys);
      if (r) { li.hidden = !hit || !r.open || r.head.hidden; return; }
      if (hit) total++;
      if (!hit) { li.hidden = true; return; }
      n++;
      li.hidden = !open && n > first;
      if (!li.hidden) visible++;
    });
    days.forEach(function (d) { d.hidden = !d.querySelector(".tl-ev:not([hidden]),.tl-foldh:not([hidden])"); });
    var what = group === "all" && !keys.length ? "events" : "matching events";
    shown.textContent = !total ? "No events match. Pick All to see every event." :
      visible < total ? "Showing the newest " + visible + " of " + total + " " + what + "." :
      total === 1 ? "Showing the one " + what.replace("events", "event") + "." : "Showing all " + total + " " + what + ", newest first.";
    if (more) {
      more.hidden = n <= first;
      more.setAttribute("aria-expanded", open ? "true" : "false");
      more.textContent = open ? more.getAttribute("data-few") : more.getAttribute("data-all").replace("{n}", total);
    }
    root.querySelectorAll(".tl-on").forEach(function (el) {
      var c = (f[el.getAttribute("data-of")] || []).length;
      el.textContent = c ? "(" + c + " picked)" : "";
    });
    if (clear) clear.hidden = !keys.length;
  }

  chips.forEach(function (c) {
    c.addEventListener("click", function () {
      group = c.getAttribute("data-filter");
      chips.forEach(function (o) { o.setAttribute("aria-pressed", o === c ? "true" : "false"); });
      apply();
    });
  });
  boxes.forEach(function (b) { b.addEventListener("change", apply); });
  Object.keys(runs).forEach(function (id) {
    var r = runs[id], b = r.head && r.head.querySelector(".tl-foldb");
    if (!b) return;
    b.addEventListener("click", function () {
      r.open = !r.open;
      b.setAttribute("aria-expanded", r.open ? "true" : "false");
      apply();
    });
  });
  if (clear) clear.addEventListener("click", function () { boxes.forEach(function (b) { b.checked = false; }); apply(); });
  if (more) {
    more.addEventListener("click", function () {
      open = !open;
      try { sessionStorage.setItem("desk-tl-all:" + location.pathname, open ? "1" : "0"); } catch (e) { /* storage blocked: nothing to remember */ }
      apply();
    });
    try { open = sessionStorage.getItem("desk-tl-all:" + location.pathname) === "1"; } catch (e) { open = false; }  // stay expanded across the page's own refresh
  }
  apply();
})();
