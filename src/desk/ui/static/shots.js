/* Screenshots as evidence (DESK-0124), shared by the comment composer and the Not accepted reason panel.
   A box with `data-attach="/ticket/<ID>/attach"` and `data-token` takes a pasted or dropped image: it is uploaded at once (PNG, JPEG,
   WebP or GIF up to the store's limit, saved to tickets/assets/<ID>/ with a timestamped name), shown as a thumbnail with a remove
   button, and handed back to the caller as {name, rel, url, size}. The server's message is shown for anything it refuses. */
(function () {
  "use strict";
  var TYPES = "PNG, JPEG, WebP or GIF";

  function upload(el, file) {
    var fd = new FormData();
    fd.append("file", file, file.name || "image.png"); fd.append("token", el.getAttribute("data-token"));
    return fetch(el.getAttribute("data-attach"), { method: "POST", body: fd, headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }, function () { return { ok: false, j: {} }; }); })
      .then(function (x) {
        if (x.j.token) el.setAttribute("data-token", x.j.token);
        if (!x.ok) throw new Error(x.j.error || "The upload failed. Only " + TYPES + " images are accepted.");
        return { name: x.j.name, rel: x.j.rel, url: x.j.url, size: file.size };
      }, function () { throw new Error("The upload failed: could not reach Desk."); });
  }

  function remove(el, name) {
    var fd = new FormData();
    fd.append("name", name); fd.append("token", el.getAttribute("data-token"));
    return fetch(el.getAttribute("data-attach") + "/remove", { method: "POST", body: fd, headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }, function () { return { ok: false, j: {} }; }); })
      .then(function (x) {
        if (x.j.token) el.setAttribute("data-token", x.j.token);
        if (!x.ok) throw new Error(x.j.error || "Could not remove that screenshot.");
      });
  }

  /* One thumbnail: the image, its name and size, and a remove button that calls onRemove(shot). */
  function thumb(shot, onRemove) {
    var s = document.createElement("span"); s.className = "shot"; s.setAttribute("data-name", shot.name);
    var img = document.createElement("img"); img.src = shot.url; img.alt = shot.name; img.width = 112; img.height = 64;
    var x = document.createElement("button"); x.type = "button"; x.className = "shot-x"; x.setAttribute("aria-label", "Remove screenshot " + shot.name); x.textContent = "×";
    x.addEventListener("click", function () { onRemove(shot); });
    var m = document.createElement("span"); m.className = "shot-meta"; m.textContent = shot.name.replace(/^\d{4}-\d{2}-\d{2}-\d{4}-/, "") + " · " + Math.max(1, Math.round(shot.size / 1024)) + " KB";
    s.appendChild(img); s.appendChild(x); s.appendChild(m);
    return s;
  }

  /* Paste and drop on any element matching `selector` (looked up when the event happens, so replaced elements keep working).
     Pasted images only; dropped files of any kind go to the server, which names the allowed types if it refuses one. */
  function watch(selector, onFile, onError) {
    function box(e) { return e.target && e.target.closest ? e.target.closest(selector) : null; }
    function go(el, files) { Array.prototype.forEach.call(files, function (f) { upload(el, f).then(function (s) { onFile(el, s); }, function (err) { onError(el, err.message); }); }); }
    document.addEventListener("paste", function (e) {
      var el = box(e); if (!el) return;
      var imgs = Array.prototype.filter.call((e.clipboardData && e.clipboardData.files) || [], function (f) { return /^image\//.test(f.type); });
      if (imgs.length) { e.preventDefault(); go(el, imgs); }
    });
    document.addEventListener("dragover", function (e) { if (box(e) && e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types || [], "Files") >= 0) e.preventDefault(); });
    document.addEventListener("drop", function (e) {
      var el = box(e); if (!el || !e.dataTransfer || !e.dataTransfer.files.length) return;
      e.preventDefault(); go(el, e.dataTransfer.files);
    });
  }

  window.DeskShots = { upload: upload, remove: remove, thumb: thumb, watch: watch, TYPES: TYPES };
})();
