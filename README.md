# Desk

**Why Desk exists and what it does differently: [docs/WHY.md](docs/WHY.md).**

Tickets as Markdown files in your own git repo. A command line for people and AI agents, a small web UI for the owner. Nothing closes without proof, approval is the owner's own words, and every change is a reviewable diff.

Desk is a lens on your files, not a landlord: the store is a plain directory you can read, grep and commit without Desk installed.

## Windows note: type `drew`, not `desk`

Windows ships `C:\Windows\System32\desk.cpl` (the Display Settings applet) and System32 comes before uv's tool directory on PATH, so typing `desk` in PowerShell opens Display Settings. The same program is installed under a second name, `drew` (and `drew-ui`), which has no collision. `desk.exe` with the extension also works. On macOS and Linux `desk` is fine.

Set the store once so no `--home` is needed: `[Environment]::SetEnvironmentVariable("DESK_HOME", "<path to your store>", "User")`, then open a new terminal.

## Install

```bash
# once per machine, from this repo
uv tool install .
# or run from the repo without installing
uv run desk --help
```

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

## The store (`DESK_HOME`)

A store is any directory with a `desk.toml`. Desk finds it in this order: the `--home` flag, the `DESK_HOME` environment variable, then the nearest `desk.toml` walking up from the current directory.

```
<store>/
  desk.toml              # owner, delegation, git sync, stale window, combos
  tickets/               # one file per ticket: KEY-0001-slug.md
  tickets/assets/<ID>/   # attachments and screen snaps for one ticket
  events/<ID>.jsonl      # the ticket's activity log: one event per change, append-only, committed (DESK-0162)
  projects/<key>.md      # one profile per project; a ticket's project must exist here
  intake/<date>-<topic>/ # research hand-offs, copied in by `desk intake`
  tracks/<project>/<slug>.md  # optional track descriptions (title, kind sprint|milestone|epic|theme, starts, ends, goal)
  tracks/rules.yaml      # ordered rules for `desk migrate track`
  glossary.md            # one `## Term` per entry; drives popovers and [[Term]] links
  DECISIONS.md           # process decisions, dated, append-only
  .desk/index.sqlite     # derived search index, gitignored, never the source of truth
```

Create one with `desk init [path] [--owner name]`.

`desk.toml`:

```toml
[desk]
owner = "james"              # the human who approves

[approval]
delegated_types = ["task"]  # main may approve these ...
delegated_max_size = "s"     # ... up to this size

[git]
sync = false                 # when true: pull before and push after every write command

[stale]
days = 21                    # open tickets untouched this long are "chewed back"

