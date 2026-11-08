/* One progress component for every form and action in Desk (DESK-0152), and the in-place posting that keeps the page where it is
   (DESK-0141). Rule (standards/rules/10-slice-contract.md, Visible progress): where a person clicked, show what is happening, how
   far along (a bar with named steps, or a count), the elapsed time after 2 s, then one plain sentence for success or failure.

   DeskProgress.start(host, {steps, msgEl, floating}) -> p     p.step(i or name)  p.count(n, total, noun)  p.done(sentence)  p.fail(sentence)
   DeskProgress.post(url, body, {progress, timeoutMs})        -> Promise of {ok, status, j}; the steps follow real XHR events:
        Sending = the request is going out, Saving = it arrived and the server is working, Done = the answer is back.
   Forms with data-inplace post through here; data-progress names their steps. Without script every form still posts and redirects
   back to its own anchor. */
(function () {
  "use strict";
  var DEFAULT_STEPS = ["Sending", "Saving", "Done"], ELAPSED_AFTER_MS = 2000, FADE_MS = 2500;

  function mk(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }

  function Progress(host, o) {
    o = o || {};
    this.steps = o.steps && o.steps.length ? o.steps : DEFAULT_STEPS;
    this.t0 = Date.now(); this.over = false;
    var old = host.querySelector(":scope > .dp"); if (old && old._p) old._p.dispose();
    var root = mk("span", "dp" + (o.floating ? " floating" : "")), bar = mk("span", "dp-bar"), fill = mk("i");
    bar.setAttribute("role", "progressbar"); bar.setAttribute("aria-valuemin", "0"); bar.setAttribute("aria-valuemax", "100"); bar.appendChild(fill);
    this.label = mk("span", "dp-step"); this.time = mk("span", "dp-time");
    root.appendChild(bar); root.appendChild(this.label); root.appendChild(this.time);
    this.msg = o.msgEl || null;
    if (!this.msg) { this.msg = mk("span", "dp-msg"); this.msg.setAttribute("role", "status"); this.msg.setAttribute("aria-live", "polite"); root.appendChild(this.msg); this.ownMsg = true; }
    root._p = this; this.root = root; this.bar = bar; this.fill = fill; this.host = host;
    host.appendChild(root);
    var self = this;
    this.tick = setInterval(function () { self.elapsed(); }, 500);
    this.step(0);
  }
  Progress.prototype.width = function (pct) { this.fill.style.width = Math.max(0, Math.min(100, pct)) + "%"; this.bar.setAttribute("aria-valuenow", String(Math.round(pct))); };
  Progress.prototype.elapsed = function () {
    var s = Date.now() - this.t0;
    this.time.textContent = !this.over && s >= ELAPSED_AFTER_MS ? "· " + Math.floor(s / 1000) + " s" : "";
  };
  /* A named step (or its index). The bar sits in the middle of the step's slice; Done is the last slice and only done() fills it. */
  Progress.prototype.step = function (which) {
    var i = typeof which === "number" ? which : this.steps.indexOf(which); if (i < 0) i = 0;
    this.index = i; this.root.setAttribute("data-step", this.steps[i]);
    this.label.textContent = this.steps[i] + "…"; this.width(((i + 0.5) / this.steps.length) * 100);
    this.elapsed();
  };
  /* Part-way through the current step, from a real number (bytes sent). */
  Progress.prototype.fraction = function (f) { this.width(((this.index + Math.max(0, Math.min(1, f))) / this.steps.length) * 100); };
  /* Count mode for an action over several items: "Moving 3 of 12". */
  Progress.prototype.count = function (n, total, noun) {
    this.root.setAttribute("data-step", "count"); this.label.textContent = (noun || "Item") + " " + n + " of " + total; this.width(total ? (n / total) * 100 : 0); this.elapsed();
  };
  Progress.prototype.stop = function () { clearInterval(this.tick); this.over = true; this.time.textContent = ""; };
  Progress.prototype.finish = function (cls, label, sentence) {
    this.stop(); this.root.classList.add(cls); this.root.setAttribute("data-step", cls === "ok" ? "done" : "failed");
    this.label.textContent = label; if (cls === "ok") this.width(100);
    if (this.msg) { this.msg.textContent = sentence || ""; if (this.msg.classList) { this.msg.classList.toggle("ok", cls === "ok"); this.msg.classList.toggle("bad", cls === "bad"); } }
    var self = this;
    clearTimeout(this.fade);  // the bar goes away after a moment; the sentence stays
    if (cls === "ok") this.fade = setTimeout(function () { self.root.classList.add("settled"); }, FADE_MS);
    return this;
  };
  Progress.prototype.done = function (sentence) { return this.finish("ok", this.steps[this.steps.length - 1], sentence); };
  Progress.prototype.fail = function (sentence) { return this.finish("bad", "Stopped", sentence); };
  Progress.prototype.dispose = function () { this.stop(); clearTimeout(this.fade); if (this.root.parentNode) this.root.parentNode.removeChild(this.root); };

  function start(host, o) { return new Progress(host, o); }

  /* The place for a form's progress: its own .dp-slot, or one made right after `el`. */
  function slotFor(el) {
    var f = el.closest ? el.closest("form") : null, s = (f || el.parentNode).querySelector(".dp-slot");
    if (!s) { s = mk("span", "dp-slot"); el.insertAdjacentElement("afterend", s); }
    return s;
  }
  /* A pill fixed to the bottom of the window, for actions whose own spot has no room (a check box in a list): it never moves the page. */
  function floater() {
    var s = document.getElementById("dp-float");
    if (!s) { s = mk("div", "dp-float"); s.id = "dp-float"; document.body.appendChild(s); }
    return s;
  }
  function stepsOf(form) { var v = form && form.getAttribute && form.getAttribute("data-progress"); return v && v !== "none" ? v.split(",").map(function (x) { return x.trim(); }) : null; }

  /* POST with the steps driven by real events. Resolves {ok, status, j} for any answer (j = {} when the body is not JSON); rejects with
     Error("timeout") or Error("network") when there was no answer. */
  function post(url, body, o) {
    o = o || {}; var p = o.progress;
    return new Promise(function (resolve, reject) {
      var x = new XMLHttpRequest(); x.open("POST", url); x.setRequestHeader("Accept", "application/json"); x.timeout = o.timeoutMs || 0;
      if (p) {
        x.upload.onprogress = function (e) { if (e.lengthComputable && e.total > 65536) p.fraction(e.loaded / e.total); };
        x.upload.onload = function () { p.step(1); };
      }
      x.onload = function () {
        var j = {}; try { j = JSON.parse(x.responseText); } catch (e) { j = {}; }
        resolve({ ok: x.status >= 200 && x.status < 300, status: x.status, j: j });
      };
      x.onerror = function () { reject(new Error("network")); };
      x.ontimeout = function () { reject(new Error("timeout")); };
      x.send(body);
    });
  }

  /* What to say when a post did not go through: the server's own sentence when it gave one, and what to do next. */
  function failText(x, err, kept) {
    var keep = kept ? " " + kept : " Nothing was changed.";
    if (err) return (err.message === "timeout" ? "Desk did not answer in time." : "Could not reach Desk. Is drew-ui still running?") + keep + " Try again.";
    if (x.j && x.j.error) return x.j.error + (x.status === 409 ? "" : keep);
    return "Desk hit an error and did not finish." + keep + " Try again; if it repeats, file the error page as a bug.";
  }

  /* Reload the part of the page that holds the ticket without moving the viewport: fetch the same address, swap <main>'s contents,
     keep the comment box (its draft and its one-time key) and the scroll position. */
  function softRefresh() {
    return fetch(location.pathname + location.search, { headers: { "Accept": "text/html" }, credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("refresh"); return r.text(); })
      .then(function (t) {
        var doc = new DOMParser().parseFromString(t, "text/html"), nm = doc.querySelector("main"), cm = document.querySelector("main");
        if (!nm || !cm) throw new Error("refresh");
        var y = window.pageYOffset, x = window.pageXOffset;
        nm.querySelectorAll("script").forEach(function (s) { s.remove(); });  // the scripts on the page are already running
        var keep = cm.querySelector("#composer"), slot = nm.querySelector("#composer");
        if (keep && slot) slot.replaceWith(keep);
        var notice = nm.querySelector(".notice"); if (notice) notice.remove();
        cm.replaceChildren.apply(cm, Array.prototype.slice.call(nm.childNodes));
        window.scrollTo(x, y);
        if (window.mermaid && document.querySelector(".mermaid")) { try { window.mermaid.run(); } catch (e) { /* diagrams stay as text */ } }
      });
  }

  /* Reload the whole page for auto-refresh and come back to the same place. */
  var KEY = "desk-scroll:";
  function reload() {
    try { sessionStorage.setItem(KEY + location.pathname + location.search, String(window.pageYOffset)); } catch (e) { /* private window */ }
    location.reload();
  }
  function restore() {
    try {
      var k = KEY + location.pathname + location.search, y = sessionStorage.getItem(k);
      if (y !== null) { sessionStorage.removeItem(k); if (!location.hash) window.scrollTo(0, parseInt(y, 10) || 0); }
    } catch (e) { /* nothing stored */ }
  }
  restore();

  /* Forms marked data-inplace post here. data-inplace="refresh" swaps the page's content afterwards (the ticket changed); "stay" only
     says the sentence (settings); "attach" puts the new image list into #attachments-body. The forms with their own script (the
     comment box, the check boxes) use DeskProgress.post and DeskProgress.start themselves. */
  document.addEventListener("submit", function (e) {
    var f = e.target, mode = f.getAttribute && f.getAttribute("data-inplace");
    if (!mode || !window.fetch || !window.XMLHttpRequest || !window.FormData) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (f.getAttribute("data-busy")) return;
    f.setAttribute("data-busy", "1");
    var fd = new FormData(f), sub = e.submitter; if (sub && sub.name) fd.set(sub.name, sub.value);
    var btns = f.querySelectorAll("button[type=submit],input[type=submit]"); btns.forEach(function (b) { b.disabled = true; });
    var p = start(slotFor(sub || f.querySelector("button") || f), { steps: stepsOf(f) });
    function end() { f.removeAttribute("data-busy"); btns.forEach(function (b) { b.disabled = false; }); }
    function token(j) { var t = f.querySelector("[name=token]"); if (t && j && j.token) t.value = j.token; }
    post(f.action, fd, { progress: p, timeoutMs: 30000 }).then(function (x) {
      token(x.j);
      if (!x.ok) { p.fail(failText(x)); return end(); }
      if (window.DeskLive) window.DeskLive.mine(x.j.stamp);
      var msg = x.j.message || "Done.";
      if (mode === "refresh") {
        p.step("Saving");
        softRefresh().then(function () {
          var nf = document.querySelector('form[action="' + f.getAttribute("action") + '"]'), host = nf ? slotFor(nf.querySelector("button") || nf) : floater();
          start(host).done(msg);
        }, function () { p.done(msg + " Reload the page to see it."); end(); });
        return;
      }
      if (mode === "attach") {
        var body = document.getElementById("attachments-body"), card = document.getElementById("attachments");
        if (body && x.j.attachments_html) { body.innerHTML = x.j.attachments_html; if (card) card.hidden = false; }
        var file = f.querySelector("input[type=file]"); if (file) file.value = "";
      }
      p.done(msg); end();
    }, function (err) { p.fail(failText(null, err)); end(); });
  }, true);

  window.DeskProgress = { start: start, post: post, slotFor: slotFor, floater: floater, stepsOf: stepsOf, failText: failText, softRefresh: softRefresh, reload: reload };
})();
