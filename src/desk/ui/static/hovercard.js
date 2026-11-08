/* Hover cards (DESK-0085): rest the pointer on a ticket id or a document link (or focus it with the keyboard) and a small card shows what
   is behind it: for a ticket its id, status, project, title, a line of summary and how much Acceptance is true; for a document its
   title, path, first paragraph, and Read here / Edit in VS Code. The cards come from /card/ticket/<ID> and /card/doc/<path>, are fetched
   once per page and read from the files at that moment. The links stay real links; without script nothing changes. */
(function () {
  "use strict";
  var DELAY_MS = 350, cache = {}, card = null, timer = null, current = null;

  function urlFor(a) {
    var h = a.getAttribute("href") || "", m;
    if (a.classList.contains("tid") && (m = /^\/ticket\/([A-Za-z0-9-]+)$/.exec(h))) return "/card/ticket/" + m[1];
    if (a.classList.contains("docref") && (m = /^\/doc\/([^#?]+)/.exec(h))) return "/card/doc/" + m[1];
    return "";
  }
  function ensure() {
    if (!card) {
      card = document.createElement("div"); card.className = "hcard"; card.id = "hcard"; card.setAttribute("role", "tooltip"); card.hidden = true;
      card.addEventListener("mouseenter", function () { clearTimeout(timer); });
      card.addEventListener("mouseleave", hide);
      document.body.appendChild(card);
    }
    return card;
  }
  function place(a) {
    var r = a.getBoundingClientRect(), c = ensure();
    c.style.left = Math.max(8, Math.min(window.pageXOffset + r.left, window.pageXOffset + document.documentElement.clientWidth - c.offsetWidth - 8)) + "px";
    var below = window.pageYOffset + r.bottom + 6;
    c.style.top = (r.bottom + c.offsetHeight + 12 > window.innerHeight && r.top > c.offsetHeight + 12 ? window.pageYOffset + r.top - c.offsetHeight - 6 : below) + "px";
  }
  function show(a) {
    var url = urlFor(a); if (!url) return;
    current = a;
    function put(html) { if (current !== a) return; var c = ensure(); c.innerHTML = html; c.hidden = false; a.setAttribute("aria-describedby", "hcard"); place(a); }
    if (cache[url] !== undefined) return put(cache[url]);
    fetch(url, { headers: { "Accept": "text/html" }, credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.text() : ""; })
      .then(function (t) { cache[url] = t; if (t) put(t); }, function () { /* no card; the link still works */ });
  }
  function hide() {
    clearTimeout(timer);
    if (current) { current.removeAttribute("aria-describedby"); current = null; }
    if (card) card.hidden = true;
  }
  function target(e) { return e.target && e.target.closest ? e.target.closest("a.tid, a.docref") : null; }
  function arm(e) {
    var a = target(e); if (!a || !urlFor(a)) return;
    clearTimeout(timer); timer = setTimeout(function () { show(a); }, DELAY_MS);
  }
  document.addEventListener("mouseover", arm);
  document.addEventListener("focusin", arm);
  document.addEventListener("mouseout", function (e) { var a = target(e); if (a && !(card && card.contains(e.relatedTarget))) { clearTimeout(timer); timer = setTimeout(hide, 120); } });
  document.addEventListener("focusout", function (e) { if (target(e)) hide(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") hide(); });
  document.addEventListener("scroll", hide, true);
})();
