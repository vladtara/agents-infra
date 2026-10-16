"""The team repo contract (TEAMS.md): check a team's files and build its agent entries."""

import copy
from dataclasses import dataclass

from teamlib import TeamError
from teamlib.context import GATEWAY_TEAMS
from teamlib.registry import NAME_RE
from teamlib.tree import Tree

MAX_ID = 64
REQUIRED_FILES = ("SOUL.md", "AGENTS.md")
FORBIDDEN_KEYS = ("workspace", "cwd", "agentDir", "default")


@dataclass(frozen=True)
class Resolved:
    entries: dict[str, dict]
    plugins: list[str]
    warnings: list[str]


def agent_id(team: str, folder: str) -> str:
    return f"{team}-{folder}"


def workspace(team: str, folder: str) -> str:
    return f"{GATEWAY_TEAMS}/{team}/workspaces/{folder}"


def merge(base: dict, override: dict) -> dict:
    """Objects merge key by key; any other value in override (arrays included) replaces base."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def plugin_specs(value) -> list[str] | None:
    """The list in a parsed plugins.json, or None when it has the wrong shape."""
    if not isinstance(value, dict) or set(value) - {"plugins"}:
        return None
    specs = value.get("plugins", [])
    if not isinstance(specs, list) or not all(isinstance(s, str) and s.strip() for s in specs):
        return None
    return list(specs)


def resolve(team: str, tree: Tree, parsed: dict, errors: dict[str, str]) -> Resolved:
    """Check the contract and build {agent id: entry}. Raises TeamError listing every problem.

    parsed and errors come from parsing tree.texts (JSON5).
    """
    problems = [f"{name}: {message}" for name, message in sorted(errors.items())]
    folders = sorted(tree.workspaces)
    if not folders:
        problems.append("no agents: add workspaces/<id>/ with SOUL.md and AGENTS.md")
    for folder in folders:
        if not NAME_RE.match(folder):
            problems.append(f"workspaces/{folder}: folder names must match {NAME_RE.pattern}")
        elif len(agent_id(team, folder)) > MAX_ID:
            problems.append(f"workspaces/{folder}: agent id {agent_id(team, folder)} is longer than {MAX_ID} characters")
        missing = [name for name in REQUIRED_FILES if name not in tree.workspaces[folder]]
        if missing:
            problems.append(f"workspaces/{folder}: missing {', '.join(missing)}")

    if "team.json5" not in tree.texts:
        problems.append("team.json5 is missing (use {} when the agents share no settings)")
    settings = {}
    for name in sorted(tree.texts):
        if name == "plugins.json" or name in errors:
            continue
        value = parsed.get(name)
        if not isinstance(value, dict):
            problems.append(f"{name}: must be an object")
            continue
        forbidden = [key for key in FORBIDDEN_KEYS if key in value]
        if forbidden:
            problems.append(f"{name}: must not set {', '.join(forbidden)} (teams.py manages them)")
        folder = name.removeprefix("agents/").removesuffix(".json5")
        if name.startswith("agents/") and folder not in tree.workspaces:
            problems.append(f"{name}: no matching workspaces/{folder}/ folder")
        settings[name] = value

    plugins = []
    if "plugins.json" in tree.texts and "plugins.json" not in errors:
        specs = plugin_specs(parsed.get("plugins.json"))
        if specs is None:
            problems.append('plugins.json: expected {"plugins": ["<package or ClawHub id>", ...]}')
        else:
            plugins = specs

    if problems:
        raise TeamError(f"team {team} does not follow TEAMS.md:\n  - " + "\n  - ".join(problems))

    entries = {}
    for folder in folders:
        entry = merge(settings.get("team.json5", {}), settings.get(f"agents/{folder}.json5", {}))
        _prefix_allow_agents(team, entry, set(folders))
        entry["workspace"] = workspace(team, folder)
        entries[agent_id(team, folder)] = entry
    warnings = [f"workspaces/{f} holds its own .git; remove it so the folder syncs" for f in sorted(tree.nested_git)]
    return Resolved(entries, plugins, warnings)


def _prefix_allow_agents(team: str, entry: dict, folders: set[str]) -> None:
    subagents = entry.get("subagents")
    if isinstance(subagents, dict) and isinstance(subagents.get("allowAgents"), list):
        subagents["allowAgents"] = [agent_id(team, a) if a in folders else a for a in subagents["allowAgents"]]
