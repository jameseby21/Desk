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
  projects/<key>.md      # one profile per project; a ticket's project must exist here
  intake/<date>-<topic>/ # research hand-offs, copied in by `desk intake`
  DECISIONS.md           # process decisions, dated, append-only
  .desk/index.sqlite     # derived search index, gitignored, never the source of truth
```

Create one with `desk init [path] [--owner name]`.

`desk.toml`:

```toml
[desk]
owner = "james"              # the human who approves

[approval]
delegated_types = ["chore"]  # main may approve these ...
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
type: slice                  # slice | intake | decision | bug | research | chore
status: ready                # see lifecycle
priority: p1                 # p0 | p1 | p2 | p3
size: s                      # xs | s | m | l  (an l is split before it leaves triage)
owner: main                  # main | james | <project key> | a name
branch: jre/JRE-0042-doc-ownership
depends_on: [JRE-0041]
docs_to_update: [docs/README.md#docs-map]
dod: [core, code, docs]      # Definition of Done modules that apply
tags: [docs, weekly]         # optional, free-form strings; combos need at least one
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

## Lifecycle

```
inbox → triage → ready → claimed → in_progress → review → approved → done
                                        ▲             │
                                        └── changes_requested ◄┘
any open status → blocked (then `desk unblock` restores the previous status)
any open status → rejected (with a reason)
```

On screen the statuses wear Drew's words: Sniffed (inbox), Thrown (ready), Fetching (in_progress), Dropped (review), Fetched (done), Stuck (blocked), Let go (rejected), Chewed back (stale). The files and the command line use the plain words.

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
| `desk review new [--week YYYY-MM-DD] [--project management] [--owner james]` | owner / main | Create the week's **Ticket review** ticket (one per ISO week; chore, ready, p1, `review_by` Friday, tags `management, review-week`). Acceptance is generated: one checkbox per ticket waiting on the owner, grouped by ask (Approve, Choose, Answer, Provide, Do, Review). Refuses a second one for the same week, or a store without a `management` profile |
| `desk review sync [ID]` | owner / main | Regenerate the list between `<!-- review:start -->` and `<!-- review:end -->` (hand-written lines outside survive): decided tickets become `[x]`, new ones are added, vanished ones are kept as `(resolved)`. Writes the weekly KPI between `<!-- kpi:start -->` and `<!-- kpi:end -->` in Report: waiting at creation, added, decided this week, still waiting, median days in review, oldest waiting. Idempotent. The Today page links this week's review with "N waiting" |
| `desk list [--project] [--status] [--owner] [--stale] [--all]` | anyone | Table of tickets (open ones by default), grouped by project |
| `desk next --project jre [--json]` | project session | Highest-priority, oldest ready ticket, in full. `--json` is the SessionStart hook shape (`hookSpecificOutput.additionalContext`) |
| `desk show ID` | anyone | Print the ticket (with a `(formerly OLD)` header line when the project's prefix has changed) |
| `desk claim ID [--owner key-or-name]` | project session | `ready → claimed`; sets owner and branch |
| `desk start ID` | project session | `claimed → in_progress` |
| `desk report ID --file report.md` or `--text "..."` | project session | Writes Report, `→ review` |
| `desk decide ID --approve "why"` or `--changes "why"` `[--as james]` | owner / main | Records the words in Decision; `→ approved` or `→ changes_requested` |
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
| `desk home` | anyone | Print the resolved store |

Refusals print a plain reason and exit 1, so an agent sees exactly why. Output is UTF-8 on every platform, including Windows consoles.

## The UI

```bash
DESK_HOME=/path/to/store desk-ui      # http://127.0.0.1:8765  (DESK_UI_PORT to change)
```

Plain HTML forms over FastAPI and Jinja2; no build step. The only JavaScript is the vendored Mermaid, loaded on ticket pages that contain a diagram.

- **Today**: In your mouth (claimed and in progress), Dropped at your feet (review, and open decisions), Other balls in play, Chewed back (stale), Decided this week. A Good / Normal / Rough switch hides the chewed-back pile on a rough week and shows each ticket's smallest step. With `[life] combos = true`, a **Combos** card lists any two tagged tickets finished within 30 minutes of each other today: `"Dishes" and "Laundry" completed together, +100 for efficiency!` It is computed from `done_at`; nothing new is stored.
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
