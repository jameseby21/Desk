# Automations

**Desk runs thirteen 'when X then Y' rules, each taken from a decision, a standards rule or a step people kept doing by hand.** Twelve are on by default and one is an example you switch on. They live in `automations.yaml` at the store root. `drew auto dry-run` shows what each rule would change today and why. `drew auto run` makes those changes, or you press **Run now** on `/automations`. Every change is made by Desk's own actor `automation`, never by a person, and one Undo takes back a whole run.

## How it works

- **Who writes.** Every change is written by the actor `automation`, and the timeline shows it with the kind `rule`. A rule cannot name who writes: a rule with `as:`, `actor:` or `by:` makes the file unusable. Nobody can type `--as automation` either, and a people.yaml entry keyed `automation` is never anyone's identity (not by OS user, alias or browser cookie), so a person's change never looks like a rule's (decision 110).
- **Every change names its rule.** Each line in the event log ends with `(rule <name>)`, and comments end the same way.
- **Undo.** One run is one batch (`b-` and 12 letters or digits). After **Run now** the page shows **Undo this run**, which takes back the whole run; once DESK-0284's Undo toast is on the page it offers the same and the page's own button hides. `drew auto undo <batch>` does the same from the terminal. It puts back every ticket the run changed. A ticket the run created is closed as rejected, with the reason, so its record stays. A ticket someone changed after the run is left alone and named, so no later work is lost. A notification already queued stays queued. A value the undo puts back still counts as the rule's, so later runs keep it up to date instead of treating it as set by hand.
- **What you set by hand stays.** A rule's `set`, `lane` and `priority` never change a value a person set, unless the rule says `override: true`. A tag a person added is never removed. The run says so when it holds back, for example `kept lane later (set by hand)`.
- **Status moves follow the usual rules.** A rule moves a ticket only through the same checks as `drew move`, so a rule can never approve and never skips How to validate or open subtickets. A move the checks refuse is listed as "held back" with the reason.
- **Notifications go to the queue.** A rule that wants to tell you something adds a line to `.desk/notify-queue.jsonl`. The sender (DESK-0289) decides when and how it reaches you.
- **You see it moving.** `drew auto run` prints a bar per rule and, inside a slow rule, `ticket k of m` at most once a second; `drew auto undo` prints `restoring k of n`. In the browser, Run now and Undo show which rule and which ticket of how many they are on, then one plain sentence.
- **Nothing runs on a clock by itself.** The sweep runs when a person or a session calls it (decision 63 retired timed sweeps). A ticket a rule changed earlier in the same run counts as touched today.
- **Pull requests.** Rules that read GitHub use the `gh` command with a 20-second limit. When `gh` is missing or offline, those rules are skipped with one plain line and the others still run. The `repos:` list names folders in the store or `owner/name`. A pull request belongs to the ticket whose id starts its title (decision 62).

## The rules that ship

| Rule | What it does | Source |
| --- | --- | --- |
| `merged-pr-closes-ticket` | An approved ticket whose pull request is merged moves to done, with the pull request link in Decision. | Standards rule 50 (merge after approval, then done with the commit); decision 62; idea 47 |
| `merged-pr-waits-for-approval` | A ticket whose pull request merged before approval gets the tag `pr-merged` and a note, and you are told. It does not close. | DESK-0195 (the ticket follows the work); rule 50 |
| `ci-state-on-review` | A ticket in review with an open pull request shows its CI state in `ci:` (green, red, pending or not run) and the link in `ci_pr:`. | Idea 48; standards rule 80 item 9 (CI must pass before merging); skill release |
| `subtickets-done-parent-to-review` | A main ticket in progress whose subtickets are all closed (at least one done) moves to review. | Idea 49; DESK-0208; event log: 86 "moved to review" notes written by hand |
| `hard-deadline-two-days` | A hard deadline two days out or missed, not yet in review: the tag `deadline-near`, the Now lane (urgent work), and an urgent note to you. Priority is not changed while DESK-0236 waits on your choice. | Idea 57; DESK-0193; DESK-0236; skill management |
| `repeated-ask-emergency` | When you ask for the same thing twice in a week, the ticket becomes p0 with the tag `emergency` in the Now lane (urgent work), and you are told. It overrides a hand-set priority. | Decision 60; standards rule 10; DESK-0158 |
| `review-waiting-a-week` | A ticket untouched in review for more than seven days gets `review-overdue`, and you are told once. | Skill management; rules.py `REVIEW_MAX_DAYS` |
| `approved-waits-to-ship` | A feature, upgrade, bug or release approved more than a day ago and not done gets `ship-pending`, and you are told once. | Skill release (built is not delivered) |
| `unblock-when-blocker-closes` | A ticket blocked on another ticket goes back to where it was once that ticket closes. | Standards rule 10; event log: 11 unblocks by hand |
| `stale-label` | A ticket untouched for more than the store's stale days (`desk.toml [stale] days`) gets the tag `stale` and a note. | DESK-0199; idea 52 |
| `stale-close` | A stale ticket untouched for the stale days again closes as rejected, with the reason. Your own tickets, tickets in review or approved, hard deadlines, and tickets that are blocked (waiting on you or anyone), claimed or in progress are labelled but never closed. | DESK-0199; standards rule 10 |
| `weekly-review-monday` | On Monday, or the first run after it, this week's ticket review is created and synced if it is missing. | Decision 24; idea 56; skill management |
| `monthly-recurring-example` (off) | An example of a ticket that repeats: one ticket a month from a template. Switch it on and change the template. `every:` is `weekly`, `monthly` or `yearly`; `day:` is a weekday, a day of the month (1 to 28) or `MM-DD`. | Idea 55; DESK-0047; decision 94 |

