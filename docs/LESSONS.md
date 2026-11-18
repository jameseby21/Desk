# Lessons: Desk

Each lesson is a case (what happened, with evidence), a rule (the sentence that prevents it) and the tests that prove the rule. Newest first.

## A bare fr column grows to its widest line that never wraps (DESK-0305, 2026-11-15)

**Case.** The independent checker found DESK-0082 wider than the window on the DESK-0305 branch: scrollWidth 991 against 375 at 390 px, 1279 against 1265 at 1280 px, with 'Your call' and the Assignee cut off. The ticket page's columns were `grid-template-columns:2fr 1fr` (`1fr` on a phone). An `fr` track means `minmax(auto, fr)`, so a column is never narrower than its content's min-content width; a Not accepted reason is one line that never wraps (`.nr-reason .why`, `white-space:nowrap`, More opens the rest), so its whole text became the column's minimum and stretched every card. The same layout is on the base branch and the install branch (measured: 973 and 941 px at 390).

**Rule.** A page-level grid track is `minmax(0, …fr)`, never a bare `fr`, so no content decides the width of the page; text meant to stay on one line ellipsizes inside the width it is given.

**Tests.** `tests/test_ticket_page_width.py`: `test_static_no_column_of_the_ticket_page_takes_its_width_from_its_content` (stylesheet), `test_browser_a_long_not_accepted_reason_never_makes_the_page_wider_than_the_window` (page scroll width at 390 and 1280 px) and `test_browser_every_reason_ellipsizes_inside_its_row_and_nothing_on_the_page_passes_the_right_edge` (every element's box at 320, 390 and 1280 px).

## A line clamp on a padded box lets the next line show through the padding (DESK-0305, 2026-11-15)

**Case.** The independent checker opened DESK-0082, DESK-0117 and MGMT-0040 at a real 375 px client width: the Latest change box showed two lines and a third cut in half at its bottom edge, and the date and "+N git saves since" were never visible. The phone rule put `-webkit-line-clamp:2` on `.tl-latest` itself, which has 8 px of padding and inline children: the clamp sets the height, but `overflow:hidden` clips at the padding edge, so about 10 px of the third line showed; and the date and the count come after what changed, so the clamp cut them off.

**Rule.** A line clamp goes on a box with no vertical padding that holds only the text being clamped; the facts a person needs whole (who, when, a count) are separate items outside the clamp, each `white-space:nowrap`.

**Tests.** `tests/test_latest_change_phone.py`: `test_static_on_a_phone_the_clamp_is_on_what_changed_not_on_the_padded_box` (stylesheet), `test_browser_no_line_of_the_latest_change_is_cut_by_the_box_edge` (text-line geometry, clipping counted) and `test_browser_who_when_and_the_git_save_count_are_whole_inside_the_box` (element boxes), at 320 and 390 px on the three real shapes, a 400-character change, a 300-character unbroken token and a short one.

## A fix that reaches the server but not the browser is not shipped (DESK-0257, 2026-11-12)

**Case.** James clicked Approve on CONS-0057 and the browser posted to `/ticket/[object RadioNodeList]`, which answered 405. DESK-0205 had already fixed that script (`form.getAttribute("action")` instead of `form.action`), and his installed Desk served the fixed file. His browser still ran the old `progress.js` from its cache: Desk sent `/static/*.js` at fixed addresses with no `Cache-Control` header, so the browser guessed how long the copy stayed fresh.

**Rule.** Every `/static` response carries `Cache-Control: no-cache` (the browser checks the ETag before reuse), and every script and stylesheet address in a rendered page ends with `?v=<build id>`, the commit the About page shows, so an upgrade changes the address itself. Templates build those addresses with the `asset()` helper, never by typing `/static/...`.

**Tests.** `tests/test_static_cache.py::test_static_files_say_no_cache` and `tests/test_static_cache.py::test_static_addresses_carry_the_build_id`.
