"""The words for states on screen, one word per state in each style (DESK-0037). Plain is the default; Dogs (Drew or Fuzz) add a dog word
in brackets after the plain label (Ready (thrown)); Cat has no words of its own yet and says the plain ones. The files and the command line are always plain."""

PLAIN = {
    "inbox": "Inbox",
    "triage": "To sort",
    "ready": "Ready",
    "claimed": "Claimed",
    "in_progress": "In progress",
    "review": "Sent for review",
    "changes_requested": "Changes requested",
    "approved": "Approved",
    "done": "Done",
    "blocked": "Blocked",
    "rejected": "Rejected",
    "stale": "Stale",
}

# DESK-0253 (standards rule 15, "Human-readable by default"): a mascot word may only sit beside the plain label, never replace it.
_DOG_WORD = {"inbox": "sniffed", "ready": "thrown", "in_progress": "fetching", "review": "dropped", "done": "fetched", "blocked": "stuck", "stale": "chewed back"}
DOGS = {k: (f"{v} ({_DOG_WORD[k]})" if k in _DOG_WORD else v) for k, v in PLAIN.items()}

TABLES = {"plain": PLAIN, "dogs": DOGS, "cat": PLAIN}

# Headings of Today, which are phrases rather than states: (plain, dogs). The dogs one keeps the plain label first.
HEADINGS = {
    "needs": ("Waiting on you", "Waiting on you (dropped at your feet)"),
    "hand": ("Claimed tickets", "Claimed tickets (in your mouth)"),
    "waiting": ("Waiting for review", "Waiting for review"),
}


def screen(status: str, style: str = "plain") -> str:
    """The word for a status in a style; an unknown status is shown as written."""
    return TABLES.get(style, PLAIN).get(status, status)


def heading(key: str, style: str = "plain") -> str:
    plain, dogs = HEADINGS[key]
    return dogs if style == "dogs" else plain


# Empty state of the flag component (Chaz F5): the only words that differ between Plain, Dogs and Cat.
FLAG_EMPTY = {
    "plain": ("Nothing flagged", "No masked comments and no rejected notes on this ticket."),
    "dogs": ("Nothing flagged (nothing to growl at)", "Drew checked: no masked comments and no rejected notes on this ticket."),
    "cat": ("Nothing flagged (nothing to hiss at)", "Soot checked: no masked comments and no rejected notes on this ticket."),
}


def flag_empty(style_name: str) -> tuple[str, str]:
    return FLAG_EMPTY.get(style_name, FLAG_EMPTY["plain"])
