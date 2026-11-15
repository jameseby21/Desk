/* Capture (DESK-0286). Three ways a thought or a file becomes a record without opening a form:
   1. The palette (search.js, Ctrl K) asks window.DeskCapture.row() for a Create row under the results. "new: <sentence>" shows only
      that row; a sentence with no match picks it, so Enter creates. An optional "MVP slice?" line sits under it.
   2. A file dropped on any page opens a small capture box with the file; Create makes the ticket and attaches the file.
   3. A file dropped on a ticket page is attached to that ticket (any type within the size limit; programs and scripts refused).
   Each write comes back with the new address carrying undo=<batch> and msg=<sentence>, which the Undo toast (DESK-0284) reads.
   Progress: a bar with steps where the person acted, then one plain sentence (standards rule 10, Visible progress). */
(function () {
  "use strict";
  var REFUSED = [".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".scr", ".com", ".lnk"];
  var mvp = "", meta = null;
  var me = document.currentScript, ver = me && me.src.indexOf("?") > 0 ? me.src.slice(me.src.indexOf("?")) : "";
  if (!document.querySelector('link[href^="/static/capture.css"]')) {
    var l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/static/capture.css" + ver; document.head.appendChild(l);
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function sentence(q) { return String(q || "").replace(/^\s*new\s*:\s*/i, "").trim(); }
  /* The name the server saves the file under (capture.stored_name): "evil.exe." is saved as evil.exe, so it is checked as that too. */
  function stored(name) { return String(name || "").split(/[\\/]/).pop().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "") || "file"; }
  function refused(name) {
    var ns = [String(name || "").trim().toLowerCase(), stored(name).toLowerCase()];
    function ends(n, x) { return n.slice(-x.length) === x; }
    for (var i = 0; i < REFUSED.length; i++) if (ends(ns[0], REFUSED[i]) || ends(ns[1], REFUSED[i])) return "Desk does not take " + REFUSED[i] + " files: programs and scripts can run on this computer. Zip it first, or attach a screenshot or a note instead.";
    return "";
  }
  /* A one-time key plus the size limit, asked for when needed (a page on another site cannot read the answer). */
  function key() {
    return fetch("/capture/token.json", { headers: { Accept: "application/json" }, credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error("key"); return r.json(); }).then(function (j) { meta = j; return j; });
  }
  function bar(host) { return window.DeskProgress ? DeskProgress.start(host, { steps: ["Sending", "Saving", "Done"] }) : null; }
  function failText(res, err) {
    if (window.DeskProgress) return DeskProgress.failText(res, err);
    return (res && res.j && res.j.error) || "Desk could not save it. Try again.";
  }
  function mine(res) { if (res && res.ok && res.j && res.j.stamp && window.DeskLive) window.DeskLive.mine(res.j.stamp); }  // DESK-0282: our own write, so no auto-refresh
  function send(url, fields, host) {
    var p = bar(host);
    return key().then(function (k) {
      var fd = new FormData();
      Object.keys(fields).forEach(function (n) { fd.append(n, fields[n]); });
      fd.append("token", k.token);
      return DeskProgress.post(url, fd, { progress: p });
    }).then(function (res) {
      mine(res);
      if (res.ok && res.j && res.j.url) { if (p) p.done(res.j.message || "Saved."); setTimeout(function () { location.href = res.j.url; }, 250); return res; }
      if (p) p.fail(failText(res)); return res;
    }, function (err) { if (p) p.fail(failText(null, err)); });
  }

  /* -- 1. the palette's Create row ------------------------------------------------------------------------------------------- */
  function row(q, total) {
    var s = sentence(q);
    if (!s) return "";
    /* A short query with no match (a mistyped id) is not picked for Enter, which still opens the full results; Create is a click away. */
    var pickable = total || /^\s*new\s*:/i.test(q) || s.split(/\s+/).length >= 3;
    var html = '<a class="' + (pickable ? "pal-item " : "") + 'pal-create" role="option" data-capture="1" href="/capture?text=' + encodeURIComponent(s) + '">' +
      '<span class="chip ball">Create</span><span class="pal-title">' + esc(s) + '</span><span class="pal-snip muted">A new inbox ticket in Later (Now if it says urgent, now, today or asap). Desk guesses the project and type.</span></a>';
    if (!total) html += '<div class="pal-mvp"><label for="pal-mvp">MVP slice?</label><input id="pal-mvp" type="text" autocomplete="off" value="' + esc(mvp) + '" placeholder="Optional: the smallest version worth shipping"></div>';
    return html;
  }
  function create(q) {
    var s = sentence(q), host = document.getElementById("pal-status");
    if (!s) return;
    if (host) host.textContent = "";
    send("/capture", { text: s, mvp: mvp, how: "palette" }, host || document.body).then(function (res) { if (res && res.ok) mvp = ""; });
  }
  document.addEventListener("input", function (e) { if (e.target && e.target.id === "pal-mvp") mvp = e.target.value; });
  document.addEventListener("keydown", function (e) {
    if (e.target && e.target.id === "pal-mvp" && e.key === "Enter") { e.preventDefault(); var i = document.getElementById("pal-input"); create(i ? i.value : ""); }
  });
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("a.pal-create") : null;
    if (!a || e.ctrlKey || e.metaKey) return;
    e.preventDefault(); var i = document.getElementById("pal-input"); create(i ? i.value : "");
  });
  window.DeskCapture = { row: row, create: create, only: function (q) { return /^\s*new\s*:/i.test(q || ""); }, refused: refused };

  /* -- 2 and 3. files dropped on a page -------------------------------------------------------------------------------------- */
  var ticket = (location.pathname.match(/^\/ticket\/([A-Za-z][A-Za-z0-9]*-\d+)\/?$/) || [])[1] || "";
  var shade = null, depth = 0;
  function hasFiles(e) { return e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") >= 0; }
  function skip(e) { var t = e.target; return t && t.tagName === "INPUT" && t.type === "file"; }
  function overlay(inner, hint) {
    if (!shade) { shade = document.createElement("div"); shade.hidden = true; document.body.appendChild(shade); }
    shade.className = "cap-drop" + (hint ? " hint" : "");
    shade.innerHTML = '<div class="cap-drop-box" role="dialog" aria-modal="true" aria-label="Capture">' + inner + "</div>";
    shade.hidden = false; return shade;
  }
  function hide() { if (shade) { shade.hidden = true; shade.innerHTML = ""; } depth = 0; }
  document.addEventListener("dragenter", function (e) {
    if (!hasFiles(e) || skip(e) || (shade && !shade.hidden && shade.querySelector("form"))) return;
    depth++;
    if (depth === 1) overlay('<p class="cap-drop-hint">' + (ticket ? "Drop to attach it to " + esc(ticket) : "Drop to make a ticket around it") + "</p>", true);
  });
  document.addEventListener("dragleave", function (e) { if (!hasFiles(e) || !shade || shade.querySelector("form")) return; if (--depth <= 0) hide(); });
  document.addEventListener("dragover", function (e) { if (hasFiles(e) && !skip(e) && !e.defaultPrevented) e.preventDefault(); });
  document.addEventListener("drop", function (e) {
    if (!hasFiles(e) || skip(e)) return;
    if (e.defaultPrevented) { hide(); return; }  // the comment box (shots.js) took it as a screenshot
    e.preventDefault(); depth = 0;
    var files = Array.prototype.slice.call(e.dataTransfer.files || []);
    if (!files.length) { hide(); return; }
    if (ticket) attachAll(files); else captureFile(files[0], files.length);
  });
  function attachAll(files) {
    var box = overlay('<h2>Attaching to ' + esc(ticket) + '</h2><div class="dp-slot" id="cap-drop-slot"></div><p class="muted" id="cap-drop-msg" role="status"></p><div class="row"><button type="button" class="btn" id="cap-drop-close">Close</button></div>');
    box.querySelector("#cap-drop-close").onclick = hide;
    var msg = box.querySelector("#cap-drop-msg"), slot = box.querySelector("#cap-drop-slot"), i = 0, last = null, bad = [];
    var p = window.DeskProgress ? DeskProgress.start(slot, { steps: ["Sending", "Saving", "Done"] }) : null;
    function next() {
      if (i >= files.length) {
        if (last) { if (p) p.done(last.message + (bad.length ? " Not attached: " + bad.join(" ") : "")); setTimeout(function () { location.href = last.url; }, bad.length ? 2500 : 300); }
        else if (p) p.fail(bad.join(" ") || "Nothing was attached.");
        return;
      }
      var f = files[i++], why = refused(f.name);
      if (p) p.count(i, files.length, "files");
      if (why) { bad.push(f.name + ": " + why); return next(); }
      key().then(function (k) {
        var fd = new FormData(); fd.append("file", f, f.name); fd.append("token", k.token);
        return DeskProgress.post("/ticket/" + encodeURIComponent(ticket) + "/drop", fd, {});
      }).then(function (res) {
        mine(res);
        if (res.ok) last = { url: res.j.url, message: files.length > 1 ? "Attached " + (i - bad.length) + " of " + files.length + " files to " + ticket + "." : res.j.message };
        else bad.push(f.name + ": " + failText(res));
        next();
      }, function (err) { bad.push(f.name + ": " + failText(null, err)); next(); });
    }
    msg.textContent = files.length > 1 ? files.length + " files." : files[0].name;
    next();
  }
  function captureFile(f, n) {
    var why = refused(f.name);
    if (why) {
      var b = overlay('<h2>Not taken</h2><p role="alert">' + esc(why) + '</p><div class="row"><button type="button" class="btn" id="cap-drop-close">Close</button></div>');
      b.querySelector("#cap-drop-close").onclick = hide; b.querySelector("#cap-drop-close").focus(); return;
    }
    var box = overlay('<h2>Make a ticket around ' + esc(f.name) + '</h2>' + (n > 1 ? '<p class="muted">Only the first of the ' + n + ' files is used. Drop the others on the new ticket.</p>' : "") +
      '<form id="cap-drop-form"><label>What do you want done?<input type="text" id="cap-drop-text" autocomplete="off" value="' + esc("Look at " + f.name) + '"></label>' +
      '<label>MVP slice? <span class="muted">Optional: the smallest version worth shipping.</span><input type="text" id="cap-drop-mvp" autocomplete="off"></label>' +
      '<div class="row"><button class="btn ball" type="submit">Create ticket</button><button type="button" class="btn" id="cap-drop-close">Cancel</button></div><div class="dp-slot"></div></form>');
    var form = box.querySelector("form"), text = box.querySelector("#cap-drop-text");
    box.querySelector("#cap-drop-close").onclick = hide;
    text.focus(); text.select();
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      var slot = form.querySelector(".dp-slot"), p = bar(slot);
      key().then(function (k) {
        if (meta && meta.max_mb && f.size > meta.max_mb * 1048576) throw { size: true };
        var fd = new FormData(); fd.append("text", text.value); fd.append("mvp", box.querySelector("#cap-drop-mvp").value); fd.append("how", "file drop"); fd.append("file", f, f.name); fd.append("token", k.token);
        return DeskProgress.post("/capture", fd, { progress: p });
      }).then(function (res) {
        mine(res);
        if (res.ok) { if (p) p.done(res.j.message); setTimeout(function () { location.href = res.j.url; }, 300); }
        else if (p) p.fail(failText(res));
      }, function (err) {
        if (p) p.fail(err && err.size ? "That file is larger than the " + meta.max_mb + " MB limit (desk.toml [attachments] max_mb). Nothing was saved." : failText(null, err));
      });
    });
  }
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && shade && !shade.hidden) { e.preventDefault(); hide(); } });
})();
