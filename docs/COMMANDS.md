# Useful commands: Desk

Every command a person or an agent runs day to day, with what it does and when. Keep it current: a command that no longer exists is a defect. Type `drew` on Windows (`desk` is the same program elsewhere).

## Daily
| Command | What it does | When |
| --- | --- | --- |
| `drew-ui` | starts the web UI on http://127.0.0.1:8765 (`DESK_UI_PORT` to change, `DESK_HOME` for the store) | every session |
| `uv run --no-sync pytest -q` | runs the whole test suite | before every report |
| `drew validate` | schema, lifecycle rules, dependencies, track values; exit 1 on errors | before every commit |
| `drew list [--project] [--status] [--assignee NAME] [--all]` | table of open tickets, with an assignee column | start of a session |
| `drew assign <ID> --to NAME [--note "..."] [--handoff URL_OR_DOC_ID] [--as WHO]` | sets `assignee` and `assigned_at`, optional `handoff`, appends `- <date> · <actor> · assigned to <name>: <note>` to Comments. Done means: assignee named and notified; the hand-off record linked | handing a ticket to someone |
| `drew comment ID "text" [--kind Question\|Suggestion\|Concern\|FYI] [--as name]` | adds a dated, timed comment that always has a kind (Question unless you say otherwise), kept at the end of the line as `<!-- kind: Concern -->`; the same words from the same author within 10 minutes, whatever the kind, are refused as a duplicate | any time |
| `drew comments [--author NAME] [--since DATE\|"DATE HH:MM"\|last-sweep] [--unanswered] [--answered-by NAME] [--json] [--mark-sweep]` | lists comments across the store (ticket, time, author, kind, text; `--json` gives a list with ticket, title, when, author, kind, text, answered). `--unanswered` keeps comments with no later line by patch in the same ticket and no change to the ticket on a day after its last comment (dates are days, so a same-day change leaves it open). `--since last-sweep` reads `.desk/state` (everything when none is marked); `--mark-sweep` records now as the last sweep after listing, or alone just records it. The Today page shows the owner's open count in an "asked" block and the weekly review ticket's Report carries it | the sweep, first: `drew comments --author james --since last-sweep --unanswered`, answer each, then `drew comments --mark-sweep` |

## Data and environment
| Command | What it does | Notes |
| --- | --- | --- |
| `drew init [path] [--owner]` | creates a store (`desk.toml`, `tickets/`, `projects/`, `intake/`) | safe to run again |
| `drew migrate track --dry-run [--rules tracks/rules.yaml]` | prints ticket -> track and counts per track; writes nothing | read it before applying |
| `drew migrate track --apply [--rules ...]` | writes one `track:` line into each matched ticket; nothing else changes, `updated` included | skips tickets that already have a track |
| `drew migrate acceptance --dry-run` / `--apply` | removes boilerplate Acceptance lines that restate the Definition of Done; the dry run prints every line and the count | `--apply` edits only the Acceptance section; `updated` untouched |
| `drew migrate checks --dry-run` / `--apply` | rewrites the older inline `- [!] text — not accepted: reason` lines (Acceptance, How to validate, Prompt, Evidence) as `- [!] text` with `  > Not accepted (must fix): reason` under it; the dry run prints every old line and its new lines; no who or when is invented | `--apply` changes only those lines (`updated` untouched); CRLF files are skipped and named; both forms are read without it, `drew validate` warns while the older form remains |
| `drew migrate type --dry-run` / `--apply` | retires the legacy `slice` and `chore` types by rule; the dry run prints ticket, old -> new and counts | `--apply` rewrites only the `type:` line |
| `drew new --project key --type feature "title"` | types: feature, upgrade, bug, task, docs, decision, research, intake, release; writes the `How to validate` heading with a hint | write the steps before the work starts |
| `drew rename-project old new` | renames a project; ids stay, old key and prefix become aliases | |
| `drew intake <folder-or-zip> --topic x --project key` | copies a hand-off into `intake/` and opens a ticket | refuses private files |
| `drew intake --check <SLICES.md>` | checks the ticket-ready table (shape of standards/templates/UPDATE.md): required columns, known project keys, type, size, ask (`choose · YYYY-MM-DD`, required on decisions), Depends on ids (a row in the file or a ticket in the store), non-empty Acceptance and How to validate; prints each problem with its row id, writes `<file>.check.md` beside the input, exits 1 on errors | imports nothing; Drive's `\|` escapes are undone first |
| `drew sync` | with `[git] sync = true`: pulls, commits what is pending now (without waiting for the idle time) and pushes | `[git] debounce_seconds` (default 30) is the idle time after which pending changes are committed on their own: by a timer in the UI, at the next start of any command otherwise. A stop in between loses nothing |
| `drew index` | rebuilds `.desk/index.sqlite` | derived, gitignored |
| `drew bench --tickets N [--runs R] [--out file.json] [--save-baseline]` | builds a throwaway store (30 projects with brand blocks, N tickets of about 3.4 KB) in a temp folder, drives Today, board and a ticket page through the app, times `next_id`, `new`, `move` and reindex, prints p50 and p95 per path against `src/desk/bench/budgets.toml`, deletes the folder | before and after any speed change; `uv run --no-sync pytest -m bench` runs the 1,000-ticket case and fails on a broken budget or a p50 more than 20% over `src/desk/bench/baseline.json` |

