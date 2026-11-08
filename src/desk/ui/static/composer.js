/* Comment composer (DESK-0122 and after). One key per post (the form's hidden token), a first click that disables the button and
   locks the text, and a reply that puts the new comment on the page without a reload. Without fetch the plain form post works. */
(function () {
  "use strict";
  var form = document.getElementById("composer");
  if (!form || !window.fetch) return;
  var ta = form.querySelector("textarea[name=text]"), btn = form.querySelector(".post-btn"), status = document.getElementById("post-status");
  var list = document.querySelector("[data-comments]"), key = form.querySelector("[name=token]");
  var idle = btn.textContent.trim(), posting = form.getAttribute("data-posting") || "Posting…";
  var busy = false;

  function say(msg, bad) { status.textContent = msg || ""; status.className = "post-status" + (bad ? " bad" : ""); }
  function lock(on) {
    busy = on; ta.readOnly = on; btn.disabled = on; btn.setAttribute("aria-disabled", on ? "true" : "false");
    btn.textContent = on ? posting : idle;
    if (on) { var sp = document.createElement("span"); sp.className = "spin"; sp.setAttribute("aria-hidden", "true"); btn.insertBefore(sp, btn.firstChild); }
  }
  function send() {
    if (busy) return;  // the first click did it; more clicks while it is out do nothing
    if (!ta.value.trim()) { say("Write something first.", true); ta.focus(); return; }
    var fd = new FormData(form);
    lock(true); say(posting);
    fetch(form.action, { method: "POST", body: fd, headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }, function () { return { ok: false, j: {} }; }); })
      .then(function (x) {
        lock(false);
        if (!x.ok) { say(x.j.error || "Couldn't post.", true); return; }
        list.innerHTML = x.j.html; key.value = x.j.token;
        if (window.DeskLive) window.DeskLive.mine(x.j.stamp);
        ta.value = "";
        say(x.j.duplicate ? "That comment is already on the ticket." : "Posted");
      })
      .catch(function () { lock(false); say("Couldn't post.", true); });
  }
  form.addEventListener("submit", function (e) { e.preventDefault(); e.stopImmediatePropagation(); send(); }, true);
})();
