# Lessons: Desk

Each lesson is a case (what happened, with evidence), a rule (the sentence that prevents it) and the tests that prove the rule. Newest first.

## A line clamp on a padded box lets the next line show through the padding (DESK-0305, 2026-11-15)

**Case.** The independent checker opened DESK-0082, DESK-0117 and MGMT-0040 at a real 375 px client width: the Latest change box showed two lines and a third cut in half at its bottom edge, and the date and "+N git saves since" were never visible. The phone rule put `-webkit-line-clamp:2` on `.tl-latest` itself, which has 8 px of padding and inline children: the clamp sets the height, but `overflow:hidden` clips at the padding edge, so about 10 px of the third line showed; and the date and the count come after what changed, so the clamp cut them off.

**Rule.** A line clamp goes on a box with no vertical padding that holds only the text being clamped; the facts a person needs whole (who, when, a count) are separate items outside the clamp, each `white-space:nowrap`.

**Tests.** `tests/test_latest_change_phone.py`: `test_static_on_a_phone_the_clamp_is_on_what_changed_not_on_the_padded_box` (stylesheet), `test_browser_no_line_of_the_latest_change_is_cut_by_the_box_edge` (text-line geometry, clipping counted) and `test_browser_who_when_and_the_git_save_count_are_whole_inside_the_box` (element boxes), at 320 and 390 px on the three real shapes, a 400-character change, a 300-character unbroken token and a short one.

## A fix that reaches the server but not the browser is not shipped (DESK-0257, 2026-11-12)

**Case.** James clicked Approve on CONS-0057 and the browser posted to `/ticket/[object RadioNodeList]`, which answered 405. DESK-0205 had already fixed that script (`form.getAttribute("action")` instead of `form.action`), and his installed Desk served the fixed file. His browser still ran the old `progress.js` from its cache: Desk sent `/static/*.js` at fixed addresses with no `Cache-Control` header, so the browser guessed how long the copy stayed fresh.

**Rule.** Every `/static` response carries `Cache-Control: no-cache` (the browser checks the ETag before reuse), and every script and stylesheet address in a rendered page ends with `?v=<build id>`, the commit the About page shows, so an upgrade changes the address itself. Templates build those addresses with the `asset()` helper, never by typing `/static/...`.

**Tests.** `tests/test_static_cache.py::test_static_files_say_no_cache` and `tests/test_static_cache.py::test_static_addresses_carry_the_build_id`.