## Build, release, docs
| Command | What it does | Notes |
| --- | --- | --- |
| `uv build --wheel` | builds the wheel; the build hook writes the commit into `desk/_build.py` | the footer and `drew --version` read it |
| `drew --version` | prints `Desk <version> · <commit or "no git">` | same text as the UI footer |
| `drew doctor [--repo PATH]` | installed version and commit, repo path and HEAD, the active YAML loader (libyaml or pure Python), tool path, store path, anything listening on the UI port; exit 1 when the installed code is older than the repo | run it when the UI looks out of date |
| `drew upgrade [--repo PATH]` | refuses while a process listens on the UI port (names the PID), then runs `uv tool install --force <repo>` and prints the new version. On Windows, when drew runs from uv's tools folder, it instead writes `desk-upgrade.cmd` to the temp folder and starts it detached (it waits 2 s, installs, logs to `desk-upgrade.log`, and on failure logs the restore command) and exits at once | stop the UI first; on Windows wait about 30 s, then `drew doctor` |
| `drew review new` / `drew review sync` | creates, then refreshes, this week's ticket review | weekly |

## Desk
| Command | What it does |
| --- | --- |
| `drew next --project <key>` | the next ready ticket for this project |
| `drew claim <ID>` / `drew start <ID>` | take a ticket; begin work |
| `drew report <ID> --file report.md` | hand it back with proof (moves to review); refused with "Write How to validate first" when that section is empty; warns on stderr for every `- [!]` (tried and failed) line it hands over |
| `drew decide <ID> --approve "why"` / `--changes "why"` | the owner's decision, in words; a `decision`-type ticket can be decided from `ready` (other types go through review first); words that look like a password or key are refused |
| `drew done <ID>` | approved to done, after the merge |
| `drew snap <ID>` / `drew attach <ID> file` | attach a screenshot or file as evidence |
| `drew stats` (includes a per-assignee table: open, done this week), `drew board`, `drew show <ID> [--raw]` | look at the store; `show` censors profanity (f*****g) unless `--raw` or `[style] profanity_filter = false` |
| `drew brand-check [--create]` | projects missing a brand mark |

## In the UI
| Where | What |
| --- | --- |
| every response | a `Server-Timing: db;dur=..., render;dur=..., total;dur=...` header; a request over 300 ms logs `slow request METHOD /path N ms` to stderr (logger `desk.slow`) |
| `/projects?p=<key>&t=<track slug>&k=<ID>` | project, track, ticket and peek panel; what is open lives in the address |
| `/board?track=<slug>`, `/?track=<slug>` | the track filter chip |
| `/doc/<store-relative path>.md` | a store Markdown file, read-only; `glossary.md` terms get popovers and `[[Term]]` links to them |
| `/ticket/<ID>` | the full ticket: How to validate, owning docs, Mentioned-in; paste or drop a screenshot into the comment box (or into a Not accepted reason): PNG, JPEG, WebP or GIF up to `[attachments] max_mb` (default 10 MB), saved to `tickets/assets/<ID>/` as `YYYY-MM-DD-HHMM-name.ext`, shown as a thumbnail with a remove button; pick `Evidence for` an Acceptance line and the post writes it into the comment and under the line as `> Evidence:`; other types are refused naming the allowed ones |
| `/ticket/<ID>` comment box | pick a kind (Question, the default, Suggestion, Concern or FYI); `Ctrl Enter` posts through the same path as the button. Post is single-shot: the first click shows a spinner and `Posting…`, locks the text and ignores more clicks; one key per post, so a repeated request returns the same comment; `Posted` appears and the comment is added in place, highlighted for 2 s; a failure (500, or no answer in 10 s) keeps your text and the button reads Try again |
| `/ticket/<ID>` check boxes | above the Acceptance list a line `2 of 4 done · 1 not accepted · 1 open` and a lime-and-red bar; each Acceptance line has a Done and a Not accepted box in a fixed right-hand column that save in place (no reload; the page keeps its place and focus); click to set, click again to clear, setting one clears the other. Keys with focus on a line (Tab to it): `D` Done, `N` Not accepted (opens the reason panel, or clears it), `Esc` closes the panel. Not accepted asks "Why isn't this accepted?" with a severity (Must fix, Should, Nit) and needs 3 or more non-space characters; the saved reason keeps severity, who, when, evidence count and an Edit link |
| every form and action | posts in place and never moves the page: the comment box, check boxes and reasons, Approve and Send back, Move, Attach an image, and Settings. A one-line progress component (`static/progress.js`) shows a thin bar with named steps (Sending, Saving, Done) that follow the real request, the elapsed time after 2 s, then one plain sentence (Posted, Moved to triage, Image attached, or what went wrong and what to do). Check boxes show it as a pill at the bottom of the window so nothing shifts. A form that changes the ticket (Approve, Send back, Move) swaps the page content in place and keeps the comment draft. Auto-refresh comes back to the same scroll position. Without script every form still posts and returns to its own part of the page (`#comments`, `#acceptance`, `#decision`). A new form must carry `data-progress` (a test fails without it) |
| `/ticket/<ID>` Decision section (reply box) | on a `decision` ticket owned by the store owner that is `ready` or in `review`, a box under Decision: `Choose:` titles with an `Options: A ...; B ...` line in Context show the options as buttons plus a free line; `Approve:` shows Approve and Send back with a reason; `Answer:` and `Provide:` show a text box. Sending records the words with the owner's name (same code as `drew decide`), moves the ticket, and fills the Decision section in place. Anyone else gets 403; secret-looking values are refused |
| `/doc/<folder>/<file>.md` | any store folder or submodule, read-only, with an "Edit in VS Code" link |

## Troubleshooting one-liners
| Symptom | Command |
| --- | --- |
| port in use | `drew doctor` shows the PID; then `taskkill /PID <pid> /T /F` (Windows) or `kill <pid>` (never a broad name match) |
| stale install | `drew doctor`, then stop the UI and `drew upgrade` |
| `No module named 'desk'` after an upgrade | `uv tool install --force --reinstall <repo>` (also printed by `drew doctor`) |
| "no git" in the footer | the wheel was built outside a git checkout; rebuild from the repo |
