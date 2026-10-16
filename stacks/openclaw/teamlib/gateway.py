"""Apply a team's agents to openclaw.json and agents.d, and install its plugins."""

import copy
import hashlib
import json

from teamlib import TeamError, contract
from teamlib.context import GATEWAY_CONFIG, GATEWAY_TEAMS
from teamlib.runner import json_out
from teamlib.tree import Tree

MAIN_WORKSPACE = f"{GATEWAY_CONFIG}/workspace"
# Spike fallback: True writes merged entries straight into openclaw.json instead of agents.d includes.
INLINE = False


def include(agent_id: str) -> dict:
    return {"$include": f"./agents.d/{agent_id}.json5"}


def owned(team: str, roster: dict) -> set[str]:
    """Agent ids in roster that teams.py manages for team."""
    prefix = f"{GATEWAY_TEAMS}/{team}/workspaces/"
    return {
        key for key, value in roster.items()
        if key.startswith(f"{team}-") and isinstance(value, dict)
        and (value == include(key) or str(value.get("workspace", "")).startswith(prefix))
    }


def plan_roster(config: dict, team: str, entries: dict[str, dict] | None, *, inline: bool = INLINE) -> dict:
    """A copy of config with team's agents set to entries; None or {} removes the team."""
    config = copy.deepcopy(config)
    agents = config.setdefault("agents", {})
    roster = agents.setdefault("entries", {})
    mine = owned(team, roster)
    wanted = entries or {}
    taken = sorted(key for key in wanted if key in roster and key not in mine)
    if taken:
        raise TeamError(f"agent id(s) already used outside team {team}: {', '.join(taken)}")
    implicit_main = not roster
    for key in mine - wanted.keys():
        del roster[key]
    if not wanted:
        return config
    if implicit_main:
        # OpenClaw's implicit single agent; list it so it stays in the roster.
        roster["main"] = {"workspace": MAIN_WORKSPACE}
    for key, entry in wanted.items():
        roster[key] = copy.deepcopy(entry) if inline else include(key)
    legacy_default = any(isinstance(v, dict) and v.get("default") is True for v in roster.values())
    if agents.get("ownership") != "explicit" and not legacy_default:
        # Explicit ownership ends the sole agent's claim on the default workspace; pin it, as OpenClaw's own migration does.
        previous = [key for key in roster if key not in wanted]
        if len(previous) == 1 and _pinnable(roster[previous[0]]):
            pinned = (agents.get("defaults") or {}).get("workspace") or MAIN_WORKSPACE
            roster[previous[0]] = {**roster[previous[0]], "workspace": pinned}
        agents["ownership"] = "explicit"
    defaults = agents.get("defaults") or {}
    system = defaults.get("systemAgent") or {}
    if "main" in roster and not system.get("agentId"):
        agents["defaults"] = {**defaults, "systemAgent": {**system, "agentId": "main"}}
    return config


def _pinnable(entry) -> bool:
    return isinstance(entry, dict) and "$include" not in entry and not entry.get("workspace")


def parse_texts(ctx, texts: dict[str, str]) -> tuple[dict, dict[str, str]]:
    """Parse JSON5 texts: strict JSON in Python, anything else with OpenClaw's json5 package."""
    parsed, rest = {}, {}
    for name, text in texts.items():
        try:
            parsed[name] = json.loads(text)
        except json.JSONDecodeError:
            rest[name] = text
    errors = {}
    if rest:
        more, errors = ctx.runner.parse_json5(rest)
        parsed.update(more)
    return parsed, errors


def resolve_tree(ctx, team: str, tree: Tree) -> contract.Resolved:
    parsed, errors = parse_texts(ctx, tree.texts)
    return contract.resolve(team, tree, parsed, errors)


def check_team(ctx, team: str, tree: Tree) -> contract.Resolved:
    """Raise TeamError unless tree follows the contract and OpenClaw accepts its agents."""
    resolved = resolve_tree(ctx, team, tree)
    _, config, _ = _read_state(ctx, [])
    _validate(ctx, plan_roster(config, team, resolved.entries, inline=True))
    return resolved


def apply_team(ctx, team: str, tree: Tree) -> list[str]:
    """Write team's agents and install its plugins. Returns problems for the user (non-fatal).

    Raises TeamError before anything is written when the team is invalid.
    """
    resolved = resolve_tree(ctx, team, tree)
    for warning in resolved.warnings:
        ctx.say(f"  warning: {warning}")
    files = {} if INLINE else {ctx.paths.agents_d / f"{key}.json5": _dump(e) for key, e in sorted(resolved.entries.items())}
    text, config, current = _read_state(ctx, list(files))
    new = plan_roster(config, team, resolved.entries)
    before = owned(team, config.get("agents", {}).get("entries", {}))
    stale = sorted(before - resolved.entries.keys())
    ops = [(path, body) for path, body in files.items() if current.get(path) != body]
    if new != config:
        ops.append((ctx.paths.openclaw_json, _dump(new)))
    if not INLINE:
        ops += [(path, None) for key in stale for path in _agent_files(ctx, key)]
    problems = []
    if ops:
        _validate(ctx, plan_roster(config, team, resolved.entries, inline=True))
        ctx.runner.write_files(ops, expect={ctx.paths.openclaw_json: _sha(text)})
        added = sorted(resolved.entries.keys() - before)
        note = "".join(f" ({word} {', '.join(ids)})" for word, ids in (("added", added), ("removed", stale)) if ids)
        ctx.say(f"  agents: {', '.join(sorted(resolved.entries))}{note}")
        result = ctx.runner.validate(_dump(new))
        if not result.get("valid"):
            problems.append("openclaw.json does not validate after applying: " + "; ".join(_issues(result)))
    problems += install_plugins(ctx, resolved.plugins)
    return problems


