"""teams.py commands: add, sync, remove, list and apply."""

from datetime import datetime
from pathlib import Path

from teamlib import TeamError, access, contract, gateway, registry
from teamlib.gitsync import Sync
from teamlib.registry import Team
from teamlib.tree import from_rev


def add(ctx, name: str, source: str, branch: str = "main") -> list[str]:
    """team-add: clone, check, apply and record a team. Returns non-fatal problems."""
    registry.check_new_name(name, ctx.registry.teams)
    registry.check_branch(branch)
    url = source
    if access.is_local(source):
        folder = Path(source).expanduser()
        folder = (folder if folder.is_absolute() else Path.cwd() / folder).resolve()
        if not folder.is_dir():
            raise TeamError(f"{source}: no such folder, and not an SSH git URL")
        url = folder.relative_to(ctx.repo).as_posix() if folder.is_relative_to(ctx.repo) else str(folder)
    team = Team(name, url, branch)
    problems = clone_team(ctx, team)
    ctx.registry.teams[name] = team
    registry.save(ctx.paths.registry, ctx.registry)
    ctx.say(f"team {name} added; sync it with: rune openclaw::team-sync {name}")
    return problems


def clone_team(ctx, team: Team) -> list[str]:
    """Steps 2 to 5 of team-add. Leaves no clone behind when it fails."""
    clone, git_dir = ctx.paths.clone(team.name), ctx.paths.git_dir(team.name)
    for path in (clone, git_dir):
        if path.exists():
            raise TeamError(f"{path} already exists; move it away first")
    folder = local_folder(ctx, team.url)
    bare = ctx.paths.remote(team.name)
    create_remote = folder is not None and not bare.exists()
    if folder is None:
        access.ensure_access(ctx, team)
    elif create_remote and not folder.is_dir():
        raise TeamError(f"{team.url}: folder not found, and data/team-remotes/{bare.name} does not exist")
    remote = ctx.runner.git_path(bare) if folder is not None else team.url
    try:
        if create_remote:
            _import(ctx, team, folder, remote)
        else:
            ctx.runner.git(
                ctx.paths.teams, "clone", "-q", "--separate-git-dir", ctx.runner.git_path(git_dir),
                "--branch", team.branch, remote, team.name, team=team.name,
            )
            ctx.runner.remove_tree(clone / ".git")
            _configure(ctx, team)
        return gateway.apply_team(ctx, team.name, from_rev(_sync(ctx, team).git, "HEAD"))
    except BaseException:
        ctx.runner.remove_tree(clone)
        ctx.runner.remove_tree(git_dir)
        if create_remote:
            ctx.runner.remove_tree(bare)
        raise


def sync(ctx, name: str | None = None) -> bool:
    """team-sync for one team or all. False when any team failed or reported problems."""
    ok = True
    for team in _selected(ctx, name):
        ctx.say(f"==> team {team.name}")
        try:
            problems = sync_team(ctx, team)
        except TeamError as exc:
            ctx.say(f"  error: {exc}")
            ok = False
            continue
        for problem in problems:
            ctx.say(f"  problem: {problem}")
        ok = ok and not problems
    return ok


def sync_team(ctx, team: Team) -> list[str]:
    for path in (ctx.paths.clone(team.name), ctx.paths.git_dir(team.name)):
        if not path.is_dir():
            raise TeamError(f"{path} is missing; move data/teams/{team.name} away and clone it again with: python3 init.py openclaw")
    state = _sync(ctx, team)
    when = datetime.now()
    stamp = when.strftime("%Y%m%d-%H%M%S")
    state.stage_workspaces(ctx.secrets)
    state.commit(when)
    state.rescue_settings(stamp, ctx.secrets)
    state.integrate(lambda tree: gateway.check_team(ctx, team.name, tree), stamp)
    return gateway.apply_team(ctx, team.name, from_rev(state.git, "HEAD"))


def remove(ctx, name: str, force: bool = False) -> None:
    """team-remove: refuse while data exists only on this VM (unless force), then drop agents, clone and registry entry."""
    team = _selected(ctx, name)[0]
    clone = ctx.paths.clone(name)
    if clone.is_dir() and ctx.paths.git_dir(name).is_dir() and not force:
        pending = _sync(ctx, team).unpushed()
        if pending:
            shown = ", ".join(pending[:10]) + (", ..." if len(pending) > 10 else "")
            raise TeamError(
                f"team {name} has data that exists only on this VM: {shown}. "
                f"Sync it with rune openclaw::team-sync {name}, look at it with rune openclaw::team-git {name} <git command>, "
                f"or remove anyway, deleting it: python3 stacks/openclaw/teams.py remove {name} --force"
            )
    removed = gateway.remove_team(ctx, name)
    if clone.is_dir():
        ctx.runner.remove_tree(clone)
    if ctx.paths.git_dir(name).is_dir():
        ctx.runner.remove_tree(ctx.paths.git_dir(name))
    del ctx.registry.teams[name]
    registry.save(ctx.paths.registry, ctx.registry)
    ctx.say(f"team {name} removed" + (f": {', '.join(removed)}" if removed else ""))
    kept = ["sessions in data/config/agents/<id>/"]
    kept += [str(p.relative_to(ctx.paths.stack)) for p in (ctx.paths.keys / name, ctx.paths.remote(name)) if p.exists()]
    ctx.say(f"  kept: {', '.join(kept)}")