[life]
combos = false               # when true: the Today page celebrates two tagged tickets done within 30 minutes
```

## A ticket

`tickets/JRE-0042-add-the-doc-ownership-map.md`. Ids are the project's prefix plus a number that counts per project. Frontmatter is validated on every write.

```yaml
---
id: JRE-0042
title: Add the doc-ownership map and owners check
project: jre                 # must match projects/jre.md
type: feature                # feature | upgrade | bug | task | docs | decision | research | intake | release
status: ready                # see lifecycle
priority: p1                 # p0 | p1 | p2 | p3
size: s                      # xs | s | m | l  (an l is split before it leaves triage)
owner: main                  # main | james | <project key> | a name
branch: jre/JRE-0042-doc-ownership
depends_on: [JRE-0041]
docs_to_update: [docs/README.md#docs-map]
dod: [core, code, docs]      # Definition of Done modules that apply
tags: [docs, weekly]         # optional, free-form strings; combos need at least one
track: Sprint 0006           # optional, non-empty string: the group inside the project (project > track > ticket)
assignee: codex              # optional: who executes it (main, james, codex, a project key, chaz, em, ... any name)
blocks: [JRE-0043]           # optional: ids this ticket holds up (must exist)
related: [JRE-0040]          # optional: ids worth reading (a missing one is a warning)
cloned_from: JRE-0031        # optional: set by `desk clone`
evidence: "commit e125efb, 36 tests"   # optional one-liner; the Evidence section holds the long form
effort_hours: [1, 2]         # optional [low, high]
confidence: 0.8              # optional, 0..1
review_by: 2026-11-14        # optional
smallest_step: "Open the preview and read the About page."   # the step for a rough week
blocked_on: ""               # required when status is blocked
source: intake/2026-11-07-em-round2/CLAUDE_CODE_HANDOFF.md#stage-2
created: 2026-11-07
updated: 2026-11-07
done_at: 2026-11-20T16:42:10 # written by `desk done`, local time
---

## Context
## Outcome
## Acceptance
## How to validate
- [ ] checkboxes a test or the owner can verify
## Prompt
## Docs to update
## Out of scope
## Attachments
## Report
## Evidence
## Decision
## Comments
```

Headings are fixed so the CLI and the UI can find them. `## Prompt` (the text an agent or person pastes to execute the ticket), `## Attachments` and `## Evidence` (what proves done: commands, records, commits) are new in v0.2 and may be missing from older files; a write adds them. Any other `## Heading` in a file is kept on write, after the canonical ones, so a backfilled ticket loses nothing. A ticket is a static hand-off: everything needed to do it and to prove it is in the file. The worker writes **Report** (what changed, how it was verified, the commands run). The owner writes **Decision**, through `desk decide` or the UI, and Desk records it as a dated line: `- 2026-11-07 · james · approved · "Ship it after the DNS lands."`

### Rich bodies

Every section is Markdown and the UI renders it with [markdown-it-py](https://github.com/executablebooks/markdown-it-py) (CommonMark plus tables and strikethrough) and the task-list plugin from `mdit-py-plugins`: paragraphs, emphasis, `### headings` (an `##` line starts a new section, so go one level deeper inside a section), ordered and bulleted lists, `- [x]` checkboxes drawn as checked boxes, tables, fenced code, links (open in a new tab), images. Raw HTML in a ticket is escaped, never passed through, and `javascript:` links are dropped.

- **Attachments** live in `tickets/assets/<ID>/` and the UI serves them at `/assets/<ID>/<file>` (ids and file names are checked; nothing outside the ticket's folder is reachable). `desk attach ID path [--caption "text"]` copies the file in and appends `![caption](assets/ID/file)` (a plain link for non-images) to the ticket's Attachments. A second file with the same name gets a `-2` suffix; nothing is overwritten.
- **Screen snaps**: `desk snap ID [--caption "text"]` captures the whole screen to `snap-<timestamp>.png` and attaches it. Windows uses PowerShell (`System.Drawing` `CopyFromScreen`), macOS `screencapture -x`, Linux ImageMagick `import` or `gnome-screenshot`; anything else is refused with a plain message.
- **Mermaid**: a fenced ```` ```mermaid ```` block is drawn in the browser. `mermaid.min.js` 11.4.1 is vendored in `src/desk/ui/static/` (packaged with the wheel) and loaded only on ticket pages that contain a diagram; if the file is ever missing the page falls back to the same version on cdn.jsdelivr.net.

### Ticket types

| Type | Means |
| --- | --- |
| `feature` | a new capability |
| `upgrade` | an improvement to something that exists |
| `bug` | something broken |
| `task` | a thing to do that is not code: paperwork, accounts, a call |
| `docs` | documentation only |
| `decision` | an ask of a person: approve, choose, answer, direction, provide |
| `research` | find something out |
| `intake` | material handed in from outside |
| `release` | ship a version |

The old `slice` (now `feature`) and `chore` (now `task`) still read and validate, with the warning "legacy type; run drew migrate type"; `desk new` never offers them. `desk migrate type --dry-run|--apply` retires them by rule (first match wins; only legacy tickets move; `--apply` rewrites the one `type:` line and bumps nothing): Approve:/Choose:/Answer:/Provide:/Direction: titles become `decision`; `Docs:` titles or tags `docs`/`quickstart` become `docs`; tag `release` or a title starting `Release` becomes `release`; `Do:` titles and all other chores become `task`; slices titled `200% motion`, `200% look`, `200% feature`, `Brand:`, `Style pass`, `Readability` or tagged `upgrade` become `upgrade`; a slice with `Bug` in the title becomes `bug`; every other slice becomes `feature`. The UI shows the type as a short colour-coded chip.

### How to validate

`## How to validate` sits right after Acceptance. It is written before the work starts, as part of planning: the steps a person or agent runs to confirm the Outcome once the work is claimed done (commands with expected output, URLs with what to look for, files with what they must contain). It is not Acceptance (what must be true) and not Evidence (what was observed). Right after it sits `## Commands` (DESK-0177): the exact commands that check the ticket, one bullet in backticks each, which the ticket page shows with a copy button; `drew migrate commands` adds it to older tickets and `drew validate` warns when a ready ticket has none and when a ui ticket in review has no screenshot. `desk new` writes the heading with a one-line hint comment fitted to the type; the hint does not count as steps. `desk report` and the UI hand-over refuse to move a ticket to `review` while the section is empty ("Write How to validate first"); `desk validate` warns about tickets already in review without one. The ticket page shows it beside Acceptance, and the review-week lines end with ` · validate: <first line>`.

## Projects, prefixes and renames

A project is `projects/<key>.md` with frontmatter:

```yaml
---
key: learninglabs
prefix: LEARN                # ticket ids are LEARN-0001, LEARN-0002, ...; without it, the key uppercased
aliases: [fraud, FR]         # old keys and old prefixes this project still answers to
name: learningLabs
---
# learningLabs
```

A ticket id is valid when its prefix is the current `prefix` **or** the uppercased form of any alias, so a project can be renamed without rewriting history: ids are permanent.

`desk rename-project <old-key> <new-key> [--prefix NEW] [--name "New name"]` does the rename in one move: `projects/<old>.md` becomes `<new>.md`, `key:` (and `prefix:`, the title) are updated, the old key and old prefix are appended to `aliases:`, and every ticket of the project gets `project: <new>`, a bumped `updated` and one comment line (`- <date> · desk · project renamed <old> -> <new>`). Ids never change; nothing is deleted; it refuses when `<new>` already exists. New tickets always take the current prefix.

Everywhere tickets are shown (`list`, `board`, `show`, every UI page) they are grouped by their current project under its current name, and a ticket whose id prefix differs from the project's current prefix is marked `(formerly FR)` after the id.

## Refreshing Desk

The installed tool is a copy of the repo from the day it was installed, so a fix in the repo does not reach `drew-ui` until you reinstall. The footer of every UI page and `drew --version` show `Desk <version> · <commit>`, so you can tell which copy is running.

1. Stop the UI (`drew doctor` names the PID listening on the port; `taskkill /PID <pid> /T /F` on Windows, `kill <pid>` elsewhere).
2. `drew upgrade` (add `--repo PATH` if the repo is not at `<store>/desk` and `DESK_REPO` is not set). It refuses while a process is listening on the UI port, then runs `uv tool install --force <repo>` and prints the new version.
3. Start `drew-ui` again.

`drew doctor` prints the installed version and commit, the repo path and HEAD, the tool and store paths and anything listening on the UI port (`DESK_UI_PORT`, default 8765). It exits 1 when the installed code is older than the repo.

## Lifecycle

```
inbox → triage → ready → claimed → in_progress → review → approved → done
                                        ▲             │
                                        └── changes_requested ◄┘
any open status → blocked (then `desk unblock` restores the previous status)
any open status → rejected (with a reason)
```

On screen each status has a plain label, chosen by the style in Settings (DESK-0253: a mascot word may only sit beside the plain label, never replace it): Plain, the default, says Inbox, To sort, Ready, Claimed, In progress, Sent for review, Changes requested, Approved, Done, Blocked, Rejected, Stale; Dogs (Drew or Fuzz) adds a dog word in brackets after the plain label, for example Ready (thrown), In progress (fetching), Done (fetched), Blocked (stuck), Stale (chewed back); Cat says the plain labels. The files and the command line always use the plain words. The tables are `src/desk/words.py`.

Rules the CLI and the UI both enforce, and `desk validate` checks in the files:

- Only `main` or the owner throws a ticket into `ready`; a project session claims it (`desk claim` refuses `main` and the owner as claimant).
- `review` needs a non-empty Report.
- `approved` needs the configured owner, unless the ticket's type is delegated (`[approval]`) and its size is within the limit.
- A ticket cannot be `done` while any `depends_on` ticket is not `done`. `desk done` stamps `done_at` with the local time.
- `blocked` needs `blocked_on` (a ticket or a person) and a reason.
- Decisions need words: approve, send back and reject all take a reason of at least four characters.
- Dangling `depends_on` or `blocks`, unknown projects, bad enum values, a `tags` value that is not a list of strings, a bad `done_at` and missing required fields are errors. A size `l` outside triage, a `related` or `cloned_from` id that does not exist, are warnings.
- Hygiene warnings (never errors): a title longer than 70 characters; a ticket that has sat in `review` for more than 7 days (measured from its `updated` date).
- Stale is computed, not stored: any open ticket whose `updated` is older than `[stale].days`.

## Commands

| Command | Who | What |
| --- | --- | --- |
| `desk init [path] [--owner]` | owner | Create a store |
| `desk new --project jre --type slice [--priority p1 --size s --owner main --assignee codex --dod core,code --source path#sec] "title"` | main | Next id for the project, from the template; prints the path |
| `desk clone ID [--project key] "new title"` | main | New ticket with the source's Context, Outcome, Acceptance (boxes cleared) and Prompt; sets `cloned_from` |
| `desk stats [--since YYYY-MM-DD] [--project key] [--json]` | anyone | Total, done, in review (built but not proven), open; counts by status, project and type; created and done per week; a Decisions-per-week table (last 8 weeks) and `Review weeks: N created, M closed` |
| `desk review new [--week YYYY-MM-DD] [--project management] [--owner james]` | owner / main | Create the week's **Ticket review** ticket (one per ISO week; task, ready, p1, `review_by` Friday, tags `management, review-week`). Acceptance is generated: one checkbox per ticket waiting on the owner, grouped by ask (Approve, Choose, Answer, Provide, Do, Review). Refuses a second one for the same week, or a store without a `management` profile |
| `desk review sync [ID]` | owner / main | Regenerate the list between `<!-- review:start -->` and `<!-- review:end -->` (hand-written lines outside survive): decided tickets become `[x]`, new ones are added, vanished ones are kept as `(resolved)`. Writes the weekly KPI between `<!-- kpi:start -->` and `<!-- kpi:end -->` in Report: waiting at creation, added, decided this week, still waiting, median days in review, oldest waiting. Idempotent. The Today page links this week's review with "N waiting" |
| `desk list [--project] [--status] [--owner] [--stale] [--all]` | anyone | Table of tickets (open ones by default), grouped by project |
| `desk next --project jre [--json]` | project session | Highest-priority, oldest ready ticket, in full. `--json` is the SessionStart hook shape (`hookSpecificOutput.additionalContext`) |
| `desk show ID` | anyone | Print the ticket (with a `(formerly OLD)` header line when the project's prefix has changed) |
| `desk claim ID [--owner key-or-name]` | project session | `ready → claimed`; sets owner and branch |
| `desk start ID` | project session | `claimed → in_progress` |
| `desk report ID --file report.md` or `--text "..."` | project session | Writes Report, `→ review` |
| `desk decide ID --approve "why"` or `--changes "why"` `[--as james] [--relaying james]` | owner / main (`--as` must be you; `--relaying` records that you relay their words) | Records the words in Decision; `→ approved` or `→ changes_requested` |
| `desk done ID [--commit sha]` | project session | `approved → done`, after the merge; writes `done_at` |
| `desk block ID --on "ticket or person" "reason"` | anyone | `→ blocked`, remembers where it came from |
| `desk unblock ID [--to status]` | anyone | Back to the status before the block |
| `desk reject ID "reason"` | owner / main | Let it go, with a reason |
| `desk move ID --to status [--reason] [--as]` | main | Any other guarded move (`inbox → triage → ready`, `changes_requested → in_progress`) |
| `desk comment ID "text" [--as name]` | anyone | Dated line under Comments |
| `desk attach ID file [--caption "text"]` | anyone | Copy a file into `tickets/assets/ID/` and list it under Attachments |
| `desk snap ID [--caption "text"]` | anyone | Capture the screen to a PNG and attach it |
| `desk rename-project old new [--prefix NEW] [--name "..."]` | main | Rename a project; ids stay, the old key and prefix become aliases |
| `desk validate` | pre-commit, CI | Schema, lifecycle rules, dependencies, hygiene warnings. Exit 1 on errors |
| `desk index` | UI, pre-commit | Rebuild `.desk/index.sqlite` from the files |
| `desk board [--project] [--all]` | owner | The board in the terminal |
| `desk intake <folder-or-zip> --topic "x" --project key` | main | Copy a hand-off into `intake/<date>-<topic>/` and open an `intake` ticket. Refuses packages containing `PRIVATE_CONTEXT.md` or a `jeby-private` path |
| `desk migrate track --dry-run\|--apply [--rules tracks/rules.yaml]` | owner / main | Assign `track:` from ordered rules (first match wins). The dry run prints ticket -> track and counts; `--apply` inserts one `track:` line per ticket and changes nothing else. Tickets that already have a track are left alone |
| `desk doctor [--repo PATH]` | anyone | Installed version and commit, repo path and HEAD, tool and store paths, anything on the UI port. Exit 1 when the installed code is older than the repo |
| `desk upgrade [--repo PATH]` | owner | Refuses while the UI port is in use (names the PID), else `uv tool install --force <repo>` |
| `desk home` | anyone | Print the resolved store |

Refusals print a plain reason and exit 1, so an agent sees exactly why. Output is UTF-8 on every platform, including Windows consoles.

## The UI

```bash
DESK_HOME=/path/to/store desk-ui      # http://127.0.0.1:8765  (DESK_UI_PORT to change)
```

Plain HTML forms over FastAPI and Jinja2; no build step. The only JavaScript is the vendored Mermaid, loaded on ticket pages that contain a diagram.

- **Today**: Claimed tickets (claimed and in progress), Waiting on you (review, and open decisions), Waiting for review, Stale (not touched for a long time), Decided this week. The Dogs style adds its dog words in brackets beside these labels. A Good / Normal / Rough switch hides the stale pile on a rough week and shows each ticket's smallest step. With `[life] combos = true`, a **Combos** card lists any two tagged tickets finished within 30 minutes of each other today: `"Dishes" and "Laundry" completed together, +100 for efficiency!` It is computed from `done_at`; nothing new is stored.
- **Board**: one column per status, filter by project.
- **Ticket**: every section rendered as Markdown (see Rich bodies), acceptance checkboxes with a count, the Report beside them with a red **Not run** tag when it is empty, Attachments with inline images, Approve and Send back forms (both require words, acting as the configured owner), a guarded Move form, comments.
- **Projects**: one row per `projects/*.md` with its prefix and aliases, counts by status and last activity.

The UI calls the same operations as the CLI, so the same rules apply.

## Hooks for Claude Code

- SessionStart: `desk next --project <key> --json` returns the next ready ticket as `additionalContext`.
- Stop: `desk validate` (exit 1 on errors) is a cheap backstop; a plugin can add "a claimed ticket has no Report" on top.
- pre-commit: `desk validate && desk index`.

## Development

```bash
uv sync
uv run pytest
```

## License

MIT, copyright JA Insights LLC.

## UI links, checkboxes and brand

- Ticket ids that exist in the store (`ABC-0001`) render as links with the ticket's title on hover; files are never changed.
- Acceptance, How to validate, Prompt and Evidence checkboxes are clickable in the UI (`POST /ticket/<ID>/check`, `ops.toggle_check`). On a review-week ticket each line also gets Open, Approve and Send back.
- A project profile may carry `brand: {mark: "assets/brand/<key>/mark.svg", accent: "#B3121A", family: "JA Insights"}`. `assets/brand/` is served at `/brand/<path>`. `drew brand-check [--create] [--assignee chaz]` lists projects with no usable brand and can open one ticket each in project `brand`; `drew validate` warns for projects without a brand block.

## Doc links, glossary and backlinks

- A store path such as `docs/EDITIONS.md#free-edition` (folders `docs`, `projects`, `intake`, `standards`, `tickets`, `tracks`) in a ticket body or comment becomes a link to `/doc/<path>`, which shows that Markdown file read-only inside the app. Only files inside the store are reachable; `..`, absolute paths and private files get a 404. A path to a file that does not exist stays plain text.
- `glossary.md` at the store root, one `## Term` per entry: the first use of each term on a page gets a dotted underline and a popover; `[[Term]]` always links to `/doc/glossary.md#term`.
- `/doc/<path>` reads the file from where it lives, at request time, from any top-level folder of the store, including submodule folders such as `jeby Real Estate/docs/README.md` or `desk/docs/WHY.md`. Nothing is copied into Desk or the store. Every doc link shows two affordances: the in-app read-only view, and "Edit in VS Code" (`vscode://file/<absolute path>`).
- When a project's profile names its repo folder (a `repo:` field or a `| Repo |` table row) and that repo has `docs/doc-ownership.yaml`, the ticket page lists the owning doc of every `docs_to_update` path and every code path in the body, links the `source:` field, and the project's `/doc/projects/<key>.md` page links its documentation index (`docs/README.md`, else `README.md#documentation-tree`). Desk reads these files; it never generates an index.
- A doc path written in a ticket is resolved the same way for links, owning docs and `desk validate`: first against the store root, then against the ticket's project repo folder (`repo:` in the profile frontmatter, else the first path of the `| Repo |` row, cut at ` →` or `(`, else a known folder: jre `jeby Real Estate`, musicalgames `Games/MusicalGames`, algo `Algo`, temujin `Temujin`, desk `desk`, standards `standards`, jeby-us `jeby-us`). The first existing file wins and the link goes to `/doc/<resolved store-relative path>`, anchor kept. `source:` may hold several paths separated by `;` or `,`; each is resolved. `desk validate` warns on a path that resolves nowhere and names what it tried: `dead link (tried: docs/X.md, jeby Real Estate/docs/X.md)`.
- A glossary entry that points at a project's own document (`See [text](jeby Real Estate/docs/x.md#lot)`) makes the popover and `[[Term]]` go there instead of defining the term twice.
- The About page reads `docs/WHY.md` from the repo (`DESK_REPO`, or the store's `desk/` folder). The wheel carries a build-time copy only as a fallback; the source tree has no second copy.
- Every ticket page, and every `/doc/` page, ends with a "Mentioned in" list of the tickets whose bodies mention that ticket id or that path.
- Assignment: `drew assign <ID> --to NAME [--note] [--handoff]` sets `assignee`, `assigned_at` and an optional `handoff` (URL or Drive doc id) and logs a Comments line; `drew list --assignee`; the Projects and Board pages have an assignee filter chip; the review-week Review group is sub-headed `**Review · name**`; `drew stats` has a per-assignee table. Done means: assignee named and notified; the hand-off record linked.
- Errors file themselves: an unhandled UI exception shows "Desk hit a bug" (type, message, first desk frame) with a File as a bug button (`POST /bugs/file`): a desk bug ticket tagged `ui-error`, one per (exception type, route) per 24 hours, repeats add a Comments line. The wheel carries its build commit in `_build.py` and a `.build-stamp` next to the templates; when the stamp on disk differs from the commit the running process imported, every page shows a yellow "restart drew-ui" bar instead of failing.
- Auto-refresh: `GET /events/<ID>` and `GET /events` are server-sent events (file mtime polled every 1.5 s, no extra dependency); a small script reloads the page on a change, never while a field has focus or unsaved text (a "Changes available" bar shows instead). `[ui] autorefresh` in `.desk/settings.toml`, default on, switch in /settings.
- Checkboxes in Acceptance, How to validate, Prompt and Evidence have three states in the file: `- [ ]` open, `- [x]` done, `- [!]` not accepted. The UI shows `N of M done · K not accepted · J open` with a lime-and-red bar above the Acceptance list, and two boxes in a fixed column at the right edge that save in place (the reply to a box click carries the section again and the summary; the page's own write does not trigger the auto-refresh), under the headers Done and Not accepted (28 px boxes in 44 px hit areas; Tab to a line, `D` Done, `N` Not accepted, `Esc` closes the reason). Not accepted needs a reason of 3 or more non-space characters and a severity (Must fix, the default, Should, Nit). It is stored as the line, a byline comment, and a quoted reason under it: `- [!] text  <!-- by james 2026-11-08T03:41 -->` then `  > Not accepted (must fix): the reason`; `  > Evidence: assets/<ID>/<file>` lines may follow a line in any state. The saved reason shows under the line with severity, who, when, the evidence count and an Edit link; Cancel with no saved reason returns the line to open, and clearing a line removes its reason. The older inline form ` — not accepted: <reason>` is still read; `drew migrate checks --dry-run` (then `--apply`) converts it, and files with only `[ ]` and `[x]` are never touched. The ticket's Proof card lists the not-accepted lines, review-week lines show `N failed`, and `drew report` warns when it hands over a ticket that has one.
- Profanity filter: on by default, display only. Where people read (the UI, `drew show`) a listed word keeps its first and last letter and the middle becomes asterisks (f*****g); code spans, fenced code and the files themselves are never touched. Switch it off per person on /settings or with `[style] profanity_filter = false` in `.desk/settings.toml`; `drew show --raw` shows the words as written. The word list ships in `src/desk/profanity.py`; add your own words, one per line, in `<store>/profanity-extra.txt`.
- Every comment has a kind (DESK-0125): Question (the default), Suggestion, Concern or FYI, chosen with the pills above the box (`drew comment ID "text" --kind Concern` on the command line), stored at the end of the comment line as `<!-- kind: Concern -->` and shown as a small label beside the author; comments written before kinds have none, and the 10-minute duplicate rule ignores the kind. `Ctrl Enter` in the box posts through the same single-shot path as the button.
- Posting a comment is single-shot (DESK-0122): the first click turns Post into a spinner reading `Posting…`, locks the text, and ignores more clicks. Each post carries one key (the form's one-time token); `POST /ticket/<ID>/comment` with `Accept: application/json` stores at most one comment per key, answers a repeated key with the comment that is already there (`repeat: true`), and leaves the key good after a failure so Try again works. The reply carries the Comments again, so the page shows `Posted` (in a polite live region) and the new comment, highlighted for 2 s, without a reload. A failure (a non-2xx answer, or none within `data-timeout-ms`, 10 s) keeps the draft, shows `Couldn't post: Desk didn't answer. Your text is still here.` and turns the button into Try again; because the key is unchanged, Try again returns the comment if Desk stored it after all. A plain form post (no script) still works once per token.
- Screenshots as evidence (DESK-0089, DESK-0124): paste an image into the comment box or into a Not accepted reason (or drop a file on it, or use the Attach form under the box). PNG, JPEG, WebP or GIF up to `[attachments] max_mb` (10 MB by default; an existing `desk.toml` keeps the value it was created with) uploads to `tickets/assets/<ID>/` through `ops.attach` as `YYYY-MM-DD-HHMM-name.ext` (`POST /ticket/<ID>/attach`, magic bytes checked, not the extension) and appears as a thumbnail with a remove button (`POST /ticket/<ID>/attach/remove`; a screenshot the ticket already uses stays). The comment form's `Evidence for` list names an Acceptance line: posting writes the images and `(Evidence for line N)` into the comment and `  > Evidence: assets/<ID>/<file>` under that line, where the page shows `N screenshots`. A Not accepted reason takes the same paste and saves the evidence under its line. Anything else is refused with a message naming the allowed types (a too-large file names the limit); `drew attach` stays the CLI path.
- Done means comes from the Definition of Done, not from the ticket. The ticket page shows two blocks: **Acceptance (this ticket's outcome)** and **Done means (from the Definition of Done)**. The second renders, by reference and on every request, each module in the ticket's `dod:` from `standards/definition-of-done/<module>.md`; a project whose profile frontmatter has `dod_doc: <path in its repo>` (jRE: `dod_doc: docs/guides/DEFINITION_OF_DONE.md`) shows that file instead, and a repo that has `docs/guides/DEFINITION_OF_DONE.md` is picked up even without the line. Nothing is copied into tickets, each source links to `/doc/` and Edit in VS Code, and a `dod_doc` that points nowhere says so and falls back to the modules. **Rule: a ticket's Acceptance never restates DoD checks** (evidence attached, secrets, reduced motion, light and dark, decision recorded and the like live in the DoD files); `desk migrate acceptance --dry-run|--apply` removes the boilerplate lines earlier backfills wrote (exact text, or the text plus a generic ` in ...` tail with nothing ticket-specific in it) and prints each line and the count; `--apply` edits only the Acceptance section and leaves `updated` alone.
- Forms (comment, decide, check, move) carry a one-time token and their button disables on click and reads `Sending...` with a spinner (class `post-once`, one snippet in base.html), so a double click cannot post twice, and the redirect back carries `?done=<kind>` which renders a confirmation line at the top of the page (review-week Approve and Send back included); a reused token gets a friendly page and nothing is written.
