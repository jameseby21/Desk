/* Copy buttons (DESK-0177): a button with data-copy puts that text on the clipboard and says so for a moment. */
(function () {
  "use strict";
  function done(b, ok) {
    var was = b.getAttribute("data-label") || b.textContent;
    b.setAttribute("data-label", was);
    b.textContent = ok ? "Copied" : "Press Ctrl C";
    setTimeout(function () { b.textContent = was; }, 1500);
  }
  function fallback(text) {
    var ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    var ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("button.copy[data-copy]");
    if (!b) return;
    var text = b.getAttribute("data-copy");
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(text).then(function () { done(b, true); }, function () { done(b, fallback(text)); });
    else done(b, fallback(text));
  });
})();
