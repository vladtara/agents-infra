"""Deploy-key access to team repos: key generation, access checks, adding the key with gh or glab."""

import re
import shutil
import subprocess
import time
from pathlib import Path

from teamlib import TeamError
from teamlib.registry import Team

SCP_RE = re.compile(r"^(?:[\w.-]+@)?(?P<host>[\w.-]+):(?P<path>[^/].*)$")
SSH_RE = re.compile(r"^ssh://(?:[\w.-]+@)?(?P<host>[\w.-]+)(?::\d+)?/(?P<path>.+)$")
RETRY_DELAYS = (2, 5, 10)


class NeedsKey(TeamError):
    """The repo refuses the deploy key and it could not be added automatically."""


def is_local(url: str) -> bool:
    return "://" not in url and not SCP_RE.match(url)


def repo_slug(url: str) -> tuple[str, str] | None:
    """(host, owner/repo) of an SSH URL, or None."""
    match = SCP_RE.match(url) or SSH_RE.match(url)
    if not match:
        return None
    return match["host"], match["path"].removesuffix(".git").strip("/")


def settings_url(host: str, path: str) -> str | None:
    if host == "github.com":
        return f"https://github.com/{path}/settings/keys/new"
    if "gitlab" in host:
        return f"https://{host}/{path}/-/settings/repository#js-deploy-keys-settings"
    return None


def ensure_access(ctx, team: Team, sleep=time.sleep) -> None:
    """Make sure team.url accepts this VM's deploy key, adding the key with gh or glab when possible."""
    slug = repo_slug(team.url)
    if slug is None:
        raise TeamError(f"{team.url}: use an SSH URL (git@host:owner/repo.git); deploy keys work over SSH only")
    pub = ctx.paths.keys / f"{team.name}.pub"
    if not pub.is_file():
        ctx.runner.keygen(team.name, f"openclaw-{ctx.registry.vm}-{team.name}")
    ok, error = _reachable(ctx, team)
    if ok:
        return
    host, path = slug
    tool = add_deploy_key(host, path, pub, ctx.registry.vm)
    if tool is None:
        url = settings_url(host, path)
        where = f" at {url}" if url else ""
        raise NeedsKey(
            f"{team.url} refuses access ({error}). Add this deploy key with write access{where}, "
            f"then run the command again: {pub.read_text().strip()}"
        )
    ctx.say(f"  added deploy key {pub.name} to {path} with {tool}")
    for delay in RETRY_DELAYS:
        sleep(delay)
        ok, error = _reachable(ctx, team)
        if ok:
            return
    raise TeamError(f"{team.url} still refuses the deploy key ({error})")


def add_deploy_key(host: str, path: str, pub: Path, title: str) -> str | None:
    """Add pub as a writable deploy key with gh (github.com) or glab (other hosts). None when neither is usable."""
    if host == "github.com":
        tool, status = "gh", ["gh", "auth", "status", "--hostname", host]
        add = ["gh", "repo", "deploy-key", "add", str(pub), "--repo", path, "--title", title, "--allow-write"]
    else:
        tool, status = "glab", ["glab", "auth", "status", "--hostname", host]
        add = ["glab", "deploy-key", "add", str(pub), "--repo", f"{host}/{path}", "--title", title, "--can-push"]
    if not shutil.which(tool) or subprocess.run(status, capture_output=True).returncode != 0:
        return None
    result = subprocess.run(add, capture_output=True, text=True)
    if result.returncode:
        raise TeamError(f"{tool} could not add the deploy key: {result.stderr.strip()}")
    return tool


def _reachable(ctx, team: Team) -> tuple[bool, str]:
    result = ctx.runner.git(ctx.paths.teams, "ls-remote", "--heads", team.url, team.branch, team=team.name, check=False)
    lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
    return result.returncode == 0, lines[-1] if lines else f"exit {result.returncode}"
