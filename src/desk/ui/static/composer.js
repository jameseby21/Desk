/* Comment composer (DESK-0122 to DESK-0124). One key per post (the form's hidden token), a first click that disables the button and
   locks the text, a reply that puts the new comment on the page without a reload, and screenshots as evidence (static/shots.js).
   Without fetch the plain form post works. */
(function () {
  "use strict";
  var form = document.getElementById("composer");
  var P = window.DeskProgress;
  if (!form || !window.fetch || !P) return;
  var ta = form.querySelector("textarea[name=text]"), btn = form.querySelector(".post-btn"), status = document.getElementById("post-status");
  var list = document.querySelector("[data-comments]"), key = form.querySelector("[name=token]");
  var strip = document.getElementById("shots"), evline = document.getElementById("evline"), pick = form.querySelector("select[name=line]");
  var note = document.getElementById("attach-note");
  var idle = btn.textContent.trim(), posting = form.getAttribute("data-posting") || "Posting…";
  var FAILED = "Couldn't post: Desk didn't answer. Your text is still here.";
  var busy = false, shots = [], slot = form.querySelector(".dp-slot"), prog = null;

  function say(msg, kind) { status.textContent = msg || ""; status.className = "post-status" + (kind ? " " + kind : ""); form.classList.toggle("failed", kind === "bad"); }
  function tell(msg, bad) { if (note) { note.textContent = msg || ""; note.className = "attach-note" + (bad ? " err" : ""); } }
  function lock(on) {
    busy = on; ta.readOnly = on; btn.disabled = on; btn.setAttribute("aria-disabled", on ? "true" : "false");
    btn.textContent = on ? posting : idle;
    if (on) { var sp = document.createElement("span"); sp.className = "spin"; sp.setAttribute("aria-hidden", "true"); btn.insertBefore(sp, btn.firstChild); }
  }

  /* Screenshots wait here, already saved with the ticket, until the post goes through; a failed post keeps them. */
  function drawShots() {
    strip.textContent = "";
    shots.forEach(function (s) {
      strip.appendChild(DeskShots.thumb(s, function (shot) {
        DeskShots.remove(ta, shot.name).then(function () { shots = shots.filter(function (x) { return x !== shot; }); drawShots(); tell(""); },
                                              function (err) { tell(err.message, true); });
      }));
    });
    strip.hidden = !shots.length; if (evline) evline.hidden = !shots.length;
    if (!shots.length && pick) pick.value = "";
  }
  DeskShots.watch("#comment-text", function (el, shot) { shots.push(shot); drawShots(); tell("Attached " + shot.name + ". It is saved with the ticket when you post."); },
                  function (el, msg) { tell(msg, true); });

  /* A post ends in one of two visible ways: Posted (the comment is on the page, highlighted for 2 s, the box is empty), or a failure
     sentence with the draft and screenshots untouched and the button reading Try again. No answer within data-timeout-ms (10 s) is a
     failure, and because the key is unchanged, Try again returns the comment if Desk did store it after all. */
  function fail(msg) { if (prog) prog.fail(msg || FAILED); lock(false); idle = "Try again"; btn.textContent = idle; say(msg || FAILED, "bad"); }
  function send() {
    if (busy) return;  // the first click did it; more clicks while it is out do nothing
    if (!ta.value.trim() && !shots.length) { say("Write something first.", "bad"); ta.focus(); return; }
    var fd = new FormData(form), ms = parseInt(form.getAttribute("data-timeout-ms"), 10) || 10000;
    shots.forEach(function (s) { fd.append("evidence", s.name); });
    lock(true); say(posting);
    prog = P.start(slot, { steps: P.stepsOf(form), msgEl: document.createElement("span") });  // the sentence is the status line; the bar is this component's
    P.post(form.action, fd, { progress: prog, timeoutMs: ms })
      .then(function (x) {
        if (x.j.token) key.value = x.j.token;  // a refused key (out of date) comes with a fresh one
        if (!x.ok) { fail(x.j.error ? "Couldn't post: " + x.j.error + " Your text is still here." : FAILED); return; }
        prog.done(); lock(false); idle = "Post"; btn.textContent = idle;
        list.innerHTML = x.j.html;
        if (x.j.check_html) {  // the post also put evidence on an Acceptance line
          var host = document.querySelector('[data-check-section="Acceptance"]'), sum = document.getElementById("chk-summary");
          if (host) host.innerHTML = x.j.check_html; if (sum) sum.innerHTML = x.j.summary_html;
        }
        if (window.DeskLive) window.DeskLive.mine(x.j.stamp);
        var item = list.querySelectorAll("li")[x.j.index];
        if (item) { item.classList.add("fresh"); setTimeout(function () { item.classList.remove("fresh"); }, 2000); }
        ta.value = ""; shots = []; drawShots(); tell("");
        say(x.j.duplicate ? (x.j.message || "That comment is already on the ticket") + "." : "Posted", "ok");
      })
      .catch(function () { fail(FAILED); });
  }
  /* Ctrl Enter (Cmd Enter on a Mac) posts through the same single-shot path as the button. Plain Enter is a new line. */
  form.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && /^(TEXTAREA|INPUT)$/.test((e.target.tagName || ""))) { e.preventDefault(); send(); }
  });
  form.addEventListener("submit", function (e) { e.preventDefault(); e.stopImmediatePropagation(); send(); }, true);
})();
