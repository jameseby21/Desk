# Desk

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
  desk.toml              # owner, delegation, git sync, stale window
  tickets/               # one file per ticket: KEY-0001-slug.md
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
```

## A ticket

`tickets/JRE-0042-add-the-doc-ownership-map.md`. Ids are the project key, upper-cased, plus a number that counts per project. Frontmatter is validated on every write.

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
effort_hours: [1, 2]         # optional [low, high]
confidence: 0.8              # optional, 0..1
review_by: 2026-11-14        # optional
smallest_step: "Open the preview and read the About page."   # the step for a rough week
blocked_on: ""               # required when status is blocked
source: intake/2026-11-07-em-round2/CLAUDE_CODE_HANDOFF.md#stage-2
created: 2026-11-07
updated: 2026-11-07
---

## Context
## Outcome
## Acceptance
- [ ] checkboxes a test or the owner can verify
## Docs to update
## Out of scope
## Report
## Decision
## Comments
```

Headings are fixed so the CLI and the UI can find them. The worker writes **Report** (what changed, how it was verified, the commands run). The owner writes **Decision**, through `desk decide` or the UI, and Desk records it as a dated line: `- 2026-11-07 · james · approved · "Ship it after the DNS lands."`

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
- A ticket cannot be `done` while any `depends_on` ticket is not `done`.
- `blocked` needs `blocked_on` (a ticket or a person) and a reason.
- Decisions need words: approve, send back and reject all take a reason of at least four characters.
- Dangling `depends_on`, unknown projects, bad enum values and missing required fields are errors. A size `l` outside triage is a warning.
- Stale is computed, not stored: any open ticket whose `updated` is older than `[stale].days`.

## Commands

| Command | Who | What |
| --- | --- | --- |
| `desk init [path] [--owner]` | owner | Create a store |
| `desk new --project jre --type slice [--priority p1 --size s --owner main --dod core,code --source path#sec] "title"` | main | Next id for the project, from the template; prints the path |
| `desk list [--project] [--status] [--owner] [--stale] [--all]` | anyone | Table of tickets (open ones by default) |
| `desk next --project jre [--json]` | project session | Highest-priority, oldest ready ticket, in full. `--json` is the SessionStart hook shape (`hookSpecificOutput.additionalContext`) |
| `desk show ID` | anyone | Print the ticket |
| `desk claim ID [--owner key-or-name]` | project session | `ready → claimed`; sets owner and branch |
| `desk start ID` | project session | `claimed → in_progress` |
| `desk report ID --file report.md` or `--text "..."` | project session | Writes Report, `→ review` |
| `desk decide ID --approve "why"` or `--changes "why"` `[--as james]` | owner / main | Records the words in Decision; `→ approved` or `→ changes_requested` |
| `desk done ID [--commit sha]` | project session | `approved → done`, after the merge |
| `desk block ID --on "ticket or person" "reason"` | anyone | `→ blocked`, remembers where it came from |
| `desk unblock ID [--to status]` | anyone | Back to the status before the block |
| `desk reject ID "reason"` | owner / main | Let it go, with a reason |
| `desk move ID --to status [--reason] [--as]` | main | Any other guarded move (`inbox → triage → ready`, `changes_requested → in_progress`) |
| `desk comment ID "text" [--as name]` | anyone | Dated line under Comments |
| `desk validate` | pre-commit, CI | Schema, lifecycle rules, dependencies. Exit 1 on errors |
| `desk index` | UI, pre-commit | Rebuild `.desk/index.sqlite` from the files |
| `desk board [--project] [--all]` | owner | The board in the terminal |
| `desk intake <folder-or-zip> --topic "x" --project key` | main | Copy a hand-off into `intake/<date>-<topic>/` and open an `intake` ticket. Refuses packages containing `PRIVATE_CONTEXT.md` or a `jeby-private` path |
| `desk home` | anyone | Print the resolved store |

Refusals print a plain reason and exit 1, so an agent sees exactly why.

## The UI

```bash
DESK_HOME=/path/to/store desk-ui      # http://127.0.0.1:8765  (DESK_UI_PORT to change)
```

Plain HTML forms over FastAPI and Jinja2; no build step, no JavaScript. htmx was not vendored (no network fetch during the build); forms do the job.

- **Today**: In your mouth (claimed and in progress), Dropped at your feet (review, and open decisions), Other balls in play, Chewed back (stale), Decided this week. A Good / Normal / Rough switch hides the chewed-back pile on a rough week and shows each ticket's smallest step.
- **Board**: one column per status, filter by project.
- **Ticket**: every section rendered, acceptance checkboxes with a count, the Report beside them with a red **Not run** tag when it is empty, Approve and Send back forms (both require words, acting as the configured owner), a guarded Move form, comments.
- **Projects**: one row per `projects/*.md` with counts by status and last activity.

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

Architecture: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## License

MIT, copyright JA Insights LLC.

## Ticket-id prefixes

A project's ticket prefix is the `prefix:` field in the frontmatter of `projects/<key>.md` (for example `prefix: FP` for `fightplan`). Without one, the key is uppercased with hyphens removed.
