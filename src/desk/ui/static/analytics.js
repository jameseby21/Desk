/* DESK-0336: the live block on /analytics. Every `data-every` seconds it asks /analytics/live.json for the last hour and redraws the
   list in place, so the page never reloads. A hidden tab does not poll; it catches up when it is shown again. Values are put in with
   textContent, never as HTML. */
(function () {
  var box = document.getElementById("live");
  if (!box) return;
  var url = box.getAttribute("data-live-url");
  var every = Math.max(5, parseInt(box.getAttribute("data-every") || "30", 10)) * 1000;
  var failed = false;

  function el(tag, text, cls) {
    var n = document.createElement(tag);
    if (text != null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }
  function idLink(id) {
    var a = el("a", id, "tid");
    a.href = "/ticket/" + encodeURIComponent(id);
    return a;
  }
  /* The server splits text into pieces and marks the ones that are tickets that exist, so only those become links. */
  function withLinks(parent, pieces) {
    pieces.forEach(function (p) {
      if (p.id) parent.appendChild(idLink(p.t)); else parent.appendChild(document.createTextNode(p.t));
    });
  }
  function set(id, text) { var n = document.getElementById(id); if (n) n.textContent = text; }

  function draw(d) {
    set("an-quiet", d.quiet);
    var ul = document.getElementById("an-events");
    ul.textContent = "";
    d.events.forEach(function (e) {
      var li = el("li");
      li.appendChild(el("time", e.time));
      li.appendChild(document.createTextNode(" "));
      li.appendChild(el("b", e.who));
      li.appendChild(document.createTextNode(" on "));
      li.appendChild(idLink(e.ticket));
      li.appendChild(document.createTextNode(": "));
      var w = el("span"); withLinks(w, e.parts); li.appendChild(w);
      ul.appendChild(li);
    });
    var more = document.getElementById("an-more");
    more.hidden = !d.more;
    more.textContent = "and " + d.more + " more in the last hour.";
    set("an-desk", d.desk);
    var c = document.getElementById("an-commit");
    c.textContent = "";
    if (d.store_commit) {
      c.appendChild(el("code", d.store_commit.sha));
      c.appendChild(document.createTextNode(" " + d.store_commit.when + " · " + d.store_commit.subject));
    } else c.textContent = "This store has no git history.";
    set("an-validate", d.validate_text);
    var cl = document.getElementById("an-commits");
    cl.textContent = "";
    d.commits.forEach(function (k) {
      var li = el("li");
      li.appendChild(el("code", k.sha));
      li.appendChild(document.createTextNode(" " + k.when + " · "));
      withLinks(li, k.parts);
      cl.appendChild(li);
    });
    set("an-upd", d.now.slice(11, 19));
    failed = false;
  }

  function poll() {
    if (document.hidden) return Promise.resolve();
    return fetch(url, { cache: "no-store", headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(draw)
      .catch(function (err) {
        failed = true;
        set("an-upd", "could not refresh at " + new Date().toTimeString().slice(0, 8) + " (" + err.message + "); trying again in " + (every / 1000) + " seconds");
      });
  }
  var timer = setInterval(poll, every);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
  window.addEventListener("pagehide", function () { clearInterval(timer); });
  window.DeskAnalytics = { poll: poll, failed: function () { return failed; } };
})();
