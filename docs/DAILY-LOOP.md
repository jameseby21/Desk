# The daily loop

**What changed for you:** Desk now brings each decision to you, with a recommended answer and an Undo. Press `D` on any page, answer with one key, and the ticket records it. You do not need to open a ticket to act on it; the ticket is where the record lands afterwards.

Built as DESK-0282 from eleven parts (DESK-0283 to DESK-0293). The full reference for every page and command is [COMMANDS.md](COMMANDS.md).

## Try this first (five minutes)

1. Open Today and press `D`. Read the first question, the worker's recommendation and why. Press `Y` to agree, or `N` to send it back with a reason.
2. Look at the note at the bottom of the screen. It says what was saved and offers **Undo** for ten seconds. Press `Z` to take it back, then answer again.
3. Press `g` then `r` for the daily brief: what to decide today, hard deadlines this week, and what shipped yesterday.
4. Press `Ctrl K`, type `new: check the brief on my phone` and press Enter. That is a new inbox ticket, and it opens with its own Undo. Press `g` then `i` to sort the inbox: `1` Now, `2` Planned, `3` Later, `4` Park.
5. Open Settings (your name, top right). Pick a theme and a type size, the lane (how soon: Now, Planned, Later or Parked) you want Today to show, and check the quiet hours for your phone.

## Keys

| Key | What it does |
| --- | --- |
| `D` | Decide mode: your questions one at a time |
| `Y` / `N` / `S` | In Decide: agree, send back with a reason, snooze for a week |
| `J` or Space / `K` / `E` | In Decide and Sort the inbox: next, back, open the ticket |
| `1` to `4`, `M` | In Sort the inbox: Now, Planned, Later, Park; `M` merges it into another ticket |
| `Z` | Undo the last change while its note shows |
| `Ctrl K` | Search; on a ticket, what you can do to it; `new:` and a sentence makes a ticket |
| `/` | Search |
| `?` | Every key on one sheet (in Decide and Sort the inbox, their own keys) |
| `g` then a letter | Go to `t` Today, `b` Board, `n` Needs me, `d` Decide, `i` Sort the inbox, `r` the brief, `c` Changes, `u` the Undo log |

No key does anything while you are typing in a box.

## Pages

| Page | What it is for |
| --- | --- |
| `/` Today | Needs me first. The switches at the top narrow it to Now, Planned, Later or Parked; snoozed tickets fold into one line. When nothing needs you, it says so and lists what the agents are doing. |
| `/decide` | One question at a time, the recommended answer picked, and what each button does next |
| `/triage` | Sort the inbox one ticket at a time |
| `/brief`, `/brief/week` | The day on one screen; the week written for you, one printed page |
| `/changes` | What others changed since you last looked |
| `/undo` | Every change this week, with Undo for one change or a whole action |
| `/capture`, `/capture/paste` | Make a ticket from one sentence; turn a pasted chat or email into tickets |
| `/automations` | The rules, what each would change today, **Run now** and **Undo this run** |
| `/settings` | Every switch once: style, theme, type size, what Today shows, Motivation, phone and email, what others may approve for you |

## What keeps you safe

- Every change is one action with one Undo. Nothing is deleted; parking keeps a reason.
- Snooze takes a ticket off Today, Needs me, the brief, Decide and Sort the inbox until its date. Showing only Now, or a rough week, never hides a hard deadline or an overdue question.
- Only you can answer in Decide, from this computer. Nobody can act as you. Rules write as `automation`, never as you.
- Your phone and email get ticket ids, titles that pass the private-word check, and counts. Quiet hours (9 pm to 7 am unless you change them) hold messages for the morning brief.

## Not done yet

- Rules run only when you press **Run now** or type `drew auto run`. Nothing runs them on a timer yet, so a deadline alert waits for that.
- Quiet hours and the daily limit are changed in desk.toml under `[notify]`; Settings shows them.
- Decide shows every question you have, even when Today shows only Now.

## Setting up your phone

`drew notify pair` makes a private topic and prints the phone steps. `drew notify schedule --print` prints the two scheduled tasks (morning brief, sending every 15 minutes) for you to paste into a Command Prompt. Desk never installs them itself. More in [NOTIFICATIONS.md](NOTIFICATIONS.md).
