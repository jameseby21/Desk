"""The words for states on screen, one word per state in each style (DESK-0037). Plain is the default; Dogs (Drew or Fuzz) say
Thrown and Fetching; Cat has no words of its own yet and says the plain ones. The files and the command line are always plain."""

PLAIN = {
    "inbox": "Inbox",
    "triage": "Triage",
    "ready": "Ready",
    "claimed": "Claimed",
    "in_progress": "In progress",
    "review": "Review",
    "changes_requested": "Sent back",
    "approved": "Approved",
    "done": "Done",
    "blocked": "Blocked",
    "rejected": "Let go",
    "stale": "Stale",
}

DOGS = {
    "inbox": "Sniffed",
    "triage": "Triage",
    "ready": "Thrown",
    "claimed": "Claimed",
    "in_progress": "Fetching",
    "review": "Dropped",
    "changes_requested": "Sent back",
    "approved": "Approved",
    "done": "Fetched",
    "blocked": "Stuck",
    "rejected": "Let go",
    "stale": "Chewed back",
}

TABLES = {"plain": PLAIN, "dogs": DOGS, "cat": PLAIN}

# Headings of Today, which are phrases rather than states: (plain, dogs)
HEADINGS = {
    "needs": ("Needs you", "Dropped at your feet"),
    "hand": ("In hand", "In your mouth"),
    "waiting": ("Waiting for review", "Waiting for review"),
}


def screen(status: str, style: str = "plain") -> str:
    """The word for a status in a style; an unknown status is shown as written."""
    return TABLES.get(style, PLAIN).get(status, status)


def heading(key: str, style: str = "plain") -> str:
    plain, dogs = HEADINGS[key]
    return dogs if style == "dogs" else plain