def list_teams(ctx) -> None:
    if not ctx.registry.teams:
        ctx.say("no teams yet; add one with: rune openclaw::team-add <team> <SSH git URL or folder>")
        return
    specs = []
    for team in ctx.registry.teams.values():
        ctx.say(f"{team.name}  {team.url} ({team.branch})")
        if not ctx.paths.clone(team.name).is_dir():
            ctx.say("  not cloned yet: python3 init.py openclaw")
            continue
        state = _sync(ctx, team)
        tree = from_rev(state.git, "HEAD")
        ahead, behind = state.ahead_behind()
        changed = len(state.status("workspaces"))
        ctx.say(f"  agents: {', '.join(contract.agent_id(team.name, f) for f in sorted(tree.workspaces))}")
        ctx.say(f"  git: {ahead} to push, {behind} to pull (as of the last fetch), {changed} changed file(s)")
        if "plugins.json" in tree.texts:
            parsed, _ = gateway.parse_texts(ctx, {"plugins.json": tree.texts["plugins.json"]})
            specs += contract.plugin_specs(parsed.get("plugins.json")) or []
    try:
        unlisted = gateway.unlisted_plugins(gateway.installed_plugins(ctx), specs)
    except TeamError as exc:
        ctx.say(f"plugins: {exc}")
        return
    if unlisted:
        ctx.say(f"plugins no team lists: {', '.join(unlisted)}")


def apply(ctx) -> None:
    """Installer step: clone missing teams, refresh the others. Problems become `action:` lines."""
    for team in ctx.registry.teams.values():
        ctx.say(f"==> team {team.name}")
        try:
            if ctx.paths.clone(team.name).is_dir():
                problems = gateway.apply_team(ctx, team.name, from_rev(_sync(ctx, team).git, "HEAD"))
            else:
                problems = clone_team(ctx, team)
        except TeamError as exc:
            problems = [str(exc)]
        for problem in problems:
            ctx.say(f"action: team {team.name}: {' '.join(problem.split())}")


def local_folder(ctx, url: str) -> Path | None:
    if not access.is_local(url):
        return None
    path = Path(url)
    return path if path.is_absolute() else ctx.repo / path


def _import(ctx, team: Team, folder: Path, remote: str) -> None:
    """Turn a plain folder into a bare repo in data/team-remotes and a clone of it."""
    ctx.runner.git(ctx.paths.remotes, "init", "-q", "--bare", "-b", team.branch, f"{team.name}.git")
    git_dir = ctx.paths.git_dir(team.name)
    ctx.runner.git(ctx.paths.teams, "init", "-q", "-b", team.branch, "--separate-git-dir", ctx.runner.git_path(git_dir), team.name)
    ctx.runner.remove_tree(ctx.paths.clone(team.name) / ".git")
    _configure(ctx, team)
    ctx.runner.copy_in(folder, ctx.paths.clone(team.name))
    git = _sync(ctx, team).git
    git("add", "-A")
    git("commit", "-q", "--no-verify", "-m", f"import {team.url}")
    git("remote", "add", "origin", remote)
    git("push", "-q", "-u", "origin", f"HEAD:refs/heads/{team.branch}")


def _configure(ctx, team: Team) -> None:
    git = _sync(ctx, team).git
    for key, value in (
        ("pull.rebase", "true"),
        ("rebase.autoStash", "true"),
        ("user.name", ctx.registry.git_name),
        ("user.email", ctx.registry.git_email),
    ):
        git("config", key, value)


def _sync(ctx, team: Team) -> Sync:
    return Sync(ctx.runner, ctx.paths.clone(team.name), ctx.paths.git_dir(team.name), team.name, team.branch, ctx.registry.vm, ctx.say)


def _selected(ctx, name: str | None) -> list[Team]:
    if name is None:
        return list(ctx.registry.teams.values())
    if name not in ctx.registry.teams:
        known = ", ".join(ctx.registry.teams) or "none"
        raise TeamError(f"unknown team {name}; teams in teams.toml: {known}")
    return [ctx.registry.teams[name]]
