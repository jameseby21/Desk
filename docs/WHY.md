# Why Desk

Desk is a free alternative to Jira and the paid work-management tools: your tickets, in your repo, with proof.

Ticket tools count cards. Desk counts proof.

Nothing closes without evidence. Every fix leaves a lesson behind. Your tickets are Markdown files in your own git repo, so Desk is a lens on your work, not a landlord. And it runs your real life on the same board as your work, because the person doing both is the same person.

## What we flushed

Ticket tools grew up making managers feel in control. These are the habits that came with that, and what Desk does instead.

| The habit | Why it fails | Desk |
| --- | --- | --- |
| Story points | Astrology, with a Fibonacci sequence. | Hours, as a range, with how sure you are and what you are waiting on. |
| "Done" means somebody dragged a card | The card moved. Did the work? | Done means proof: the command, where it ran, what it printed, plus the owner's own words. |
| Ten columns of status theater | Everything is "in progress", nothing is. | One ball per person or agent. You are holding it or you are not. |
| Everything is P1 | When everything is urgent, the loudest wins. | One ordered queue. Moving a ticket up is a decision, and it is recorded. |
| The backlog graveyard | Where good ideas go to be ignored politely. | Three weeks untouched and it comes back to you. Keep it with a reason, or let it go with one. |
| Approval by emoji | A thumbs-up is not a decision. | Approval is your words on the record. "Looks good" doesn't count. |
| Decisions buried in comment threads | Forty replies later, who decided what? | Talk and decisions live apart. A decision is one line, with a name and a date. |
| Close the bug, forget the bug | So it comes back in March. | Every fix buries a lesson: the rule and the test that keep it from happening twice. |
| Plans that assume a perfect week | No week is. | Tell Desk how the week is going. On a rough one it shows each ticket's smallest step, and that still counts. |
| Your tickets locked in their database | Export is a premium feature, apparently. | Tickets are Markdown files in your own repo. Desk reads them; it never owns them. |

## How a ticket moves

| In the file | On screen (Plain) | Means |
| --- | --- | --- |
| `inbox` | Inbox | Captured: the problem and the evidence for it |
| `ready` | Ready | Committed: done-means, a range of hours, a review date |
| `in_progress` | In progress | Someone is holding it. One each |
| `review` | Review | At the owner's feet, with proof attached |
| `done` | Done | Approved in words, merged, and a lesson buried |
| `blocked` | Blocked | Says what it is waiting on and who |
| `rejected` | Let go | Always says why |

Plain words are the default. If you want a Scottish Terrier named Drew fetching your tickets, or a cat named Soot, that is a per-person setting; the files and the command line never change.

## Every ticket says what it needs from you

One field, `ask`, tells you what a ticket wants before you read it: approve this, choose one, set a direction, answer a question, provide something, give feedback, do a step, just so you know, or nothing. The reply box matches the ask. You never have to work out what a ticket is asking.

## Work and life on one board

A job application, a specialist appointment, a SQL exercise and a pull request are all things you intend to do this week. Desk keeps them on one board with the same honesty: a soft goal is recorded as what it was, a partial day counts, and two things finished together earn a line ("Take a break" and "Duolingo lesson" completed together, +100 for efficiency). Turn it off if you want a work-only board; it is a setting, not a mandate.

## For people and for agents

The UI is for the owner. The command line is for agents: `drew next` prints the next ready ticket, `drew claim` takes it, `drew report` hands it back with proof, and `drew decide` records the owner's words. Every ticket carries a Prompt section, so a ticket is a static hand-off any agent or person can pick up without the conversation that produced it.

## Free, and paid

**Free, forever (this repo, MIT):** tickets as Markdown in your own git repo; the `drew` command line for you and for agents; the local web app (Today, the queue, tickets, decisions, stats); the lesson library for one repo; proof from the commands you run.

**Paid, hosted by JA Insights (when it exists):** sync across your machines without running a server; one lesson library across all your projects; proof captured from your CI without copy and paste; approvals from your phone with a full record; seats for AI agents next to your people. What you keep if you leave: everything, because it was always in your repo.

## The proof that it works

Desk is built with Desk. Its own tickets, including the ones for this page, live in the author's store, and the author's job search, appointments and practice sit on the same board as the code. The numbers (`drew stats`) are the development log.
