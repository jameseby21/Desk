/* DESK-0171 and DESK-0305: the ticket timeline. Everything is already on the page, so nothing waits on the server.
   The status strip: a segment's label shows only when it fits whole; hovering or focusing a segment names it under the strip.
   The event rail: the chips (All, Decisions, Status, Comments, Changes, Git) and the More filters boxes (ANDed across
   dimensions, either within one) narrow the events; the first 12 that match show until Show all is pressed. */
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
  var tick;
  window.addEventListener("resize", function () { clearTimeout(tick); tick = setTimeout(fit, 100); });

  // -- the event rail -------------------------------------------------------------------
  var list = document.getElementById("tl-list");
  if (!list) return;
  var first = parseInt(list.getAttribute("data-first"), 10) || 12, open = false, group = "all";
  var items = Array.prototype.slice.call(list.querySelectorAll(".tl-ev")), days = Array.prototype.slice.call(list.querySelectorAll(".tl-day"));
  var chips = Array.prototype.slice.call(root.querySelectorAll(".tl-chip")), boxes = Array.prototype.slice.call(root.querySelectorAll(".tl-f input[type=checkbox]"));
  var more = document.getElementById("tl-showall"), shown = document.getElementById("tl-shown"), clear = document.getElementById("tl-clear");

  function chosen() {
    var f = {};
    boxes.forEach(function (b) { if (b.checked) (f[b.getAttribute("data-dim")] = f[b.getAttribute("data-dim")] || []).push(b.value); });
    return f;
  }

  function apply() {
    var f = chosen(), keys = Object.keys(f), n = 0, visible = 0;
    items.forEach(function (li) {
      var ok = (group === "all" || li.getAttribute("data-group") === group) &&
               keys.every(function (k) { return f[k].indexOf(li.getAttribute("data-" + k)) >= 0; });
      if (ok) n++;
      li.hidden = !ok || (!open && n > first);
      if (!li.hidden) visible++;
    });
    days.forEach(function (d) { d.hidden = !d.querySelector(".tl-ev:not([hidden])"); });
    var what = group === "all" && !keys.length ? "events" : "matching events";
    shown.textContent = !n ? "No events match. Pick All to see every event." :
      visible < n ? "Showing the newest " + visible + " of " + n + " " + what + "." :
      n === 1 ? "Showing the one " + what.replace("events", "event") + "." : "Showing all " + n + " " + what + ", newest first.";
    if (more) {
      more.hidden = n <= first;
      more.setAttribute("aria-expanded", open ? "true" : "false");
      more.textContent = open ? more.getAttribute("data-few") : more.getAttribute("data-all").replace("{n}", n);
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
