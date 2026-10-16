"""Where team data lives, and the state shared by one teams.py run."""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from teamlib.registry import Registry

GATEWAY_CONFIG = "/home/node/.openclaw"
GATEWAY_TEAMS = "/home/node/teams"


@dataclass(frozen=True)
class Paths:
    """Host paths under stacks/openclaw/."""

    stack: Path

    @property
    def data(self) -> Path:
        return self.stack / "data"

    @property
    def teams(self) -> Path:
        return self.data / "teams"

    @property
    def config(self) -> Path:
        return self.data / "config"

    @property
    def agents_d(self) -> Path:
        return self.config / "agents.d"

    @property
    def openclaw_json(self) -> Path:
        return self.config / "openclaw.json"

    @property
    def keys(self) -> Path:
        return self.data / "team-keys"

    @property
    def remotes(self) -> Path:
        return self.data / "team-remotes"

    @property
    def gitdirs(self) -> Path:
        return self.data / "team-gitdirs"

    @property
    def registry(self) -> Path:
        return self.stack / "teams.toml"

    def clone(self, team: str) -> Path:
        return self.teams / team

    def remote(self, team: str) -> Path:
        return self.remotes / f"{team}.git"

    def git_dir(self, team: str) -> Path:
        """Git data of a clone, kept outside data/teams so agents cannot change hooks or config."""
        return self.gitdirs / team


@dataclass
class Context:
    repo: Path
    paths: Paths
    runner: object
    registry: Registry
    secrets: list[str] = field(default_factory=list)
    out: TextIO = sys.stdout

    def say(self, message: str) -> None:
        print(message, file=self.out, flush=True)
