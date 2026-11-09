/* The New ticket form (DESK-0189). Posts through DeskProgress so a person sees Sending, Saving, Done and then one plain sentence;
   problems come back by field and are written under the box they belong to. Without script the form posts and the server draws the
   same messages, so nothing here is needed for the form to work. */
(function () {
  "use strict";
  var f = document.getElementById("newform");
  if (!f || !window.DeskProgress || !window.FormData) return;
  var box = document.getElementById("form-error");

  function show(errs) {
    f.querySelectorAll(".ferr").forEach(function (e) { e.textContent = ""; });
    var first = null;
    Object.keys(errs || {}).forEach(function (k) {
      var el = document.getElementById("err-" + k);
      if (el) { el.textContent = errs[k]; if (!first) first = document.getElementById("f-" + k); }
    });
    if (box) {
      box.hidden = false;
      box.textContent = errs && errs.form ? errs.form : "Not saved yet. Fix the lines marked below, then save again.";
    }
    if (first) first.focus();
  }

  f.addEventListener("submit", function (e) {
    e.preventDefault(); e.stopImmediatePropagation();
    if (f.getAttribute("data-busy")) return;
    f.setAttribute("data-busy", "1");
    var btn = document.getElementById("nf-save"); btn.disabled = true;
    if (box) box.hidden = true;
    var p = DeskProgress.start(DeskProgress.slotFor(btn), { steps: DeskProgress.stepsOf(f) });
    function end() { f.removeAttribute("data-busy"); btn.disabled = false; }
    DeskProgress.post(f.action, new FormData(f), { progress: p, timeoutMs: 30000 }).then(function (x) {
      var t = f.querySelector("[name=token]"); if (t && x.j && x.j.token) t.value = x.j.token;
      if (x.ok && x.j.url) {
        p.done((x.j.message || "Saved.") + " Opening it now.");
        location.href = x.j.url;  // the form stays locked: a second click cannot make a second ticket
        return;
      }
      p.fail(x.j && x.j.errors ? "Not saved. Fix the lines marked below." : DeskProgress.failText(x));
      show(x.j && x.j.errors ? x.j.errors : { form: DeskProgress.failText(x) });
      end();
    }, function (err) { p.fail(DeskProgress.failText(null, err)); end(); });
  }, true);
})();
