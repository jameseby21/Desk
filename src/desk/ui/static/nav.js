/* Ticket navigation (DESK-0148): j or the right arrow goes to Next, k or the left arrow to Previous, when no field has focus and no
   modifier key is held. The links are real links (rel=prev, rel=next), so they also open in a new tab. Changing either picker sends
   its form (static/progress.js saves the choice in place and the links and the position text refresh). */
(function () {
  "use strict";
  document.addEventListener("keydown", function (e) {
    if (e.ctrlKey || e.altKey || e.metaKey || e.shiftKey || e.defaultPrevented) return;
    var el = e.target;
    if (el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable)) return;
    var rel = e.key === "j" || e.key === "ArrowRight" ? "next" : e.key === "k" || e.key === "ArrowLeft" ? "prev" : "";
    if (!rel) return;
    var a = document.querySelector('#tnav a[rel="' + rel + '"]');
    if (a) { e.preventDefault(); window.location.href = a.href; }
  });
  document.addEventListener("change", function (e) {
    var f = e.target.closest ? e.target.closest(".navpick, .autosubmit") : null;
    if (f && f.requestSubmit) f.requestSubmit();
  });
})();
