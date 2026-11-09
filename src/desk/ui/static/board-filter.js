/* Board filter dropdowns (DESK-0034): typing in a dropdown narrows its list; only one dropdown is open at a time; Esc or a click
   outside closes it. Without script the dropdowns are plain forms of check boxes with an Apply button. */
(function () {
  "use strict";
  var dds = Array.prototype.slice.call(document.querySelectorAll("details.dd"));
  dds.forEach(function (d) {
    d.addEventListener("toggle", function () {
      if (!d.open) return;
      dds.forEach(function (o) { if (o !== d) o.open = false; });
      var q = d.querySelector(".dd-q"); if (q) q.focus();
    });
    var q = d.querySelector(".dd-q"), items = d.querySelectorAll("li[data-text]"), none = d.querySelector("li.dd-none[hidden]");
    if (q) q.addEventListener("input", function () {
      var t = q.value.trim().toLowerCase(), shown = 0;
      items.forEach(function (li) { var on = !t || li.dataset.text.indexOf(t) >= 0; li.hidden = !on; if (on) shown++; });
      d.querySelectorAll("li.dd-head").forEach(function (h) {  // a group heading with nothing under it is hidden too
        var n = h.nextElementSibling, any = false;
        while (n && !n.classList.contains("dd-head") && n.hasAttribute("data-text")) { if (!n.hidden) any = true; n = n.nextElementSibling; }
        h.hidden = !any;
      });
      if (none) none.hidden = shown > 0;
    });
    if (q) q.addEventListener("keydown", function (e) { if (e.key === "Enter") e.preventDefault(); });
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") dds.forEach(function (d) { d.open = false; }); });
  document.addEventListener("click", function (e) { dds.forEach(function (d) { if (d.open && !d.contains(e.target)) d.open = false; }); });
})();
