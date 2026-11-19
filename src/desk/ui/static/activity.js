/* DESK-0329: the activity chart on the ticket page. Every event of the ticket is a mark at its time, on the status strip's own time
   axis (created on the left, now on the right), in one row per kind (Decisions, Status, Comments, Changes, Git: the rail's chips),
   coloured and shaped by who did it (the owner, Patch and code sessions, coworkers, agents, rules, other people), with a legend in words.
   A spot holds every event that falls in its slice of time: up to two marks stack; a busier spot prints how many, over a thin bar
   split by who. The marks always add up to the events in the list below.
   Hover, keyboard focus or a tap names the events of a spot (time, who, what); a click or Enter shows the newest of them in the list
   (opening Show all, a git fold or clearing a filter when it is hidden there) and outlines every event of the spot.
   The layout arithmetic (time to a spot, the counts) is plain functions, exported for the unit tests (tests/test_activity_chart.py).
   The data is what the page already has: the rail's rows (data-i, data-group, data-kind, <time datetime>) and the actor dots' colours,
   so the chart and the list can never disagree. */
(function (factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;  // node, for the unit tests
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", function () { api.mount(document); });
    else api.mount(document);
  }
})(function () {
  "use strict";
  var LANES = ["decisions", "status", "comments", "changes", "git"];  // the rail's chips, in their order
  // the kind of actor (timeline.py KIND_LABELS) -> who the chart names; the main session and code sessions are both Patch's
  var WHO = { owner: "owner", main: "patch", code: "patch", coworker: "coworker", subagent: "agent", rule: "rule", person: "other" };
  var WHO_ORDER = ["owner", "patch", "coworker", "agent", "rule", "other"];
  var CAP = 2;    // marks stacked in one spot (two fit under the row's name); a busier spot shows its count instead
  var SPOT = 26;  // px per spot: wider than the 24 px target minimum, and room for a three-digit count
  var DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  // -- the arithmetic (no page needed) ---------------------------------------------------------------

  /** "2026-11-18T21:09:00" (the log's own naive local form; a date alone is midnight) -> ms on that same naive clock, or NaN. */
  function parseTs(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(String(s == null ? "" : s).trim());
    if (!m) return NaN;
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
  }

  /** How many spots fit a track `width` px wide: at least one. */
  function spots(width, spot) {
    return Math.max(1, Math.floor((width || 0) / (spot || SPOT)));
  }

  /** Where `t` sits between `start` (0) and `end` (1), clamped; a time that cannot be read sits at the start; a range of no length
      puts everything at now (1), the right end, as the strip does for a ticket made this minute. */
  function frac(t, start, end) {
    if (isNaN(t)) return 0;
    if (!(end > start)) return 1;
    return Math.min(1, Math.max(0, (t - start) / (end - start)));
  }

  /** The spot (0 .. n-1) a time falls in. */
  function spotOf(t, start, end, n) {
    return Math.min(n - 1, Math.floor(frac(t, start, end) * n));
  }

  /** events: [{i, t, group, who}] -> {n, lanes: [{key, total, spots: [{bin, left, width, events (newest first), count, byWho}]}]}.
      `left` and `width` are percent of the track, so the spots tile it exactly; every event lands in exactly one spot of one lane
      (an unknown group goes to Changes), so the counts add up to the events. */
  function layout(events, start, end, width, spot) {
    var n = spots(width, spot), by = {};
    LANES.forEach(function (k) { by[k] = {}; });
    (events || []).forEach(function (e) {
      var lane = by[LANES.indexOf(e.group) >= 0 ? e.group : "changes"], b = spotOf(e.t, start, end, n);
      (lane[b] = lane[b] || []).push(e);
    });
    return {
      n: n,
      lanes: LANES.map(function (k) {
        var bins = Object.keys(by[k]).map(Number).sort(function (a, b) { return a - b; });
        var out = bins.map(function (b) {
          var evs = by[k][b].slice().sort(function (x, y) { return (y.t || 0) - (x.t || 0) || (x.i - y.i); });  // newest first
          var byWho = {};
          evs.forEach(function (e) { byWho[e.who] = (byWho[e.who] || 0) + 1; });
          return { bin: b, left: 100 * b / n, width: 100 / n, events: evs, count: evs.length, byWho: byWho };
        });
        return { key: k, total: out.reduce(function (s, x) { return s + x.count; }, 0), spots: out };
      })
    };
  }

  function pad(x) { return (x < 10 ? "0" : "") + x; }

  /** `Wed 18 Nov 21:09`; the year when it is not `thisYear`; the time left out when it is midnight (a date the log kept alone). */
  function when(t, thisYear, timeOnly) {
    if (isNaN(t)) return "Date not recorded";
    var d = new Date(t), hm = d.getUTCHours() || d.getUTCMinutes() || d.getUTCSeconds() ? pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) : "";
    if (timeOnly && hm) return hm;
    var day = DAYS[d.getUTCDay()] + " " + d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + (d.getUTCFullYear() === thisYear ? "" : " " + d.getUTCFullYear());
    return day + (hm ? " " + hm : "");
  }

  /** The time a spot covers in words: one time, or `Tue 17 Nov 22:00 to Wed 18 Nov 21:11` (the second date left out on one day). */
  function span(evs, thisYear) {
    var a = evs[evs.length - 1].t, b = evs[0].t;
    if (a === b || isNaN(a) || isNaN(b)) return when(b, thisYear);
    var same = new Date(a).toISOString().slice(0, 10) === new Date(b).toISOString().slice(0, 10);
    return when(a, thisYear) + " to " + (same ? when(b, thisYear, true) : when(b, thisYear));
  }

  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  // -- the page -----------------------------------------------------------------------------------------

  function mount(doc) {
    var box = doc.getElementById("activity");
    if (!box || box.getAttribute("data-ready")) return;
    box.setAttribute("data-ready", "1");
    var list = doc.getElementById("tl-list"), lanesBox = box.querySelector(".ac-lanes"), tip = box.querySelector(".ac-tip");
    var legend = box.querySelector(".ac-legend"), say = box.querySelector(".ac-say");
    var start = parseTs(box.getAttribute("data-start")), end = parseTs(box.getAttribute("data-end"));
    var thisYear = isNaN(end) ? new Date().getUTCFullYear() : new Date(end).getUTCFullYear();
    var rows = list ? Array.prototype.slice.call(list.querySelectorAll(".tl-ev")) : [];
    var colour = {}, names = {}, events = rows.map(function (li) {
      var kind = li.getAttribute("data-kind") || "", who = WHO[kind] || "other", tm = li.querySelector("time");
      if (!colour[who]) {
        var dot = li.querySelector(".tl-actor .tl-dot");
        if (dot && dot.style.backgroundColor) colour[who] = dot.style.backgroundColor;
      }
      if (!names[kind]) names[kind] = actorName(li);
      return { i: parseInt(li.getAttribute("data-i"), 10) || 0, t: parseTs(tm && tm.getAttribute("datetime")), group: li.getAttribute("data-group") || "changes",
               who: who, kind: kind, li: li };
    });
    box.setAttribute("data-events", String(events.length));
    if (!lanesBox) return;  // no events: the frame already says so, and there is nothing to point at
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var lastPointer = "", tapSecond = false, tipSpot = null, hits = [], current = null, built = -1;

    function actorName(li) {  // the name in the row's who chip, without its kind tag
      var a = li.querySelector(".tl-actor");
      if (!a) return "";
      var out = "";
      Array.prototype.forEach.call(a.childNodes, function (c) { if (c.nodeType === 3) out += c.textContent; });
      return out.trim();
    }
    function whoText(e) {  // `James (Owner)`; a name that is its kind already (`Code session`) once
      var a = e.li.querySelector(".tl-actor"), name = actorName(e.li), tag = a && a.querySelector("small");
      return tag && tag.textContent.trim() && tag.textContent.trim() !== name ? name + " (" + tag.textContent.trim() + ")" : name || "Not recorded";
    }
    function kindText(e) { var k = e.li.querySelector(".tl-ekind"); return k ? k.textContent.trim() : "Change"; }
    function whatText(e) { var w = e.li.querySelector(".tl-what"); return w ? w.textContent.trim() : ""; }
    function laneName(key) { var l = lanesBox.querySelector('.ac-lane[data-group="' + key + '"] .ac-name'); return l ? l.textContent.trim() : key; }
    function whoLabel(w) {
      if (w === "owner") return (names.owner || "Owner") + " (owner)";
      if (w === "patch") return names.main && names.code ? names.main + " and code sessions" : names.main ? names.main + " (main session)" : "Code sessions";
      return { coworker: "Coworkers", agent: "Agents", rule: "Rules", other: "Other people" }[w];
    }
    function glyph(w) {
      var g = doc.createElement("i");
      g.className = "ac-g ac-w-" + w;
      g.setAttribute("aria-hidden", "true");
      if (colour[w]) g.style.setProperty("--c", colour[w]);
      return g;
    }
    function label(sp, laneKey) {  // the spot's accessible name: what the tooltip says, in one sentence per event
      var e = sp.events[0];
      if (sp.count === 1) return kindText(e) + " by " + whoText(e) + ", " + when(e.t, thisYear) + ": " + whatText(e).slice(0, 160);
      var who = WHO_ORDER.filter(function (w) { return sp.byWho[w]; }).map(function (w) { return sp.byWho[w] + " by " + whoLabel(w); }).join(", ");
      return laneName(laneKey) + ": " + sp.count + " events, " + span(sp.events, thisYear) + "; " + who + ". Newest: " + kindText(e) + " by " + whoText(e) +
        ", " + when(e.t, thisYear) + ": " + whatText(e).slice(0, 120);
    }

    function draw() {
      var track = lanesBox.querySelector(".ac-track"), width = track ? track.clientWidth : 0;
      var n = spots(width), focusKey = current ? current.getAttribute("data-lane") + ":" + current.getAttribute("data-bin") : "";
      if (n === built) return;
      built = n;
      var lay = layout(events, start, end, width);
      lanesBox.setAttribute("data-spots", String(n));
      var all = [];
      lay.lanes.forEach(function (lane) {
        var li = lanesBox.querySelector('.ac-lane[data-group="' + lane.key + '"]');
        if (!li) return;
        var tr = li.querySelector(".ac-track");
        tr.textContent = "";
        lane.spots.forEach(function (sp) {
          var b = doc.createElement("button");
          b.type = "button";
          b.className = "ac-spot" + (sp.count > CAP ? " ac-many" : "");
          b.style.left = sp.left + "%";
          b.style.width = sp.width + "%";
          b.setAttribute("data-n", String(sp.count));
          b.setAttribute("data-lane", lane.key);
          b.setAttribute("data-bin", String(sp.bin));
          b.setAttribute("data-i", sp.events.map(function (e) { return e.i; }).join(" "));
          b.setAttribute("aria-label", label(sp, lane.key));
          b.tabIndex = -1;
          if (sp.count > CAP) {  // a busy spot: how many, over a thin bar split by who
            var c = doc.createElement("span");
            c.className = "ac-count";
            c.textContent = String(sp.count);
            var bar = doc.createElement("span");
            bar.className = "ac-bar";
            WHO_ORDER.forEach(function (w) {
              if (!sp.byWho[w]) return;
              var seg = doc.createElement("i");
              seg.className = "ac-seg";
              seg.setAttribute("data-who", w);
              seg.style.flexGrow = String(sp.byWho[w]);
              if (colour[w]) seg.style.setProperty("--c", colour[w]);
              bar.appendChild(seg);
            });
            b.appendChild(bar);
            b.appendChild(c);
          } else {
            sp.events.slice().reverse().forEach(function (e) { b.appendChild(glyph(e.who)); });  // oldest at the bottom
          }
          b._spot = sp;
          tr.appendChild(b);
          all.push(b);
        });
        li.classList.toggle("ac-empty", !lane.spots.length);
      });
      var keep = focusKey && lanesBox.querySelector('.ac-spot[data-lane="' + focusKey.split(":")[0] + '"][data-bin="' + focusKey.split(":")[1] + '"]');
      var first = keep || newest(all);
      if (first) first.tabIndex = 0;
      current = first || null;
      hideTip();
    }
    function newest(all) {  // the spot holding the newest event: where a keyboard user starts
      var best = null, bt = -Infinity;
      all.forEach(function (b) { var t = b._spot.events[0].t; if (!isNaN(t) && t >= bt) { bt = t; best = b; } });
      return best || all[0];
    }

    // -- the legend, in words ------------------------------------------------------------------------
    function buildLegend() {
      if (!legend) return;
      legend.textContent = "";
      var count = {};
      events.forEach(function (e) { count[e.who] = (count[e.who] || 0) + 1; });
      WHO_ORDER.forEach(function (w) {
        if (!count[w]) return;
        var li = doc.createElement("li");
        li.setAttribute("data-who", w);
        li.appendChild(glyph(w));
        var s = doc.createElement("span");
        s.textContent = whoLabel(w);
        var k = doc.createElement("span");
        k.className = "ac-n";
        k.textContent = String(count[w]);
        li.appendChild(s);
        li.appendChild(k);
        legend.appendChild(li);
      });
      if (events.length) {
        var many = doc.createElement("li");
        many.className = "ac-key-many";
        var c = doc.createElement("span");
        c.className = "ac-count";
        c.setAttribute("aria-hidden", "true");
        c.textContent = (CAP + 1) + "+";
        var s2 = doc.createElement("span");
        s2.textContent = "a number: that many events in one spot";
        many.appendChild(c);
        many.appendChild(s2);
        legend.appendChild(many);
      }
    }

    // -- naming a spot: hover, focus, tap ------------------------------------------------------------
    function showTip(b) {
      if (!tip || !b) return;
      var sp = b._spot, lane = b.getAttribute("data-lane");
      tip.textContent = "";
      var head = doc.createElement("p");
      head.className = "ac-tip-head";
      head.textContent = sp.count === 1 ? kindText(sp.events[0]) : laneName(lane) + ": " + plural(sp.count, "event", "events") + ", " + span(sp.events, thisYear);
      tip.appendChild(head);
      var ul = doc.createElement("ul");
      sp.events.slice(0, 4).forEach(function (e) {
        var li = doc.createElement("li"), top = doc.createElement("span"), what = doc.createElement("span");
        li.appendChild(glyph(e.who));
        top.className = "ac-tip-who";
        var tb = doc.createElement("b");
        tb.textContent = when(e.t, thisYear);
        top.appendChild(tb);
        top.appendChild(doc.createTextNode(" · " + whoText(e) + (sp.count > 1 ? " · " + kindText(e) : "")));
        what.className = "ac-tip-what";
        what.textContent = whatText(e);
        li.appendChild(top);
        li.appendChild(what);
        ul.appendChild(li);
      });
      tip.appendChild(ul);
      if (sp.count > 4) {
        var more = doc.createElement("p");
        more.className = "ac-tip-more";
        more.textContent = "and " + plural(sp.count - 4, "older event", "older events") + " in the list";
        tip.appendChild(more);
      }
      var hint = doc.createElement("p");
      hint.className = "ac-tip-hint";
      hint.textContent = lastPointer === "touch" ? "Tap again to see it in the list" : "Click or press Enter to see it in the list";
      tip.appendChild(hint);
      tip.hidden = false;
      tipSpot = b;
      lanesBox.querySelectorAll(".ac-spot.ac-on").forEach(function (x) { x.classList.remove("ac-on"); });
      b.classList.add("ac-on");
      // under the spot, kept inside the chart's own width
      var r = b.getBoundingClientRect(), o = box.getBoundingClientRect(), w = tip.offsetWidth;
      var left = Math.min(Math.max(0, r.left - o.left + r.width / 2 - w / 2), Math.max(0, box.clientWidth - w));
      tip.style.left = left + "px";
      tip.style.top = (r.bottom - o.top + 6) + "px";
    }
    function hideTip() {
      if (tip) tip.hidden = true;
      if (tipSpot) tipSpot.classList.remove("ac-on");
      tipSpot = null;
    }

    // -- showing a spot's events in the list ---------------------------------------------------------
    function visible(li) { return !!(li && li.offsetParent !== null && !li.closest("[hidden]")); }
    function reveal(li) {
      if (visible(li)) return;
      var clear = doc.getElementById("tl-clear");
      if (clear && !clear.hidden) clear.click();  // a More filters box hides it
      var all = doc.querySelector('.tl-chip[data-filter="all"]');
      if (!visible(li) && all && all.getAttribute("aria-pressed") !== "true") all.click();  // a Show only chip hides it
      var more = doc.getElementById("tl-showall");
      if (!visible(li) && more && !more.hidden && more.getAttribute("aria-expanded") !== "true") more.click();  // past the newest 12
      var run = li.getAttribute("data-run");
      if (!visible(li) && run) {  // inside a closed fold of git saves
        var fb = doc.querySelector('.tl-foldh[data-run="' + run + '"] .tl-foldb');
        if (fb && fb.getAttribute("aria-expanded") !== "true") fb.click();
      }
    }
    function jump(b) {
      var sp = b && b._spot;
      if (!sp) return;
      hideTip();
      hits.forEach(function (li) { li.classList.remove("ac-hit"); });
      hits = sp.events.map(function (e) { return e.li; });
      reveal(hits[0]);
      hits.forEach(function (li) { reveal(li); li.classList.add("ac-hit"); });
      var target = hits[0];
      target.setAttribute("tabindex", "-1");
      target.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
      try { target.focus({ preventScroll: true }); } catch (e) { target.focus(); }
      if (say) say.textContent = "Showing " + (sp.count === 1 ? "this event" : "the newest of these " + sp.count + " events") + " in the list, outlined.";
    }

    // -- keyboard: the chart is one stop in the Tab order; arrows move between spots -------------------
    function move(b) {
      if (!b) return;
      if (current) current.tabIndex = -1;
      b.tabIndex = 0;
      current = b;
      b.focus();
    }
    function laneSpots(b) { return Array.prototype.slice.call(b.parentNode.querySelectorAll(".ac-spot")); }
    function nearIn(laneLi, bin) {
      var best = null, d = Infinity;
      laneLi.querySelectorAll(".ac-spot").forEach(function (x) { var k = Math.abs(+x.getAttribute("data-bin") - bin); if (k < d) { d = k; best = x; } });
      return best;
    }
    function step(b, dir) {  // the nearest spot in the next lane up or down that has one
      var lanes = Array.prototype.slice.call(lanesBox.querySelectorAll(".ac-lane")), i = lanes.indexOf(b.closest(".ac-lane"));
      for (var j = i + dir; j >= 0 && j < lanes.length; j += dir) {
        var x = nearIn(lanes[j], +b.getAttribute("data-bin"));
        if (x) return x;
      }
      return null;
    }

    lanesBox.addEventListener("pointerdown", function (e) {  // before the focus a press brings: was this spot already named?
      var b = e.target.closest && e.target.closest(".ac-spot");
      lastPointer = e.pointerType || "mouse";
      tapSecond = !!b && tipSpot === b;
    });
    lanesBox.addEventListener("pointerover", function (e) {
      var b = e.target.closest && e.target.closest(".ac-spot");
      if (b && e.pointerType !== "touch") showTip(b);
    });
    lanesBox.addEventListener("pointerout", function (e) {
      var b = e.target.closest && e.target.closest(".ac-spot");
      if (b && e.pointerType !== "touch" && !b.contains(e.relatedTarget) && doc.activeElement !== b) hideTip();
    });
    lanesBox.addEventListener("focusin", function (e) {
      var b = e.target.closest && e.target.closest(".ac-spot");
      if (!b) return;
      if (current && current !== b) current.tabIndex = -1;
      b.tabIndex = 0;
      current = b;
      showTip(b);
    });
    lanesBox.addEventListener("focusout", function (e) { if (!box.contains(e.relatedTarget)) hideTip(); });
    lanesBox.addEventListener("click", function (e) {
      var b = e.target.closest && e.target.closest(".ac-spot");
      if (!b) return;
      if (e.detail !== 0 && lastPointer === "touch" && !tapSecond) {  // a tap names the events first; a second tap shows them in the list
        showTip(b);
        tapSecond = true;
        return;
      }
      jump(b);
    });
    lanesBox.addEventListener("keydown", function (e) {
      var b = e.target.closest && e.target.closest(".ac-spot");
      if (!b) return;
      lastPointer = "keyboard";
      var sib = laneSpots(b), i = sib.indexOf(b), to = null;
      if (e.key === "ArrowRight") to = sib[i + 1];
      else if (e.key === "ArrowLeft") to = sib[i - 1];
      else if (e.key === "ArrowDown") to = step(b, 1);
      else if (e.key === "ArrowUp") to = step(b, -1);
      else if (e.key === "Home") to = sib[0];
      else if (e.key === "End") to = sib[sib.length - 1];
      else if (e.key === "Escape") { hideTip(); return; }
      else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); jump(b); return; }
      else return;
      e.preventDefault();
      move(to || b);
    });
    doc.addEventListener("pointerdown", function (e) { if (tipSpot && !box.contains(e.target)) hideTip(); });
    doc.addEventListener("keydown", function (e) { if (e.key === "Escape") hideTip(); });

    // day lines: the strip's own date marks, carried down through the rows so the chart reads against the same dates
    var grid = box.querySelector(".ac-grid");
    if (grid) doc.querySelectorAll("#tl-axis .tl-tick").forEach(function (t) {
      var g = doc.createElement("i");
      g.className = "ac-gl " + (t.classList.contains("q") ? "q" : "");
      g.style.left = t.style.left;
      grid.appendChild(g);
    });

    buildLegend();
    draw();
    var tick;
    window.addEventListener("resize", function () { clearTimeout(tick); tick = setTimeout(draw, 120); });
  }

  return { LANES: LANES, WHO: WHO, WHO_ORDER: WHO_ORDER, CAP: CAP, SPOT: SPOT, parseTs: parseTs, spots: spots, frac: frac, spotOf: spotOf,
           layout: layout, when: when, span: span, mount: mount };
});
