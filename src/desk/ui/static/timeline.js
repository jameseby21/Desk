/* DESK-0171: the ticket timeline. Filters are multi-select per dimension (ANDed across dimensions); Colour by re-tints the markers
   and rebuilds the legend from the events still shown. Everything is already on the page, so nothing waits on the server. */
(function () {
  var root = document.getElementById("timeline"), list = document.getElementById("tl-list");
  if (!root || !list) return;
  var all = document.getElementById("tl-all"), by = document.getElementById("tl-by"), shown = document.getElementById("tl-shown"),
      legend = document.getElementById("tl-legend"), clear = document.getElementById("tl-clear");
  var items = Array.prototype.slice.call(list.querySelectorAll(".tl-ev"));
  var boxes = Array.prototype.slice.call(root.querySelectorAll(".tl-f input[type=checkbox]"));

  function chosen() {
    var f = {};
    boxes.forEach(function (b) { if (b.checked) (f[b.getAttribute("data-dim")] = f[b.getAttribute("data-dim")] || []).push(b.value); });
    return f;
  }

  function apply() {
    var f = chosen(), keys = Object.keys(f), dim = by ? by.value : "kind", n = 0, seen = {}, order = [];
    items.forEach(function (li) {
      var ok = keys.every(function (k) { return f[k].indexOf(li.getAttribute("data-" + k)) >= 0; });
      li.hidden = !ok;
      var mark = li.querySelector(".tl-mark");
      if (mark) mark.style.background = li.getAttribute("data-c-" + dim);
      if (!ok) return;
      n++;
      var v = li.getAttribute("data-" + dim);
      if (!seen[v]) { seen[v] = { c: li.getAttribute("data-c-" + dim), l: li.getAttribute("data-l-" + dim), n: 0 }; order.push(v); }
      seen[v].n++;
    });
    shown.textContent = keys.length ? "Showing " + n + " of " + items.length + " events, newest first." : "Showing all " + items.length + " events, newest first.";
    legend.textContent = "";
    order.forEach(function (v) {
      var s = document.createElement("span"), i = document.createElement("i");
      i.className = "tl-dot"; i.style.background = seen[v].c;
      s.appendChild(i); s.appendChild(document.createTextNode(seen[v].l + " " + seen[v].n));
      legend.appendChild(s);
    });
    root.querySelectorAll(".tl-on").forEach(function (el) {
      var c = (f[el.getAttribute("data-of")] || []).length;
      el.textContent = c ? "(" + c + " picked)" : "";
    });
    if (clear) clear.hidden = !keys.length;
  }

  boxes.forEach(function (b) { b.addEventListener("change", apply); });
  if (by) by.addEventListener("change", apply);
  if (clear) clear.addEventListener("click", function () { boxes.forEach(function (b) { b.checked = false; }); apply(); });
  if (all) {  // stay expanded across the page's own refresh after a save
    try { if (sessionStorage.getItem("desk-tl-open") === "1") all.open = true; } catch (e) { /* storage blocked: start closed */ }
    all.addEventListener("toggle", function () { try { sessionStorage.setItem("desk-tl-open", all.open ? "1" : "0"); } catch (e) { /* ignore */ } });
  }
  apply();
})();
