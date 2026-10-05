"""Subprocess helpers: echo each command, run it, and fail with a readable error."""

import os
import shlex
import subprocess
from pathlib import Path


class CommandError(RuntimeError):
    """A command was not found or exited non-zero."""


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
    label: str | None = None,
) -> subprocess.CompletedProcess:
    """Run cmd, streaming output unless capture is set. label replaces the echoed command."""
    if not capture:
        print(f"  $ {label or shlex.join(cmd)}", flush=True)
    try:
        result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=capture, check=False)
    except FileNotFoundError as exc:
        raise CommandError(f"{cmd[0]}: command not found") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or "").strip() if capture else ""
        message = f"`{label or shlex.join(cmd)}` exited with {result.returncode}"
        raise CommandError(f"{message}: {detail}" if detail else message)
    return result


def sudo(cmd: list[str]) -> list[str]:
    """Prefix cmd with sudo unless already running as root."""
    return list(cmd) if os.geteuid() == 0 else ["sudo", *cmd]


def compose(directory: Path, *args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    """Run `docker compose` inside a component folder, so the project name is the folder name."""
    return run(["docker", "compose", *args], cwd=directory, capture=capture, check=check)
