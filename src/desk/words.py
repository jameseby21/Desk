"""Drew's words on screen, plain words in the files."""

SCREEN = {
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


def screen(status: str) -> str:
    return SCREEN.get(status, status)


# Empty state of the flag component (Chaz F5): the only words that differ between Plain, Dogs and Cat.
FLAG_EMPTY = {
    "plain": ("Nothing flagged", "No masked comments and no rejected notes on this ticket."),
    "dogs": ("Nothing to growl at", "Drew checked: no masked comments and no rejected notes here."),
    "cat": ("Nothing hissed at", "Soot checked: no masked comments and no rejected notes here."),
}


def flag_empty(style_name: str) -> tuple[str, str]:
    return FLAG_EMPTY.get(style_name, FLAG_EMPTY["plain"])