## Writing a rule

```yaml
- name: review-waiting-a-week          # one word with dashes, used once
  source: "skill management"           # required: the decision, rule or event pattern
  does: One plain sentence.            # shown by drew auto list and on the page
  override: false                      # true lets it change values a person set
  when: {status: review, untouched_days: 7, tags_none: [review-overdue]}
  then:
    - {add_tags: [review-overdue], notify: "{id} has waited {days} days in review: {title}"}
```

- **Conditions (`when`), all must hold:** `status`, `not_status`, `type`, `project`, `priority`, `tags_any`, `tags_none`, `field_set`, `field_empty`, `field_changed: {field, within_days}`, `untouched_days` (a number, or `store` for the stale days), `deadline_within_days` (hard deadlines), `no_hard_deadline`, `subtickets_all_closed`, `blocked_on_closed`, `repeated_ask`, `pr_merged`, `pr_open`, `owner_not` (`owner` means the store owner).
- **Actions (`then`), in order:** `set: {field: value}`, `lane`, `priority`, `add_tags`, `remove_tags`, `comment`, `notify` (with `urgent: true`), `move` (with `reason` or `note`; `link_pr: true` puts the pull request link on done), `unblock`, `ci`, `weekly_review`.
- **Schedules:** `every:` with `day:` and a `create:` template (`project`, `title`, `type`, `priority`, `size`, `owner`, `assignee`, `tags`, `sections`). A ticket is created once per period and carries `recurring: <rule>@<period>`.
- **Words you can use in text:** `{id}`, `{title}`, `{rule}`, `{days}`, `{n}` (subtickets), `{pr}` (the link), `{date}`, `{owner}`, `{due}` (a hard deadline in words, such as "missed by 2 days"), and `{period}` in templates.

## Found in the record but not automated

Each of these is a 'when X then Y' the record prescribes or people do by hand. Each is left to a person, or to code elsewhere, for the reason given.

| When, then | Source | Why it is not a rule here |
| --- | --- | --- |
| A ticket's workflow names its assignee | Decision 67; standards rule 27 | Already done when a ticket is created (`assign.yaml`, DESK-0168). A sweep would fill the blanks you use as your "not scanned yet" marker. |
| Major work gets review subtickets | Decision 103; standards rule 28 | Deciding what is "major" needs judgment. `drew gates` (DESK-0267) creates them when asked. |
| A parent is not approved while review subtickets are open | DESK-0267 | A guard on approval, so it belongs in the lifecycle rules, not a sweep. |
| Only the owner approves, with words | Decision 10; rules.py | Approval is a person's judgment. No rule can approve. |
| How to validate is written before review | Decision 41; standards rule 45 | Already refused by the lifecycle rules. |
| Your comments are work | Decision 56 | Each comment needs reading and a choice. Needs me lists them. |
| What you say moves tickets | Decisions 64 and 123 | Needs your words read and understood. |
| Close duplicates and merge related tickets | DESK-0210; decision 109; event log: over 1,000 duplicate notes | Sameness needs judgment, and a merge must carry every acceptance line. |
| Make a ticket a subticket of a bigger one | Event log: 177 "now a subticket of" notes | Needs judgment of what belongs together. |
| Sign-offs by a coworker in their domain | Decisions 108 and 110 | A person's own act. Automating it would be acting as them. |
| Approval for live | Decision 117; standards rule 32 | Your one action by design. |
| A project with no brand gets a ticket for the creative director | Decision 26 | Already `drew brand-check --create`. It joins the sweep once it is checked to skip projects that already have an open brand ticket. |
| A hand-off waiting on you that has not moved across two sweeps | DESK-0158 | `drew validate` already warns. It can become a notification once DESK-0289 sets the alert budget. |
| A hard deadline within two days joins the top priority band | DESK-0236 | Waits on your choice (A, B or C). The rule tags it and moves it to the Now lane (urgent work) but leaves priority alone. |
| Budgets per lane (how much work each list may hold) push work to the backlog | Decision 125; skill management | DESK-0283 owns lanes (now, planned, later, parked) and their budgets. |
| Analyses are reviewed by their review-by date | Decision 116; standards rule 29 | The analyses' dates live in project repos that Desk does not read yet. |
| Post outbound entries per coworker thread | Decision 78 | Writes to the team Drive, outside Desk. |
| Stop idle agents; read team folders on a timer | Decisions 63 and 88 | Not Desk's work. Decision 63 is also why nothing here runs on a clock by itself. |
