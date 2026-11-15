/* Motivation (DESK-0293): the decided-today line in the header, a short celebration when it goes up, and the focus timer.

   The count comes from /motivation/state.json (the event log, never stored here). The last count seen today sits in sessionStorage, so
   a page opened after a decision (a form post, an in-place swap, even a change from the terminal) celebrates once. The celebration is
   skipped when the Motivation switch is off, and is a still highlight (no motion) when the system asks for reduced motion. With the
   count switched off and the celebration on, the "+1" shows by itself where the count would be, then hides again.

   The focus timer keeps {id, title, end} in localStorage, so it survives a reload and shows on every page. When it ends, the header
   asks "Done or snooze?": Done posts to /focus/done/<id> (an approved ticket closes, with an undo toast; any other ticket stays as it is
   and its decision box opens for the next step), Snooze to /focus/snooze/<id> (until tomorrow). Each shows progress, then one plain
   sentence. Storage may be missing (a private window); the page still works, the timer just does not persist. */
(function () {
  "use strict";
  var root = document.getElementById("mot");
  if (!root) return;
  var countEl = document.getElementById("mot-count"), focusEl = document.getElementById("mot-focus");
  var FOCUS_KEY = "desk-focus", SEEN_KEY = "desk-decided-seen";
  var token = root.getAttribute("data-token") || "";

  function get(store, k) { try { return JSON.parse(store.getItem(k) || "null"); } catch (e) { return null; } }
  function put(store, k, v) { try { if (v === null) store.removeItem(k); else store.setItem(k, JSON.stringify(v)); } catch (e) { /* no storage */ } }
  function mk(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
  function calm() { return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches); }

  /* -- decided today and the celebration -- */
  function celebrate(up, counting) {
    if (root.getAttribute("data-celebrate") !== "1") return;
    if (!counting) { countEl.textContent = ""; countEl.hidden = false; }  // the count is off: the "+1" alone, for the moment
    countEl.classList.remove("mot-pop", "mot-still");
    void countEl.offsetWidth;  // restart the animation when two decisions land close together
    var plus = mk("span", "mot-plus", "+" + up);
    plus.setAttribute("aria-hidden", "true");
    countEl.appendChild(plus);
    if (calm()) {
      countEl.classList.add("mot-still");  // no motion: a highlight that fades out by itself, nothing moves
    } else {
      countEl.classList.add("mot-pop");
      var burst = mk("span", "mot-burst");
      burst.setAttribute("aria-hidden", "true");
      for (var i = 0; i < 12; i++) { var d = mk("i"); d.style.setProperty("--a", (i * 30) + "deg"); burst.appendChild(d); }
      countEl.appendChild(burst);
      setTimeout(function () { burst.remove(); }, 900);
    }
    setTimeout(function () {
      plus.remove(); countEl.classList.remove("mot-pop", "mot-still");
      if (root.getAttribute("data-count") !== "1") countEl.hidden = true;
    }, 2400);
  }

  function showCount(s) {
    if (!s) return;
    var counting = root.getAttribute("data-count") === "1";
    if (counting) {
      countEl.hidden = false;
      countEl.textContent = s.text;
      countEl.classList.toggle("met", !!s.met);
    }
    var seen = get(sessionStorage, SEEN_KEY);
    if (seen && seen.day === s.day && s.decided > seen.n) celebrate(s.decided - seen.n, counting);
    put(sessionStorage, SEEN_KEY, { day: s.day, n: s.decided });
  }

  function refresh() {
    if (root.getAttribute("data-count") !== "1" && root.getAttribute("data-celebrate") !== "1") return;
    fetch("/motivation/state.json", { credentials: "same-origin", headers: { "Accept": "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; }).then(showCount).catch(function () { /* the line stays as it was */ });
  }
  refresh();
  /* An in-place Approve, Send back or Move does not reload the page: look again once it has had time to save. */
  document.addEventListener("submit", function (e) {
    var a = (e.target && e.target.getAttribute && e.target.getAttribute("action")) || "";
    if (/\/(decide|move|check)$/.test(a)) { setTimeout(refresh, 1500); setTimeout(refresh, 4000); }
  }, true);

  /* -- the focus timer -- */
  var tick = null;
  function two(n) { return (n < 10 ? "0" : "") + n; }
  function left(ms) { var s = Math.max(0, Math.ceil(ms / 1000)); return Math.floor(s / 60) + ":" + two(s % 60); }

  function post(url, data, host) {
    var body = new FormData();
    Object.keys(data).forEach(function (k) { body.append(k, data[k]); });
    body.append("token", token);
    var p = window.DeskProgress ? DeskProgress.start(host, { steps: ["Sending", "Saving", "Done"] }) : null;
    var send = window.DeskProgress ? DeskProgress.post(url, body, { progress: p }) : fetch(url, { method: "POST", body: body, headers: { "Accept": "application/json" }, credentials: "same-origin" })
      .then(function (r) { return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, j: j }; }); });
    return send.then(function (x) {
      if (x.j && x.j.token) token = x.j.token;  // a refusal hands back a fresh one-time key
      if (p) { if (x.ok) p.done(x.j.message || "Done."); else p.fail((x.j && x.j.error) || "Desk did not finish. Nothing was changed. Try again."); }
      return x;
    }, function () {
      if (p) p.fail("Could not reach Desk. Nothing was changed. Try again.");
      return { ok: false, j: {} };
    });
  }

  function stopFocus() { put(localStorage, FOCUS_KEY, null); if (tick) clearInterval(tick); tick = null; drawFocus(); }

  function drawEnd(f) {
    focusEl.replaceChildren();
    focusEl.classList.add("ended");
    var q = mk("span", "mot-ask");
    q.setAttribute("role", "status");
    q.appendChild(mk("b", "", "Time is up. "));
    var link = mk("a", "", f.id); link.href = "/ticket/" + encodeURIComponent(f.id); q.appendChild(link);
    q.appendChild(document.createTextNode(": done or snooze?"));
    var done = mk("button", "btn small ball", "Done"), snooze = mk("button", "btn small", "Snooze till tomorrow"), close = mk("button", "mot-x", "×");
    done.type = snooze.type = close.type = "button";
    close.setAttribute("aria-label", "Stop the timer without changing the ticket");
    close.title = "Stop the timer without changing the ticket";
    var slot = mk("span", "mot-slot");
    function after(x, okText) {
      if (x.ok) {
        put(localStorage, FOCUS_KEY, null);
        if (x.j && x.j.batch && window.deskToast) window.deskToast({ text: x.j.message || okText, batch: x.j.batch });
        done.disabled = snooze.disabled = true;
        if (x.j && x.j.open) setTimeout(function () { location.assign(x.j.open); }, 1200);  // not closable yet: the decision box
        setTimeout(function () { drawFocus(); refresh(); }, 2500);
      } else {
        done.disabled = snooze.disabled = false;
        if (!slot.querySelector("a.mot-open")) { var o = mk("a", "mot-open", "Open the ticket"); o.href = "/ticket/" + encodeURIComponent(f.id) + "#decision"; slot.appendChild(o); }
      }
    }
    done.addEventListener("click", function () {
      done.disabled = snooze.disabled = true;
      post("/focus/done/" + encodeURIComponent(f.id), {}, slot).then(function (x) { after(x, "Closed."); });
    });
    snooze.addEventListener("click", function () {
      done.disabled = snooze.disabled = true;
      post("/focus/snooze/" + encodeURIComponent(f.id), {}, slot).then(function (x) { after(x, "Snoozed until tomorrow."); });
    });
    close.addEventListener("click", stopFocus);
    focusEl.append(q, done, snooze, close, slot);
    if (!document.activeElement || document.activeElement === document.body) done.focus({ preventScroll: true });
  }

  function drawFocus() {
    var f = get(localStorage, FOCUS_KEY);
    var startBtn = document.getElementById("focus-start");
    if (!f || !f.id || !f.end) {
      focusEl.hidden = true; focusEl.replaceChildren(); focusEl.classList.remove("ended");
      if (startBtn) { startBtn.disabled = false; startBtn.textContent = "Start focus · " + startBtn.getAttribute("data-minutes") + " min"; }
      return;
    }
    focusEl.hidden = false;
    if (startBtn) { startBtn.disabled = startBtn.getAttribute("data-focus-start") === f.id; if (startBtn.disabled) startBtn.textContent = "Focus running"; }
    if (Date.now() >= f.end) { if (tick) clearInterval(tick); tick = null; if (!focusEl.classList.contains("ended")) drawEnd(f); return; }
    focusEl.classList.remove("ended");
    var a = focusEl.querySelector("a.mot-clock");
    if (!a) {
      focusEl.replaceChildren();
      a = mk("a", "mot-clock"); a.href = "/ticket/" + encodeURIComponent(f.id); a.title = "Focus on " + f.id + ": " + (f.title || "");
      var x = mk("button", "mot-x", "×"); x.type = "button"; x.setAttribute("aria-label", "Stop the focus timer"); x.title = "Stop the focus timer";
      x.addEventListener("click", stopFocus);
      focusEl.append(a, x);
    }
    a.textContent = "Focus " + f.id + " · " + left(f.end - Date.now());
    a.setAttribute("aria-label", "Focus timer on " + f.id + ", " + left(f.end - Date.now()) + " left");
    if (!tick) tick = setInterval(drawFocus, 1000);
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest && e.target.closest("[data-focus-start]");
    if (!b) return;
    var mins = parseInt(b.getAttribute("data-minutes"), 10) || 25;
    put(localStorage, FOCUS_KEY, { id: b.getAttribute("data-focus-start"), title: b.getAttribute("data-title") || "", end: Date.now() + mins * 60000, minutes: mins });
    focusEl.replaceChildren();
    drawFocus();
  });
  window.addEventListener("storage", function (e) { if (e.key === FOCUS_KEY) { focusEl.replaceChildren(); focusEl.classList.remove("ended"); drawFocus(); } });  // another tab started or stopped it
  drawFocus();
})();
