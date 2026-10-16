"""Git-managed OpenClaw teams: clone team repos, check them, sync them, apply their agents."""


class TeamError(Exception):
    """A team operation failed; the message is meant for the user."""
