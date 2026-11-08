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

  var FAILED = "Couldn't post: Desk didn't answer. Your text is still here.";
  function say(msg, kind) { status.textContent = msg || ""; status.className = "post-status" + (kind ? " " + kind : ""); form.classList.toggle("failed", kind === "bad"); }
  function lock(on) {
    busy = on; ta.readOnly = on; btn.disabled = on; btn.setAttribute("aria-disabled", on ? "true" : "false");
    btn.textContent = on ? posting : idle;
    if (on) { var sp = document.createElement("span"); sp.className = "spin"; sp.setAttribute("aria-hidden", "true"); btn.insertBefore(sp, btn.firstChild); }
  }
  /* A post ends in one of two visible ways: Posted (the comment is on the page, highlighted for 2 s, the box is empty), or a failure
     sentence with the draft untouched and the button reading Try again. No answer within data-timeout-ms (10 s) is a failure, and
     because the key is unchanged, Try again returns the comment if Desk did store it after all. */
  function fail(msg) { lock(false); idle = "Try again"; btn.textContent = idle; say(msg || FAILED, "bad"); }
  function send() {
    if (busy) return;  // the first click did it; more clicks while it is out do nothing
    if (!ta.value.trim()) { say("Write something first.", "bad"); ta.focus(); return; }
    var fd = new FormData(form), ms = parseInt(form.getAttribute("data-timeout-ms"), 10) || 10000;
    var ctl = window.AbortController ? new AbortController() : null, timer = setTimeout(function () { if (ctl) ctl.abort(); }, ms);
    lock(true); say(posting);
    fetch(form.action, { method: "POST", body: fd, headers: { "Accept": "application/json" }, signal: ctl ? ctl.signal : undefined })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }, function () { return { ok: false, j: {} }; }); })
      .then(function (x) {
        clearTimeout(timer);
        if (x.j.token) key.value = x.j.token;  // a refused key (out of date) comes with a fresh one
        if (!x.ok) { fail(x.j.error ? "Couldn't post: " + x.j.error + " Your text is still here." : FAILED); return; }
        lock(false); idle = "Post"; btn.textContent = idle;
        list.innerHTML = x.j.html;
        if (window.DeskLive) window.DeskLive.mine(x.j.stamp);
        var item = list.querySelectorAll("li")[x.j.index];
        if (item) { item.classList.add("fresh"); setTimeout(function () { item.classList.remove("fresh"); }, 2000); }
        ta.value = "";
        say(x.j.duplicate ? "That comment is already on the ticket." : "Posted", "ok");
      })
      .catch(function () { clearTimeout(timer); fail(FAILED); });
  }
  form.addEventListener("submit", function (e) { e.preventDefault(); e.stopImmediatePropagation(); send(); }, true);
})();
