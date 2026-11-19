/* DESK-0325 (standards: anything that will cause an error should have caused a warning beforehand).
   1. The Move box on the ticket page (form.mv-form): Blocked shows "Waiting on"; Rejected and Parked need your words. Move stays grey, with the
      reason beside it, until the field is filled.
   2. A button marked data-needs="words" (or "option-or-words") stays grey, with the reason beside it, until the form's words box holds at least
      four characters (or, for "option-or-words", an option is picked).
   Without this script the pages still work: the server refuses in the same words and gives every typed value back. */
(function () {
  "use strict";
  var MIN_WORDS = 4;

  function moveState(form) {
    var sel = form.querySelector(".mv-to"), opt = sel.options[sel.selectedIndex];
    var wait = form.querySelector(".mv-wait"), words = form.querySelector(".mv-words");
    var need = (opt && opt.getAttribute("data-need")) || "";
    wait.hidden = need !== "waiting_on";
    var why = "";
    if (!sel.value) why = opt.getAttribute("data-why") || "Choose where to move it.";
    else if (need === "waiting_on" && !wait.querySelector("input").value.trim()) why = opt.getAttribute("data-why");
    else if (need === "reason" && words.querySelector("input").value.trim().length < MIN_WORDS) why = opt.getAttribute("data-why");
    form.querySelector(".mv-go").disabled = !!why;
    form.querySelector(".mv-why").textContent = why || "Ready to move.";
    form.classList.toggle("mv-waiting", !!why);
  }

  function needsState(form) {
    var words = form.querySelector('[name="words"]');
    var typed = words ? words.value.trim().length >= MIN_WORDS : false;
    var picked = !!form.querySelector('input[name="option"]:checked');
    var hint = "";
    Array.prototype.forEach.call(form.querySelectorAll("button[data-needs]"), function (b) {
      if (b.getAttribute("data-locked") === null) b.setAttribute("data-locked", b.disabled ? "1" : "0");
      var kind = b.getAttribute("data-needs");
      var lack = kind === "option-or-words" ? !(typed || picked) : !typed;
      if (b.getAttribute("data-locked") === "1") return;  // disabled for another reason (not the owner): leave it
      b.disabled = lack;
      if (lack) hint = kind === "option-or-words" ? "Pick an option or type your words to enable this button." : "Type your words (at least four characters) to enable this button.";
    });
    var line = form.querySelector("[data-why]");
    if (line) line.textContent = hint;
  }

  function wire(root) {
    Array.prototype.forEach.call(root.querySelectorAll("form"), function (form) {
      if (form.getAttribute("data-wf") === "1") return;
      if (!form.classList.contains("mv-form") && !form.querySelector("button[data-needs]")) return;
      form.setAttribute("data-wf", "1");
      var run = function () { if (form.classList.contains("mv-form")) moveState(form); else needsState(form); };
      form.addEventListener("input", run);
      form.addEventListener("change", run);
      run();
    });
  }

  wire(document);
  // a page shown again from the back button: the browser may refill the fields, and base.html frees a form that was mid-send, so check again
  window.addEventListener("pageshow", function () {
    Array.prototype.forEach.call(document.querySelectorAll('form[data-wf="1"]'), function (form) {
      if (form.classList.contains("mv-form")) moveState(form); else needsState(form);
    });
  });
  // pages swap their content in place after an action (progress.js), so a new form can appear later
  new MutationObserver(function () { wire(document); }).observe(document.body, { childList: true, subtree: true });
})();