def remove_team(ctx, team: str) -> list[str]:
    """Remove team's agents from openclaw.json and agents.d. Returns the removed ids."""
    text, config, _ = _read_state(ctx, [], required=False)
    if text is None:
        return []
    removed = sorted(owned(team, config.get("agents", {}).get("entries", {})))
    if not removed:
        return []
    ops = [(ctx.paths.openclaw_json, _dump(plan_roster(config, team, None)))]
    ops += [(path, None) for key in removed for path in _agent_files(ctx, key)]
    ctx.runner.write_files(ops, expect={ctx.paths.openclaw_json: _sha(text)})
    return removed


def installed_plugins(ctx) -> list[dict]:
    result = ctx.runner.openclaw("plugins", "list", "--json")
    if result.returncode:
        raise TeamError(f"`openclaw plugins list` failed: {result.stderr.strip()}")
    return [p for p in json_out(result.stdout).get("plugins", []) if isinstance(p, dict)]


def missing_plugins(specs: list[str], installed: list[dict]) -> list[str]:
    have = {name for p in installed for name in (p.get("id"), p.get("packageName")) if name}
    return [spec for spec in specs if not {spec, _bare(spec)} & have]


def unlisted_plugins(installed: list[dict], specs: list[str]) -> list[str]:
    """Installed (origin global) plugins that no team lists."""
    wanted = {spec for s in specs for spec in (s, _bare(s))}
    return sorted(
        p.get("id") or p.get("packageName") for p in installed
        if p.get("origin") == "global" and not {p.get("id"), p.get("packageName")} & wanted
    )


def install_plugins(ctx, specs: list[str]) -> list[str]:
    """Install missing plugins that need no confirmation; report the rest. Never raises."""
    if not specs:
        return []
    try:
        missing = missing_plugins(specs, installed_plugins(ctx))
    except TeamError as exc:
        return [str(exc)]
    problems = []
    for spec in missing:
        result = ctx.runner.openclaw("plugins", "install", spec)
        if result.returncode == 0:
            ctx.say(f"  installed plugin {spec}")
            continue
        detail = (result.stderr or result.stdout).strip().splitlines()
        reason = detail[-1] if detail else f"exit {result.returncode}"
        problems.append(f"plugin {spec} was not installed ({reason}); review it and run: rune openclaw::cli plugins install {spec}")
    return problems


def _read_state(ctx, extra: list, *, required: bool = True) -> tuple[str | None, dict, dict]:
    found = ctx.runner.read_files([ctx.paths.openclaw_json, *extra])
    text = found.pop(ctx.paths.openclaw_json)
    if text is None:
        if required:
            raise TeamError(f"{ctx.paths.openclaw_json} is missing; install OpenClaw first: python3 init.py openclaw")
        return None, {}, found
    try:
        config = json.loads(text)
    except json.JSONDecodeError:
        parsed, errors = ctx.runner.parse_json5({"openclaw.json": text})
        if errors:
            raise TeamError(f"openclaw.json: {errors['openclaw.json']}")
        config = parsed["openclaw.json"]
    return text, config, found


def _validate(ctx, config: dict) -> None:
    result = ctx.runner.validate(_dump(config))
    if not result.get("valid"):
        raise TeamError("OpenClaw rejects the team's settings:\n  - " + "\n  - ".join(_issues(result)))


def _issues(result: dict) -> list[str]:
    issues = [f"{i.get('path', '<root>')}: {i.get('message', '')}" for i in result.get("issues") or [] if isinstance(i, dict)]
    return issues or [str((result.get("error") or {}).get("message") or "config validate failed")]


def _agent_files(ctx, agent_id: str) -> list:
    """An agent's include file and the backups OpenClaw keeps when it writes through to it."""
    path = ctx.paths.agents_d / f"{agent_id}.json5"
    return [path, path.with_name(path.name + ".bak"), *(path.with_name(f"{path.name}.bak.{n}") for n in range(1, 5))]


def _bare(spec: str) -> str:
    """Package name without a version: name@1.2.3 -> name, @scope/name@1 -> @scope/name."""
    at = spec.rfind("@")
    return spec[:at] if at > 0 else spec


def _dump(value: dict) -> str:
    return json.dumps(value, indent=2) + "\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
