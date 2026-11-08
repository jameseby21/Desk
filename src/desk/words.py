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
