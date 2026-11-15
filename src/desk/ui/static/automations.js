/* The Automations page (DESK-0292): Run now and Undo show where they were clicked which rule and which ticket of how many they are
   on (DeskProgress count mode), then one plain sentence, then the page reloads with the result and its Undo. Without script both
   forms post and redirect when done. Once DESK-0284's toast is on the page it offers the Undo, so the page's own button hides. */
(function () {
  "use strict";
  var POLL_MS = 400;
  var P = window.DeskProgress;
  if (window.deskToast) document.querySelectorAll("form.auto-undo").forEach(function (f) { f.hidden = true; });
  if (!P || !window.fetch || !window.FormData) return;

  function finish(p, j) {
    if (j.state === "done") {
      p.done(j.message);
      var q = new URLSearchParams({ msg: j.message }); if (j.undo) q.set("undo", j.undo);
      setTimeout(function () { location.assign("/automations?" + q.toString()); }, 900);
    } else {
      p.fail(j.message || "The run stopped without a result. Reload the page to see what changed.");
    }
  }

  function poll(p, id, btn) {
    fetch("/automations/job/" + encodeURIComponent(id), { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (j.state === "running") {
          if (j.total) p.count(j.done, j.total, j.step + " · ticket"); else p.step(0);
          setTimeout(function () { poll(p, id, btn); }, POLL_MS);
          return;
        }
        finish(p, j); btn.disabled = false;
      }, function () { p.fail("Could not reach Desk to follow the run. Reload the page to see what changed."); btn.disabled = false; });
  }

  document.querySelectorAll("form[data-job]").forEach(function (f) {
    f.addEventListener("submit", function (e) {
      e.preventDefault();
      var btn = f.querySelector("button[type=submit]"); if (btn.disabled) return;
      btn.disabled = true;
      var p = P.start(P.slotFor(btn), { steps: P.stepsOf(f) });
      fetch(f.getAttribute("action"), { method: "POST", body: new FormData(f), headers: { Accept: "application/json" }, credentials: "same-origin" })
        .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
        .then(function (x) {
          if (!x.ok || !x.j.job) { p.fail(x.j.error || "Desk did not start the run. Reload the page and try again."); return; }
          p.step(1); poll(p, x.j.job, btn);
        }, function () { p.fail("Could not reach Desk. Is drew-ui still running? Nothing was changed."); btn.disabled = false; });
    });
  });
})();
