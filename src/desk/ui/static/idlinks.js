/* DESK-0334: ticket ids in text a script draws (toasts, messages, fragments that arrive after the page) become links by the same rule as
   the server's (desk/idlinks.py): an id that exists is a link; one that does not stays plain with the title "No such ticket"; never in
   code, a URL, a file name, a field or a link to somewhere else; inside a whole-row link it is a role=link span (an anchor cannot hold an
   anchor). The page itself arrives linked, so nothing is scanned at load: only nodes added afterwards. The ids that exist come from
   /ids?q=A,B, asked once per id per page. window.deskIds.scan(node) links a node now; window.deskIds.link(text) returns a fragment. */
(function () {
  "use strict";
  var ID = /(^|[^A-Za-z0-9_-])([A-Z]{2,10}-\d{4})(?![\dA-Za-z_]|-[A-Za-z0-9])/g;
  /* bounded (300 characters) so a long unbroken token cannot make the scan quadratic */
  var PROT = /`[^`\n]*`|\S{0,300}:\/\/\S*|\bwww\.\S+|[A-Za-z]:\\\S*|\S{0,300}\\\S*|\S{1,300}\.(?:md|png|jpe?g|gif|webp|ya?ml|json|py|html?|txt|pdf|csv|js|css|toml|log)\b/g;
  var SKIP = /^(CODE|PRE|SCRIPT|STYLE|TEXTAREA|TITLE|OPTION|SELECT|BUTTON|SVG|KBD|SAMP|NOSCRIPT|TEMPLATE|INPUT)$/;
  var known = {};  /* id -> [title, status] when it exists, null when it does not */

  function candidates(text) {
    var prot = [], m, out = [];
    PROT.lastIndex = 0;
    while ((m = PROT.exec(text))) prot.push([m.index, m.index + m[0].length]);
    ID.lastIndex = 0;
    while ((m = ID.exec(text))) {
      var at = m.index + m[1].length;
      if (prot.some(function (p) { return p[0] <= at && at < p[1]; })) continue;
      out.push({ id: m[2], at: at });
    }
    return out;
  }

  function allowed(node) {  /* where may this text node's ids be linked, and is it inside a whole-row ticket link? */
    var nested = "", el = node.parentNode;
    for (; el && el.nodeType === 1; el = el.parentNode) {
      if (SKIP.test(el.tagName.toUpperCase()) || el.isContentEditable || el.hasAttribute("data-noids")) return null;
      if (el.classList && (el.classList.contains("tid") || el.classList.contains("tid-missing"))) return null;
      if (el.tagName === "A") {
        var m = /^\/ticket\/([A-Za-z]+-\d{4})/.exec(el.getAttribute("href") || "");
        if (!m) return null;
        nested = nested || m[1].toUpperCase();
      }
    }
    return { nested: nested };
  }

  function make(id, info, nested) {
    var e;
    if (!info) { e = document.createElement("span"); e.className = "tid-missing"; e.title = "No such ticket"; e.textContent = id; return e; }
    if (nested) {
      e = document.createElement("span"); e.className = "tid"; e.setAttribute("role", "link"); e.tabIndex = 0; e.setAttribute("data-href", "/ticket/" + id);
    } else { e = document.createElement("a"); e.className = "tid"; e.href = "/ticket/" + id; }
    e.title = info[0]; e.setAttribute("data-status", info[1]); e.textContent = id;
    return e;
  }

  function fragment(text, nested, own) {
    var f = document.createDocumentFragment(), pos = 0;
    candidates(text).forEach(function (c) {
      if (c.id === own || !(c.id in known)) return;
      if (c.at > pos) f.appendChild(document.createTextNode(text.slice(pos, c.at)));
      f.appendChild(make(c.id, known[c.id], !!nested));
      pos = c.at + c.id.length;
    });
    if (pos < text.length) f.appendChild(document.createTextNode(text.slice(pos)));
    return f;
  }

  function textNodes(root) {
    var out = [];
    if (root.nodeType === 3) { out.push(root); return out; }
    if (root.nodeType !== 1) return out;
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null), n;
    while ((n = w.nextNode())) out.push(n);
    return out;
  }

  function scan(root) {
    var work = [], want = [];
    textNodes(root).forEach(function (n) {
      if (n.nodeValue.length > 20000 || !/[A-Z]{2,10}-\d{4}/.test(n.nodeValue)) return;  /* cheap tests first: most text has no id */
      var a = allowed(n);
      if (!a) return;
      var c = candidates(n.nodeValue);
      if (!c.length) return;
      work.push({ node: n, nested: a.nested });
      c.forEach(function (x) { if (!(x.id in known) && want.indexOf(x.id) < 0) want.push(x.id); });
    });
    if (!work.length) return Promise.resolve();
    var ask = want.length ? fetch("/ids?q=" + encodeURIComponent(want.join(",")), { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j) want.forEach(function (id) { known[id] = j[id] || null; }); }, function () { /* no answer: the text stays as it is */ })
      : Promise.resolve();
    return ask.then(function () {
      work.forEach(function (w) {
        if (!w.node.parentNode) return;
        var f = fragment(w.node.nodeValue, w.nested, w.nested);
        if (f.querySelector && f.querySelector(".tid,.tid-missing")) w.node.parentNode.replaceChild(f, w.node);
      });
    });
  }

  /* the nodes added after load, a moment after the last one (a toast is built in several steps) */
  var queue = [], timer = null;
  function flush() { timer = null; var q = queue; queue = []; q.forEach(function (n) { if (document.contains(n)) scan(n); }); }
  if (window.MutationObserver) {
    new MutationObserver(function (list) {
      list.forEach(function (m) { for (var i = 0; i < m.addedNodes.length; i++) queue.push(m.addedNodes[i]); });
      if (queue.length && !timer) timer = setTimeout(flush, 40);
    }).observe(document.documentElement, { childList: true, subtree: true });
  }

  /* a span that stands for a link inside a whole-row link: click, middle click and Enter follow it */
  function go(e, el) {
    var href = el.getAttribute("data-href"); if (!href) return;
    e.preventDefault(); e.stopPropagation();
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button === 1) window.open(href, "_blank", "noopener"); else window.location.href = href;
  }
  document.addEventListener("click", function (e) { var el = e.target.closest && e.target.closest("span.tid[data-href]"); if (el) go(e, el); }, true);
  document.addEventListener("auxclick", function (e) { var el = e.target.closest && e.target.closest("span.tid[data-href]"); if (el && e.button === 1) go(e, el); }, true);
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Enter") return;
    var el = e.target.closest && e.target.closest("span.tid[data-href]"); if (el) go(e, el);
  }, true);

  window.deskIds = { scan: scan, link: function (text) { return fragment(String(text), false, ""); }, known: known };
})();
