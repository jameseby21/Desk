# Lessons: Desk

Each lesson is a case (what happened, with evidence), a rule (the sentence that prevents it) and the tests that prove the rule. Newest first.

## A fix that reaches the server but not the browser is not shipped (DESK-0257, 2026-11-12)

**Case.** James clicked Approve on CONS-0057 and the browser posted to `/ticket/[object RadioNodeList]`, which answered 405. DESK-0205 had already fixed that script (`form.getAttribute("action")` instead of `form.action`), and his installed Desk served the fixed file. His browser still ran the old `progress.js` from its cache: Desk sent `/static/*.js` at fixed addresses with no `Cache-Control` header, so the browser guessed how long the copy stayed fresh.

**Rule.** Every `/static` response carries `Cache-Control: no-cache` (the browser checks the ETag before reuse), and every script and stylesheet address in a rendered page ends with `?v=<build id>`, the commit the About page shows, so an upgrade changes the address itself. Templates build those addresses with the `asset()` helper, never by typing `/static/...`.

**Tests.** `tests/test_static_cache.py::test_static_files_say_no_cache` and `tests/test_static_cache.py::test_static_addresses_carry_the_build_id`.
