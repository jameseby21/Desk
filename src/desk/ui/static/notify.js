/* Popup notifications (DESK-0192). Every few seconds the page asks /notify/changes what changed since the last answer; the server has
   already left out tickets this person holds no role on (James and Patch hear about all). Each change is one line in a small stack
   at the bottom of the window: the ticket, who did what, a link, a dismiss button. Three show; older ones fold into "N earlier".
   "Notify me" in the header asks the browser once for permission to raise a system notification when this window is not in front.
   "Mute" hides every popup and every system notification. Both choices live in localStorage; the stack and the place in the feed live in
   sessionStorage, so the auto-refresh reload does not lose a popup. Nothing is sent anywhere but this Desk. */
(function () {
  "use strict";
  var MUTE = "desk-notify-mute", SYS = "desk-notify-system", CURSOR = "desk-notify-cursor", STACK = "desk-notify-stack", MAX = 3, EVERY = window.DeskNotifyEvery || 3000;
  var layer = null, items = [], cursor = null, timer = null;

  function get(store, key) { try { return window[store].getItem(key); } catch (e) { return null; } }
  function put(store, key, val) { try { if (val === null) window[store].removeItem(key); else window[store].setItem(key, val); } catch (e) { /* private window: the choice lasts until the page closes */ } }
  function muted() { return get("localStorage", MUTE) === "1"; }
  function systemOn() { return get("localStorage", SYS) === "1" && !!window.Notification && Notification.permission === "granted"; }

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }

  function save() { put("sessionStorage", STACK, JSON.stringify(items.slice(-30))); }

  function render() {
    if (!layer) return;
    layer.replaceChildren();
    var shown = items.slice(-MAX), older = items.length - shown.length;
    if (older > 0) {
      var fold = el("div", "toast fold"); fold.appendChild(el("span", "", older + " earlier change" + (older === 1 ? "" : "s")));
      var clear = el("button", "toast-x", "Clear all"); clear.type = "button"; clear.addEventListener("click", function () { items = []; save(); render(); });
      fold.appendChild(clear); layer.appendChild(fold);
    }
    shown.forEach(function (it) {
      var box = el("div", "toast"), a = el("a", "", ""), id = el("b", "", it.id);
      a.href = it.href; a.appendChild(id); a.appendChild(document.createTextNode(" " + it.title));
      box.appendChild(a); box.appendChild(el("span", "toast-what", it.who + " " + it.what));
      var x = el("button", "toast-x", "Dismiss"); x.type = "button"; x.setAttribute("aria-label", "Dismiss " + it.id);
      x.addEventListener("click", function () { items = items.filter(function (o) { return o.seq !== it.seq; }); save(); render(); });
      box.appendChild(x); layer.appendChild(box);
    });
  }

  function system(it) {
    if (!systemOn() || document.hasFocus()) return;  // a window in front already shows the popup
    try { new Notification(it.id + " " + it.title, { body: it.who + " " + it.what, tag: it.id }); } catch (e) { /* the browser refused; the popup still shows */ }
  }

  function take(list) {
    if (muted()) return;  // muted: nothing shows and nothing is raised
    list.forEach(function (it) {
      if (items.some(function (o) { return o.seq === it.seq; })) return;
      items.push(it); system(it);
    });
    save(); render();
  }

  function poll() {
    var url = "/notify/changes" + (cursor === null ? "" : "?since=" + cursor);
    return fetch(url, { headers: { "Accept": "application/json" }, credentials: "same-origin", cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("no"); return r.json(); })
      .then(function (j) {
        cursor = j.cursor; put("sessionStorage", CURSOR, String(cursor));
        if (j.items && j.items.length) take(j.items);
      }, function () { /* Desk is restarting or offline: try again at the next look */ });
  }

  function labels() {
    var allow = document.getElementById("notify-allow"), mute = document.getElementById("notify-mute");
    if (allow) {
      allow.hidden = !window.Notification;
      allow.textContent = systemOn() ? "Alerts on" : (window.Notification && Notification.permission === "denied" ? "Alerts blocked" : "Notify me");
      allow.title = systemOn() ? "Desk raises a system notification when this window is not in front. Click to turn off."
        : "Click once to let Desk raise a system notification when this window is not in front.";
      allow.setAttribute("aria-pressed", systemOn() ? "true" : "false");
    }
    if (mute) { mute.textContent = muted() ? "Unmute" : "Mute"; mute.setAttribute("aria-pressed", muted() ? "true" : "false"); mute.title = muted() ? "Popups are muted. Click to show them again." : "Hide every popup and system notification."; }
  }

  function wire() {
    var allow = document.getElementById("notify-allow"), mute = document.getElementById("notify-mute");
    if (allow) allow.addEventListener("click", function () {
      if (systemOn()) { put("localStorage", SYS, null); labels(); return; }
      if (!window.Notification) return;
      var done = function (p) { if (p === "granted") put("localStorage", SYS, "1"); labels(); };
      try { var r = Notification.requestPermission(done); if (r && r.then) r.then(done); } catch (e) { labels(); }
    });
    if (mute) mute.addEventListener("click", function () {
      put("localStorage", MUTE, muted() ? null : "1");
      if (muted()) { items = []; save(); render(); }
      labels();
    });
  }

  function start() {
    layer = document.getElementById("toasts");
    if (!layer) { layer = el("div", "toasts"); layer.id = "toasts"; layer.setAttribute("role", "status"); layer.setAttribute("aria-live", "polite"); document.body.appendChild(layer); }
    try { items = JSON.parse(get("sessionStorage", STACK) || "[]") || []; } catch (e) { items = []; }
    if (muted()) items = [];
    var c = get("sessionStorage", CURSOR); cursor = c !== null && /^\d+$/.test(c) ? parseInt(c, 10) : null;
    wire(); labels(); render();
    poll(); timer = setInterval(poll, EVERY);
    window.addEventListener("pagehide", function () { clearInterval(timer); });
  }

  window.DeskNotify = { poll: poll, labels: labels, items: function () { return items.slice(); } };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
