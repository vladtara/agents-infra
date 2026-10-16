"""teams.toml: the teams this VM runs and the git identity of its sync commits."""

import json
import re
import socket
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from teamlib import TeamError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
VM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


@dataclass(frozen=True)
class Team:
    name: str
    url: str
    branch: str = "main"


@dataclass
class Registry:
    vm: str
    git_name: str
    git_email: str
    teams: dict[str, Team] = field(default_factory=dict)


def default_vm() -> str:
    host = socket.gethostname().split(".")[0].lower()
    return re.sub(r"[^a-z0-9-]", "-", host).strip("-") or "vm"


def load(path: Path) -> Registry:
    """Read teams.toml; a missing file means no teams and default identity."""
    data = {}
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise TeamError(f"{path}: {exc}") from exc
    git = data.get("git", {})
    vm = git.get("vm") or default_vm()
    if not isinstance(vm, str) or not VM_RE.match(vm):
        raise TeamError(f"{path}: git.vm must match {VM_RE.pattern}")
    registry = Registry(vm=vm, git_name=git.get("name") or f"OpenClaw {vm}", git_email=git.get("email") or f"openclaw@{vm}")
    for name, raw in data.get("teams", {}).items():
        if not NAME_RE.match(name):
            raise TeamError(f"{path}: team name {name!r} must match {NAME_RE.pattern}")
        if not isinstance(raw, dict) or not isinstance(raw.get("url"), str) or not raw["url"]:
            raise TeamError(f"{path}: [teams.{name}] needs a url")
        branch = raw.get("branch", "main")
        if not isinstance(branch, str) or not BRANCH_RE.match(branch):
            raise TeamError(f"{path}: [teams.{name}] branch must match {BRANCH_RE.pattern}")
        registry.teams[name] = Team(name, raw["url"], branch)
    return registry


def save(path: Path, registry: Registry) -> None:
    lines = ["[git]", f"name = {_quote(registry.git_name)}", f"email = {_quote(registry.git_email)}", f"vm = {_quote(registry.vm)}"]
    for team in registry.teams.values():
        lines += ["", f"[teams.{team.name}]", f"url = {_quote(team.url)}", f"branch = {_quote(team.branch)}"]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)


def check_new_name(name: str, existing: Iterable[str]) -> None:
    """A new team name must be valid, unused, and not a dash-prefix of another team (agent ids would clash)."""
    if not NAME_RE.match(name):
        raise TeamError(f"team name {name!r} must match {NAME_RE.pattern}")
    for other in existing:
        if other == name:
            raise TeamError(f"team {name} is already in teams.toml")
        if name.startswith(other + "-") or other.startswith(name + "-"):
            raise TeamError(f"team name {name!r} overlaps team {other!r}: their agent ids would be ambiguous")


def check_branch(branch: str) -> None:
    if not BRANCH_RE.match(branch):
        raise TeamError(f"branch {branch!r} must match {BRANCH_RE.pattern}")


def _quote(value: str) -> str:
    # JSON string escapes are valid TOML basic-string escapes.
    return json.dumps(value, ensure_ascii=False)
