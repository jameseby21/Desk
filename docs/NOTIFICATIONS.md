# Notifications: Desk on your phone and in your email

**Answer first.** Today Desk sends the morning brief and alerts free through two channels:

- **ntfy** pushes to a phone app.
- **Your own email account** sends mail through SMTP.
- **Text messages** have no free route that still works in 2026, so the phone push stands in for them.
- **The best paid service** in each category (Postmark for email, Twilio for text, Pushover for phone push) has its adapter built and switched off. You turn one on by adding its keys and naming it in `desk.toml`.

The quickest start takes three steps and about two minutes:

1. Run `drew notify pair`.
2. Subscribe to the topic it prints in the ntfy app.
3. Run `drew notify test`.

Owner: DESK-0289. Commands: [COMMANDS.md](COMMANDS.md#notifications).

## Phone push (the paired app)

| | Free now | Best | Cost | What you do |
| --- | --- | --- | --- | --- |
| Service | **ntfy** on the public ntfy.sh server (adapter `ntfy`, on once paired) | **Pushover** (adapter `pushover`, off) | ntfy: free, up to 250 messages a day. Pushover: 30-day trial, then $4.99 once per platform (iPhone, Android, desktop); 10,000 messages a month per app with no monthly fee | ntfy: install the app, run `drew notify pair`, subscribe to the topic, run `drew notify test`. Pushover: buy the app after the trial, register an application on pushover.net, put `PUSHOVER_TOKEN` and `PUSHOVER_USER` in notify.env, add `"pushover"` to `channels` |

- **ntfy needs no account.** The topic name works as the password, because every topic on ntfy.sh is public. `drew notify pair` makes a random 32-character topic, so nobody can guess it.
- **ntfy has room to spare.** The free server allows 250 messages a day and 60 quick requests at once, refilling one every 5 seconds. Desk's default budget is 10 a day.
- **Other free choices** were checked and left out. Telegram's Bot API needs a Telegram account and a bot. Gotify is self-hosted and has only an Android app.
- **Why Pushover is best:** it is one purchase with no subscription and has apps for every platform. Its emergency priority repeats until you acknowledge it.

## Email

| | Free now | Best | Cost | What you do |
| --- | --- | --- | --- | --- |
| Service | **Your own mail account over SMTP** (adapter `smtp`, on once configured; Gmail settings by default) | **Postmark** (adapter `postmark`, off) | SMTP: free with your account. Postmark: free for 100 emails a month, then $15 a month for 10,000 | SMTP: turn on 2-Step Verification, create an app password at myaccount.google.com/apppasswords, then put `SMTP_USER` (your address), `SMTP_PASSWORD` (the app password) and `SMTP_TO` (where the brief goes) in notify.env. Postmark: create an account, confirm a sender signature, then put `POSTMARK_TOKEN`, `POSTMARK_FROM` and `POSTMARK_TO` in notify.env and add `"postmark"` to `channels` |

- **Gmail app passwords need 2-Step Verification.** They are not offered when 2-Step Verification uses only security keys, on a work or school account, or with Advanced Protection.
- Another mail provider works too. Set `smtp_host` and `smtp_port` in `[notify]`; port 465 uses SSL, and any other port uses STARTTLS. Either way Desk checks the server's certificate and name before it sends the password.
- **Other services checked:**
  - Resend is free for 3,000 emails a month (100 a day), then $20 a month for 50,000.
  - Brevo's pricing page could not be read, so it was not checked.
- **Why Postmark is best:** it sends only transactional mail, so its sending reputation stays high. It also refuses mail from an unconfirmed sender (HTTP 422), so a mistake fails loudly instead of landing in spam.

## Text messages

| | Free now | Best | Cost | What you do |
| --- | --- | --- | --- | --- |
| Service | **None that works.** The phone push above is the text channel | **Twilio** (adapter `twilio`, off) | $0.0083 per message segment plus a carrier fee of $0.0035 to $0.005; a number costs $1.15 a month (toll-free $2.15). Registration for a 10DLC number as a sole proprietor: $4.50 once for the brand, $15 once for campaign vetting, $2 a month | Create a Twilio account, buy a number, finish 10DLC or toll-free verification, then put `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` and `TWILIO_TO` in notify.env and add `"twilio"` to `channels` |

- **Free email-to-text gateways are gone or going.** The sources are vendor blogs and could not be checked against the carriers' own pages:
  - AT&T closed txt.att.net on 2025-06-17.
  - T-Mobile's tmomail.net has dropped messages without a bounce since late 2024.
  - Verizon's vtext.com works only in part and is announced to close by 2027-03-31.
- **No free text API is reliable.** Desk does not build on any of them.
- **US registration is required.** Carriers block every unregistered sender.
  - **10DLC** (an ordinary 10-digit number) needs a brand and a campaign registration.
  - **Toll-free** numbers need verification. Twilio charges no fee for it. Since 2026 it needs a business registration number. From 2026-09-15 it also needs links to a privacy policy and terms; a submission without them is rejected with error 30493. Expect 2 to 10 business days.

## Settings (desk.toml, nothing secret)

```toml
[notify]
channels = ["ntfy", "smtp"]   # add "postmark", "twilio" or "pushover" to turn a paid adapter on
daily_budget = 10             # queued messages a day; the rest fold into the next brief
quiet_start = "21:00"         # no queued messages from here ...
quiet_end = "07:00"           # ... to here; they fold into the next brief ("" turns quiet hours off)
urgent_bypasses_quiet = false # true lets an urgent alert through during quiet hours
brief_time = "07:30"          # used by `drew notify schedule --print`
drain_minutes = 15
ntfy_server = "https://ntfy.sh"
smtp_host = "smtp.gmail.com"
smtp_port = 587
```

Leave out any line to keep its default.

## Secrets (outside every repository)

- **Where they live.** Secrets are in `%USERPROFILE%\.desk\notify.env`, or the file named by `DESK_NOTIFY_ENV`. The file has one `KEY=value` per line, and Desk refuses to write it inside a git repository.
- **Never printed.** `drew notify status` shows each key as its first two characters and a length.
- **One exception.** `drew notify pair` prints the ntfy topic once, because your phone needs it.
- **Never committed.** Never paste a key into a ticket, a chat or a commit. If one is ever exposed, change it at the service.

## Privacy rules (what may leave this computer)

- **An allow-list decides.** A notification carries ticket ids, titles, counts, dates and the brief's plain text. It never carries a ticket's Context, Report, comments or files, because Desk never reads them to build a message.
- **Every title and every queued line is scanned.** The private-term scan comes from the owner's private folder, and the secret-shape scan (`desk.secretscan`) runs too. A hit becomes `(title withheld)` or the bare ids. It is never sent. A withheld line with no ids goes as "1 alert (text withheld)", and only a known kind (alert, brief, digest-line) is named; any other becomes "note".
- **No scan, no titles.** If the private-term scan cannot be loaded, or its list is empty, only ids are sent, and the message says so.
- **Brief fields are allow-listed.** Only `id`, `title`, `ask`, `date`, `status`, `due`, `count` and `kind` survive, plus plain count tables. Any other field the brief grows is dropped.
- **One sender.** Other parts of Desk never send anything. They call `notify_queue.enqueue(...)`, and only `drew notify drain` sends. The filter, the budget and quiet hours therefore live in one place.
- **A broken queue line is skipped.** A line that is not readable JSON is counted, shown by `drew notify status` and `drew notify drain`, and stepped over. The lines after it still go.

## Budget and quiet hours

- **Past the budget, items wait.** After `daily_budget` messages in a day, `drew notify drain` holds every further item for the next brief.
- **Quiet hours hold everything.** That includes urgent alerts, unless `urgent_bypasses_quiet = true`.
- **The brief carries what was held.** It ends with "Held since the last brief: N" and up to ten lines, then clears the held list once it has been sent.
- **Some messages do not count.** The brief and `drew notify test` are sent on request and are not counted against the budget.

## Scheduling (Desk prints, you install)

- `drew notify schedule --print` prints two `schtasks` lines: the brief every day at `brief_time`, and a drain every 15 minutes.
- Both run `pythonw.exe`, so no console window flashes.
- Desk never installs them; you paste them yourself. `schtasks /Delete /TN "Desk\Notify brief" /F` removes one.

## How to validate

- `drew notify brief --dry-run` prints exactly the text that would be sent. It holds ticket ids and titles and no Context or Report text.
- `drew notify status` lists five channels: ntfy and smtp as free, and postmark, twilio and pushover as paid and off. No secret value appears in it.
- Run `drew notify pair`, then `drew notify test`. The phone shows "Desk test: notifications work".
- `pytest tests/test_notify.py` passes 22 tests: the privacy filter, the budget, quiet hours and each adapter with a fake transport. None of them uses the network.

## Sources (all read 2026-11-15)

- ntfy pricing and apps: https://ntfy.sh/
- ntfy publishing, the topic is the password, limits (250 a day, 60-request burst, one every 5 s): https://docs.ntfy.sh/publish/
- ntfy free tier, 250 a day (contributor comment, matches the docs): found through a web search on 2026-11-15
- Pushover price and trial: https://pushover.net/pricing
- Pushover message API: https://pushover.net/api
- Gotify (self-hosted, Android client): https://gotify.net/
- Telegram Bot API request form: https://core.telegram.org/bots/api
- Google app passwords: https://support.google.com/accounts/answer/185833
- Resend pricing: https://resend.com/pricing
- Postmark pricing: https://postmarkapp.com/pricing
- Postmark email API: https://postmarkapp.com/developer/user-guide/send-email-with-api
- Twilio US SMS pricing: https://www.twilio.com/en-us/sms/pricing/us
- Twilio Messages API: https://www.twilio.com/docs/messaging/api/message-resource
- 10DLC fees (Twilio's figures as quoted by third parties; check the Twilio console before paying): https://www.sociocs.com/post/twilio-10dlc-explained/ and https://calilio.com/blogs/10dlc-pricing
- Toll-free verification (no Twilio fee, business registration number, privacy and terms links): https://support.twilio.com/hc/en-us/articles/360038172934-Best-Practices-for-Using-Toll-Free-SMS-and-MMS-in-the-US-and-Canada and https://support.whitespark.ca/en/articles/14039903-toll-free-sms-registration-requirements-new-rules-in-2026
- Carrier email-to-text shutdowns (vendor blogs, not carrier pages): https://textbolt.com/blog/verizon-sms-gateway-address/ and https://www.txtimpact.com/blog/t-mobile-email-to-text-alternative and https://textbolt.com/blog/att-email-to-text-alternative/
