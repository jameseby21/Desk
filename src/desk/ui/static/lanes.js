/* DESK-0283: the lane chip on rows and tiles opens a small menu (lane, park with a reason, snooze). Each choice is one write in one
   batch; the answer goes to the undo toast (DESK-0284, window.deskToast) or, without it, to a status line in the menu.
   Keyboard: Enter or Space on a chip opens the menu, arrow keys move, Escape closes and returns focus to the chip. */
(function () {
  "use strict";
  var menu = null, chipEl = null;

  function mk(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }

  function close() {
    if (!menu) return;
    menu.remove(); menu = null;
    if (chipEl) { chipEl.setAttribute("aria-expanded", "false"); chipEl.focus(); }
    chipEl = null;
  }

  function say(text, bad) {
    var line = menu && menu.querySelector(".lm-msg");
    if (line) { line.textContent = text; line.classList.toggle("bad", !!bad); }
  }

  function post(id, data, done) {
    var body = new URLSearchParams(data);
    var bar = menu && menu.querySelector(".lm-bar"); if (bar) bar.hidden = false;
    say("Saving...");
    fetch("/ticket/" + encodeURIComponent(id) + "/lane", { method: "POST", headers: { "Accept": "application/json" }, body: body, credentials: "same-origin" })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (bar) bar.hidden = true;
        if (!res.ok) { if (res.j.token && menu) menu.dataset.token = res.j.token; say(res.j.error || "Not saved. Try again.", true); return; }
        done(res.j);
      })
      .catch(function () { if (bar) bar.hidden = true; say("Desk did not answer, so nothing was saved. Check that it is running, then try again.", true); });
  }

  function apply(j) {
    document.querySelectorAll('.lane-chip[data-lane-id="' + j.id + '"]').forEach(function (c) {
      c.textContent = j.label;
      c.className = "lane-chip lane-" + j.lane + (j.set ? "" : " derived");
    });
    if (window.deskToast) { window.deskToast({ text: j.message, batch: j.batch }); close(); }
    else { say(j.message); setTimeout(close, 1800); }
  }

  function open(chip) {
    close();
    chipEl = chip;
    var id = chip.getAttribute("data-lane-id");
    menu = mk("div", "lane-pop"); menu.setAttribute("role", "menu"); menu.setAttribute("aria-label", "Lane for " + id);
    menu.appendChild(mk("span", "lm-msg", "Loading..."));
    var r = chip.getBoundingClientRect();
    menu.style.top = (window.scrollY + r.bottom + 6) + "px";
    menu.style.left = Math.max(8, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - 260)) + "px";
    document.body.appendChild(menu);
    chip.setAttribute("aria-expanded", "true");
    fetch("/ticket/" + encodeURIComponent(id) + "/lane", { headers: { "Accept": "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.json(); })
      .then(function (j) { if (menu && chipEl === chip) build(id, j); })
      .catch(function () { say("Desk did not answer. Check that it is running.", true); });
  }

  function item(text, cls, fn) {
    var b = mk("button", "lm-item " + (cls || ""), text); b.type = "button"; b.setAttribute("role", "menuitem");
    b.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); fn(); });
    return b;
  }

  function build(id, j) {
    menu.textContent = "";
    menu.dataset.token = j.token;
    var tok = function () { return menu.dataset.token; };
    menu.appendChild(mk("span", "lm-head", "Lane"));
    j.lanes.forEach(function (l) {
      if (l[0] === "parked") return;
      var b = item(l[1], "lane-" + l[0], function () { post(id, { token: tok(), lane: l[0] }, apply); });
      if (j.set && j.lane === l[0]) b.setAttribute("aria-checked", "true");
      menu.appendChild(b);
    });
    var park = mk("div", "lm-park");
    var inp = mk("input"); inp.type = "text"; inp.maxLength = 200; inp.placeholder = "Why not now? One line"; inp.value = j.reason || "";
    inp.setAttribute("aria-label", "Why park it");
    var pb = item("Park", "lane-parked", function () {
      if (!inp.value.trim()) { say("Write one line on why, then press Park.", true); inp.focus(); return; }
      post(id, { token: tok(), lane: "parked", reason: inp.value }, apply);
    });
    inp.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); pb.click(); } });
    park.appendChild(inp); park.appendChild(pb); menu.appendChild(park);
    menu.appendChild(mk("span", "lm-head", j.snoozed ? "Snoozed until " + j.until : "Snooze"));
    j.snooze.forEach(function (s) { menu.appendChild(item(s[1], "", function () { post(id, { token: tok(), snooze: s[0] }, apply); })); });
    if (j.snoozed) menu.appendChild(item("Wake it now", "", function () { post(id, { token: tok(), snooze: "clear" }, apply); }));
    var bar = mk("span", "lm-bar"); bar.hidden = true; bar.setAttribute("role", "progressbar"); bar.setAttribute("aria-label", "Saving"); bar.appendChild(mk("i"));
    menu.appendChild(bar);
    var msg = mk("span", "lm-msg"); msg.setAttribute("role", "status"); msg.setAttribute("aria-live", "polite"); menu.appendChild(msg);
    var first = menu.querySelector(".lm-item"); if (first) first.focus();
  }

  function isChip(t) { return t && t.closest && t.closest(".lane-chip[data-lane-id]"); }

  document.addEventListener("click", function (e) {
    var c = isChip(e.target);
    if (c) { e.preventDefault(); e.stopPropagation(); if (chipEl === c) close(); else open(c); return; }
    if (menu && !menu.contains(e.target)) close();
  }, true);

  document.addEventListener("keydown", function (e) {
    var c = isChip(e.target);
    if (c && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); e.stopPropagation(); open(c); return; }
    if (!menu) return;
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      var items = Array.prototype.slice.call(menu.querySelectorAll(".lm-item, input"));
      var i = items.indexOf(document.activeElement);
      if (i < 0 && !menu.contains(document.activeElement)) return;
      e.preventDefault();
      items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length].focus();
    }
  }, true);

  /* After a plain form post the page comes back with ?msg=; when the undo toast (DESK-0284) is not on this page, say it here. */
  window.addEventListener("load", function () {
    var q = new URLSearchParams(location.search), m = q.get("msg");
    if (!m || window.deskToast) return;
    var main = document.querySelector("main"); if (!main) return;
    var n = mk("div", "notice lane-notice", m); n.setAttribute("role", "status");
    main.insertBefore(n, main.firstChild);
  });
})();
