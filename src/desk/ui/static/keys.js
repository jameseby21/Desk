/* Keys (DESK-0290): the global key map, the shortcut sheet, recently viewed tickets, unsent drafts and the theme preview.
   - ? opens a one-screen sheet of every key; g then t, b, n, d, r or u moves between pages; / opens search (search.js).
   - No key fires while you type in a field. On /decide its own single keys (Y, N, S, E, J, K, Space) stay Decide mode's: this map
     only takes ?, g and the key right after g.
   - The last 10 tickets opened on this device are kept in localStorage for the palette (search.js lists them when it is empty).
   - Text typed into a ticket's comment or answer box is kept in localStorage per ticket and field, comes back after a reload, and is
     cleared when the post goes through (progress.js and composer.js send a `desk:sent` event). */
(function () {
  "use strict";
  var GO = [["t", "/", "Today"], ["b", "/board", "Board"], ["n", "/board?needs=1", "Needs me"], ["d", "/decide", "Decide mode"],
            ["r", "/brief", "Daily brief"], ["u", "/undo", "Undo log"]];
  var CHORD_MS = 1500;
  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function put(k, v) { try { if (v == null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (e) { /* storage off: nothing kept */ } }
  function typing(el) { return !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable); }
  function mk(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  /* -- the shortcut sheet ------------------------------------------------------------------------------------------------ */
  var sheet = null, back = null;
  function row(dl, keys, what) {
    var dt = mk("dt"); keys.forEach(function (k, i) { if (i) dt.appendChild(document.createTextNode(" then ")); dt.appendChild(mk("kbd", "k", k)); });
    dl.appendChild(dt); dl.appendChild(mk("dd", "", what));
  }
  function buildSheet() {
    sheet = mk("div", "ks"); sheet.id = "keys-sheet"; sheet.hidden = true;
    sheet.setAttribute("role", "dialog"); sheet.setAttribute("aria-modal", "true"); sheet.setAttribute("aria-labelledby", "keys-title");
    var b = mk("div", "ks-box"), h = mk("h2", "", "Keyboard shortcuts"); h.id = "keys-title"; b.appendChild(h);
    b.appendChild(mk("h3", "", "Anywhere"));
    var dl = mk("dl");
    row(dl, ["?"], "This sheet. Esc closes it.");
    row(dl, ["/"], "Search tickets, docs and intake.");
    row(dl, ["Ctrl K"], "Search, or act on the open ticket.");
    GO.forEach(function (g) { row(dl, ["g", g[0]], "Go to " + g[2] + "."); });
    b.appendChild(dl);
    b.appendChild(mk("h3", "", "In the search box"));
    dl = mk("dl");
    row(dl, ["Up", "Down"], "Move through the list.");
    row(dl, ["Enter"], "Open it, or run the action.");
    row(dl, ["Ctrl Enter"], "Open in a new tab.");
    row(dl, ["Esc"], "Close, or back from a words box.");
    b.appendChild(dl);
    b.appendChild(mk("h3", "", "On a ticket"));
    dl = mk("dl");
    row(dl, ["Ctrl K"], "Approve, Send back, Snooze, Move to Now or Later, Assign.");
    row(dl, ["j"], "Next ticket in the walk; k goes back.");
    row(dl, ["Ctrl Enter"], "Post the comment you are writing.");
    b.appendChild(dl);
    var p = mk("p", "muted", "Keys never fire while you type in a box. In Decide mode its own keys come first.");
    b.appendChild(p);
    sheet.appendChild(b);
    sheet.addEventListener("mousedown", function (e) { if (e.target === sheet) closeSheet(); });
    document.body.appendChild(sheet);
  }
  function openSheet() { if (!sheet) buildSheet(); back = document.activeElement; sheet.hidden = false; sheet.querySelector(".ks-box").setAttribute("tabindex", "-1"); sheet.querySelector(".ks-box").focus(); }
  function closeSheet() { if (sheet && !sheet.hidden) { sheet.hidden = true; if (back && back.focus && document.contains(back)) back.focus(); back = null; } }

  /* -- g chords ---------------------------------------------------------------------------------------------------------- */
  var pending = false, timer = null, hint = null;
  function endChord() { pending = false; clearTimeout(timer); if (hint) hint.hidden = true; }
  function startChord() {
    pending = true; clearTimeout(timer); timer = setTimeout(endChord, CHORD_MS);
    if (!hint) { hint = mk("div", "ks-chord"); hint.setAttribute("role", "status"); document.body.appendChild(hint); }
    hint.textContent = "g: then " + GO.map(function (g) { return g[0] + " " + g[2]; }).join(", ") + ".";
    hint.hidden = false;
  }
  document.addEventListener("keydown", function (e) {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || e.isComposing) return;
    if (typing(e.target) || document.body.classList.contains("pal-open")) { endChord(); return; }
    var k = e.key || "";
    if (sheet && !sheet.hidden) {
      if (k === "Escape" || k === "?") { e.preventDefault(); e.stopImmediatePropagation(); closeSheet(); }
      return;
    }
    if (pending) {
      var g = GO.filter(function (x) { return x[0] === k.toLowerCase(); })[0];
      endChord();
      if (g) { e.preventDefault(); e.stopImmediatePropagation(); location.href = g[1]; }
      return;
    }
    if (k === "?") { e.preventDefault(); openSheet(); return; }
    if (k === "g" && !e.shiftKey) { e.preventDefault(); startChord(); }
  }, true);  // capture: the key after g is the chord's, before a page's own single-key handler sees it

  /* -- recently viewed --------------------------------------------------------------------------------------------------- */
  var tm = /^\/ticket\/([A-Za-z][A-Za-z0-9]*-\d+)\/?$/.exec(location.pathname);
  if (tm) {
    var h1 = document.querySelector("main h1"), id = tm[1].toUpperCase(), list = [];
    try { list = JSON.parse(get("desk:recent") || "[]") || []; } catch (e) { list = []; }
    list = [{ id: id, title: h1 ? h1.textContent.trim().slice(0, 140) : "" }].concat(list.filter(function (x) { return x && x.id !== id; })).slice(0, 10);
    put("desk:recent", JSON.stringify(list));
  }

  /* -- unsent drafts ----------------------------------------------------------------------------------------------------- */
  function draftable(el) {
    var f = el && el.form;
    return !!f && /^\/ticket\//.test(f.getAttribute("action") || "") && (el.tagName === "TEXTAREA" || (el.type === "text" && el.name === "words"));
  }
  function keyOf(el) {
    var f = el.form, action = f.getAttribute("action"), same = Array.prototype.filter.call(document.forms, function (x) { return x.getAttribute("action") === action; });
    return "desk:draft:" + action + ":" + (f.id || same.indexOf(f)) + ":" + el.name;
  }
  var saveTimer = null;
  document.addEventListener("input", function (e) {
    var el = e.target;
    if (!draftable(el)) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () { put(keyOf(el), el.value.trim() ? el.value : null); }, 250);
  });
  document.addEventListener("desk:sent", function (e) {
    var f = e.target;
    if (!f || !f.elements) return;
    clearTimeout(saveTimer);
    Array.prototype.forEach.call(f.elements, function (el) { if (draftable(el)) put(keyOf(el), null); });
  });
  function restore() {
    Array.prototype.forEach.call(document.querySelectorAll("form textarea, form input[type=text][name=words]"), function (el) {
      if (!draftable(el) || el.value.trim()) return;
      var v = get(keyOf(el));
      if (!v) return;
      el.value = v;
      var d = el.closest("details"); if (d) d.open = true;  // the comment box sits in a fold; open it so the kept text is seen
      var note = mk("span", "muted draft-note", "Your unsent text from before is back.");
      note.style.fontSize = "12.5px";
      el.insertAdjacentElement("afterend", note);
    });
  }
  restore();

  /* -- saved views: Save and Remove post in place --------------------------------------------------------------------------
     The bar and the sentence appear where you clicked (DeskProgress), and an Undo (Put it back, for a removal) posts what the server
     handed back to reverse it. Without script the forms still post and redirect with the sentence in the address. */
  function undoButton(p, host, undo) {
    var b = mk("button", "btn small sv-undo", undo.label || "Undo"); b.type = "button";
    b.addEventListener("click", function () {
      var P = window.DeskProgress, fd = new FormData(), q = P.start(host, { steps: ["Sending", "Saving", "Done"] });
      Object.keys(undo.fields).forEach(function (k) { fd.append(k, undo.fields[k]); });
      P.post(undo.url, fd, { progress: q, timeoutMs: 15000 }).then(function (x) {
        if (!x.ok) { q.fail(P.failText(x), P.failDetail(x)); return; }
        q.done("Undone. " + (x.j.message || "")); setTimeout(function () { P.reload(); }, 1200);
      }, function (err) { q.fail(P.failText(null, err), P.failDetail(null, err)); });
    });
    p.root.appendChild(b);
  }
  document.addEventListener("submit", function (e) {
    var f = e.target, P = window.DeskProgress;
    if (!f.matches || !f.matches("form.sv-form, form.sv-remove") || !P || !window.FormData) return;
    e.preventDefault();
    var btn = f.querySelector("button[type=submit]");
    if (btn && btn.disabled) return;
    var host = P.slotFor(btn || f), p = P.start(host, { steps: ["Sending", "Saving", "Done"] });
    if (btn) btn.disabled = true;
    P.post(f.getAttribute("action"), new FormData(f), { progress: p, timeoutMs: 15000 }).then(function (x) {
      var t = f.querySelector("[name=token]"); if (t && x.j && x.j.token) t.value = x.j.token;  // the next post needs a fresh one
      if (btn) btn.disabled = false;
      if (!x.ok) { p.fail(P.failText(x), P.failDetail(x)); return; }
      p.done(x.j.message || "Saved.");
      if (x.j.undo) undoButton(p, host, x.j.undo);
      var li = f.closest("li"); if (li && f.matches(".sv-remove")) { li.classList.add("sv-gone"); if (btn) btn.hidden = true; }
    }, function (err) { if (btn) btn.disabled = false; p.fail(P.failText(null, err), P.failDetail(null, err)); });
  });

  /* -- theme and type size preview (Settings) ---------------------------------------------------------------------------- */
  var disp = document.getElementById("display");
  if (disp) disp.addEventListener("change", function (e) {
    var el = e.target, root = document.documentElement;
    if (el.name === "theme") { if (el.value === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", el.value); }
    if (el.name === "size") root.setAttribute("data-size", el.value);
  });
})();
