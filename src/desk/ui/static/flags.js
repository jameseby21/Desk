/* Flags (DESK-0154, DESK-0155): Show and Hide on masked words, and More on a reason line. Everything here changes colour or hides and
   shows a paragraph that is already in the page: no layout moves, nothing is stored, the ticket file is never touched. A masked word
   arrives as stars plus its text in base64; once decoded it becomes the span's own text, transparent, so the block is exactly the
   word's width. Without script the stars stay as they were. */
(function () {
  "use strict";
  function decode(b) { try { return decodeURIComponent(escape(atob(b))); } catch (e) { return null; } }
  function prime() {
    document.querySelectorAll(".mask[data-w]:not([data-ready])").forEach(function (m) {
      var w = decode(m.getAttribute("data-w"));
      if (w) { m.textContent = w; m.setAttribute("data-ready", "1"); }
    });
  }
  function shown(m, on) { m.classList.toggle("shown", on); m.setAttribute("aria-pressed", on ? "true" : "false"); }
  prime();
  if (window.MutationObserver) new MutationObserver(prime).observe(document.body, { childList: true, subtree: true });
  document.addEventListener("click", function (e) {
    var t = e.target.closest ? e.target : null; if (!t) return;
    var m = t.closest(".mask");
    if (m) { shown(m, !m.classList.contains("shown")); return; }
    var b = t.closest('[data-act="mask-toggle"]');
    if (b) {
      var on = b.getAttribute("aria-pressed") !== "true";
      b.closest("li").querySelectorAll(".mask").forEach(function (x) { shown(x, on); });
      b.setAttribute("aria-pressed", on ? "true" : "false"); b.textContent = on ? "Hide" : "Show";
      return;
    }
    var mo = t.closest('[data-act="more"]');
    if (mo) {
      var p = mo.closest(".nr-reason").querySelector(".more"), open = p.hidden;
      p.hidden = !open; mo.textContent = open ? "Less" : "More"; mo.setAttribute("aria-expanded", open ? "true" : "false");
    }
  });
  document.addEventListener("keydown", function (e) {
    var m = e.target.closest ? e.target.closest(".mask") : null;
    if (m && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); shown(m, !m.classList.contains("shown")); }
  });
})();
