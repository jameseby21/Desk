# The priority score

**One number from 0 to 100 on every open ticket, computed when Desk reads the store and never stored.** It comes from the fields Desk already holds, the project profile, the track and the comments. A person sets at most two fields.

**Where it is decided.** DECISIONS.md 54 (one metric at every level) and 90 (James's rules of 2026-11-09). Built under DESK-0135, 0136, 0137 and 0214; James tests it in DESK-0212.

**Where it shows.** Today's rows, the board's columns, the Projects page, `drew next`, `drew list --sort score` (and a `score` column), `drew explain <ID>`, `drew stats`. Every card and the ticket page show the score and its top three reasons in plain words. Two lists keep their own order: Immediate Action Required (the hard deadline, DESK-0193) and Needs me (emergencies, then overdue asks, then the review date, DESK-0149).

## 1. The formula

**Start at 10 and add every term; clamp the result.**

- `raw = base + value + date + unblocks + type + state + open dependencies + owner waiting + age + stuck or sinking + effort + project + track + tags + no clients + people + owner load`
- An ordinary ticket scores `round(raw)` held between 0 and 89.
- An emergency scores `90 + min(9, round(raw / 10))`, so it can never fall below an ordinary ticket. Its open dependencies move into the band with it (they say "an emergency waits on it").
- Closed tickets (done, rejected) have no score.

**The two fields a person may set.**

- `value: 1..5`, what the ticket is worth when done. Unset, the hand-set `priority` stands in (p0 is 5, p1 is 4, p2 is 3, p3 is 2), so every ticket scores on day one. Priority is one input now, not the order.
- `deadline:` with `deadline_kind: hard | soft | sprint` (DESK-0193), a date someone is counting on. Unset, `review_by` stands in at half weight.

## 2. Every signal, its source and its default weight

| Signal | Source field | Default points | In words on a card |
| --- | --- | --- | --- |
| base | none | +10 | every ticket starts here |
| value | `value`, else `priority` | (value − 1) × 6, so 0 to 24 | `hand-set p0`, `value 5` |
| date | `deadline` and `deadline_kind`, else `review_by` | 20 × (1 − d/21) with d days left, 0 at 21 days; past the date 20 + 1 per day late, cap 25. Hard counts 1.0, soft 0.75, sprint 0.5, review_by 0.5. The larger wins | `due in 2 days`, `missed by 3 days`, `review by Nov 14` |
| unblocks | `depends_on` of other tickets and this ticket's `blocks`, followed transitively, open only | 4 × log2(1 + n), cap 20 (1 gives 4, 3 gives 8, 9 gives 13) | `unblocks 9 tickets` |
| type and ask | `type`; for a decision the title's first word | bug 8, release 8, decision 6, feature 4, upgrade 3, task 3, research 3, docs 2, intake 2; approve or choose +2, answer or provide +1; cap 10 | `a bug`, `a choice for James` |
| state | `status` | approved +10, review +8, sent back +8, in progress +6, claimed +4, ready 0, triage −5, inbox −10, blocked −15 | `in review`, `blocked` |
| open dependencies | `depends_on` with an open ticket, on a ready ticket | −10 | `waiting on 2 open dependencies` |
| owner waiting | a Comments line by the store owner with no later line by `patch` and no change to the ticket on a later day | +15 | `James asked and nobody answered` |
| age | `created` | +0.2 per day, cap 8 | `12 days old` |
| stuck | `updated`, on claimed, in progress, review, sent back | +0.4 per quiet day after 7, cap 8 | `stuck for 12 days` |
| sinking | `updated`, on inbox, triage, ready, blocked | −0.25 per quiet day after `[stale] days` (21), cap −8 | `untouched for 30 days` |
| effort | `effort_hours` midpoint ÷ `confidence` (0.7 unset), else `size` xs 1 h, s 3, m 6, l 12 | up to 1 h +6, 3 h +3, 6 h 0, 12 h −3, more −6 | `about an hour`, `about 9 hours, uncertain` |
| project | profile `weight:`, else the first word of `status` | active +6, new +2, pending 0, exploring 0, parked −10, dormant −10 | `project parked` |
| track | `track`, against the tracks map | Go-live +6, First real use +3, Backlog −3, Wishlist −6; project and track together capped at ±15 | `track Go-live` |
| tags | `tags`, against the tags map | health +30, repeatable-process +8 | `health comes first`, `solves a repeatable process` |
| no clients | `type` feature or upgrade; profile `clients`, `monetization`, `enabler` | −20 when clients is 0 or monetization is not active; 0 when enabler is true | `no clients yet: feature work waits` |
| people | owner, assignee, `associated`, tags `with:<key>`; the private file | the key's boost, the largest one, cap 30 | the words the private file chose, else `a person who comes first` |
| owner load | the assignee's claimed or in-progress tickets, on a ready ticket | −4 (never on an emergency) | `Patch already holds a ball` |
| emergency | tag `emergency`, or `james-asked-Nx` with N of 2 or more | no points: the 90 to 99 band | `an emergency` |

**James's rules, as encoded (DECISIONS 90).**

- Health: the `health` tag is worth +30; its urgency is the hard deadline, which grows as the date nears and keeps growing after it is missed.
- No clients: feature and upgrade work on a project with `clients: 0` or `monetization` other than `active` loses 20 points. Bugs, tasks, decisions, docs, research and releases are never reduced. A project with `enabler: true` is exempt (DESK-0213; the default until James answers).
- People: a ticket tied to a key with a boost in the private file gains that boost. The file is TOML outside the repo, at the path in `[priority] private_weights`; a missing file means no people terms. Its contents are never printed; `drew explain` says only present or absent.
- Repeatable process: the `repeatable-process` tag adds 8, and the Projects page counts each project's done tickets that carry it.

## 3. Ties

**Two tickets with the same whole-number score are ordered by, in turn:** the higher raw; more open dependents; the nearer date (`deadline`, then `review_by`); fewer expected hours; the older `created`; the id. The order is fully determined, so two people see the same list.

## 4. Rollups

**Every group ranks by the highest score inside it, and shows the sum as load and the mean as health.** A project scoring 78 means the best thing to do in it scores 78, the same scale as a ticket. Sum would crown the biggest pile; mean would punish filing a small ticket.

| Level | Group | Where |
| --- | --- | --- |
| track | open tickets with that `track` | Projects page, board group rows |
| project | open tickets of the project | Projects page, board project rows |
| person | open tickets assigned to that person | `drew stats`, `/scores?assignee=` |
| company | open tickets of the projects in that `area` | the board's company rows |
| store | all open tickets | `drew stats` |

- Groups tying on max are ordered by the sum of their three best tickets, then by sum.
- Clicking a rollup opens `/scores?...`, the group's tickets in score order with their reasons.

## 5. Five real tickets scored by hand

**Facts as of 2026-11-10 (today), from the store, read-only.** Terms are rounded in the table; raw is the exact sum. The same five, rebuilt in a fixture, are the test `test_the_five_worked_examples_reproduce_the_documented_order_and_scores`.

| Ticket | Facts | Terms | raw | score |
| --- | --- | --- | --- | --- |
| JEBYUS-0001 Go live: merge jeby-us PR #1 | release, review, p0, s, review_by 11-14, tags emergency and james-asked-3x, Go-live, project active | 10 + 24 value + 8.1 review by + 8 release + 8 in review + 0.6 age + 3 effort + 6 active + 6 Go-live | 73.7 | **97** (band) |
| DESK-0157 Choose: threads use tickets directly | decision for James with no options listed (an answer), ready, p0, xs, review_by 11-13, 9 open tickets wait on it, First real use, active | 10 + 24 + 8.6 review by + 13.3 unblocks 9 + 7 an answer + 0.4 age + 6 effort + 6 active + 3 First real use | 78.3 | **78** |
| a health appointment (life) | task, ready, p1, xs, tag health, hard deadline 11-12, project active, no clients | 10 + 18 + 18.1 due in 2 days + 3 task + 0.4 age + 6 effort + 6 active + 30 health | 91.5 | **89** (the ordinary cap) |
| JRE-0007 Foreclosure screen weeks 1-4 | feature, ready, p1, m, Backlog, jre active, clients 0, monetization planned | 10 + 18 + 4 feature + 0.4 age + 0 effort + 6 active − 3 Backlog − 20 no clients | 15.4 | **15** |
| LAB-0002 Rainbow Road (as if open) | feature, ready, p1, m, lightinglab parked, clients 0, monetization none | 10 + 18 + 4 feature + 0.4 age + 0 effort − 10 parked − 20 no clients | 2.4 | **2** |

**Why this order.**

- The emergency is first whatever its raw; it leaves the band only when it closes.
- The health appointment is second because of the tag and a hard deadline two days out; without a deadline it would score 71, still above the p0 decision.
- The decision beats every feature because nine tickets wait on it and it takes minutes to answer.
- The two features sit low because nobody pays for them yet; the parked project loses ten more. A bug on either project would not be reduced.

## 6. Defaults chosen for DESK-0134's questions

**Until James answers, the recommended answer stands; each is one line in desk.toml to change.**

1. Fields: `value` plus `deadline` (the DESK-0193 field serves as the due date).
2. p0..p3 fills in value until `value` is set; priority stays a required field.
3. An emergency leaves the band when it closes (done or rejected), not at review.
4. An emergency pulls its open dependencies into the band, transitively.
5. Rollups rank by max; sum and mean are shown.
6. Not-started tickets sink after the stale window; balls in play rise after seven quiet days.
7. Project weight reads the first word of the profile's `status`, unless the profile sets `weight:`.
8. Track weights: Go-live +6, First real use +3, Backlog −3, Wishlist −6.
9. A team is a profile `area` (the company rows); a person is an assignee.
10. Personal projects count in the store total; the company rows keep them in their own area.
11. The score shows as a number with its reasons beside it.

## 7. How James changes a weight

**Four levels; the nearest to the ticket wins. `drew explain <ID>` names the level that set each weight.**

- Store: `[priority]` in desk.toml. Scalars at the top (`base = 10`, `no_clients = -20`), maps as tables (`[priority.tags] health = 30`, `[priority.tracks] "Go-live" = 6`, `[priority.types]`, `[priority.status]`). Unknown keys are ignored.
- Track rules: a `weights:` map in `tracks/rules.yaml` (`"Go-live": 6`) overrides the store's tracks map.
- Project: a `priority:` block in `projects/<key>.md`, written inline (`priority: {tags: {health: 50}, no_clients: -5}`), overrides both for that project's tickets.
- People: `private_weights = "~/.claude/jeby-private/priority.toml"` in `[priority]`; the file holds `[people.<key>] boost = 25`, an optional `projects = ["jre"]` scope and optional `words = "family priority"`. Nothing else may change there.

```toml
[priority]
private_weights = "~/.claude/jeby-private/priority.toml"
no_clients = -20

[priority.tags]
health = 30
repeatable-process = 8
```
