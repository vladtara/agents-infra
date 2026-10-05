"""init.py command line: install host prerequisites, then the selected components."""

import argparse
import getpass
import os
import shlex
import sys
from pathlib import Path

from installer import components, envfile, host
from installer.components import Component, ManifestError, SelectionError
from installer.shell import CommandError, compose, run, sudo

REPO = Path(__file__).resolve().parent.parent


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="init.py",
        description="Install agents-infra host prerequisites and components. Safe to rerun.",
    )
    parser.add_argument("components", nargs="*", help="components to install (default: all)")
    parser.add_argument("--list", action="store_true", help="list components and their state, then exit")
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="install missing host tools without asking; skip secret prompts and interactive steps",
    )
    parser.add_argument("--skip-host", action="store_true", help="do not check or install host prerequisites")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, repo: Path = REPO) -> int:
    args = parse_args(argv)
    try:
        available = components.discover(repo)
        if args.list:
            print_list(available)
            return 0
        selected = components.select(available, args.components)
        if not args.skip_host:
            host.ensure(assume_yes=args.yes, confirm=confirm)
    except (ManifestError, SelectionError, host.HostError, CommandError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    tailnet = host.tailnet_hostname()
    context = {
        "repo_dir": str(repo),
        "stacks_dir": str(repo / "stacks"),
        "uid": str(os.getuid()),
        "gid": str(os.getgid()),
        # Placeholder keeps generated config valid until Tailscale is up; rerun init.py afterwards.
        "ts_hostname": tailnet or "localhost",
    }
    failed = []
    for component in selected:
        try:
            install(component, context, tailnet=tailnet, interactive=not args.yes)
        except (CommandError, OSError) as exc:
            failed.append(f"{component.name}: {exc}")
    for message in failed:
        print(f"error: {message}", file=sys.stderr)
    return 1 if failed else 0


def install(component: Component, context: dict[str, str], *, tailnet: str, interactive: bool) -> None:
    """Install or update one component. Every step is idempotent."""
    print(f"\n==> {component.name}: {component.description}")
    path = component.path

    if component.env_example.is_file():
        existing = envfile.parse(component.env_path.read_text()) if component.env_path.is_file() else {}
        text = envfile.render(
            component.env_example.read_text(),
            existing,
            set_values={key: components.expand(value, context) for key, value in component.env_set.items()},
            generate=component.env_generate,
            ask=component.env_ask,
            prompt=ask_secret if interactive else None,
        )
        envfile.write(component.env_path, text)
        print("  wrote .env")

    for rel in component.dirs:
        (path / rel).mkdir(parents=True, exist_ok=True)

    was_running = is_running(path)
    compose(path, "pull")
    ran_setup = False
    for step in component.setup:
        marker = path / step.once if step.once else None
        if marker and marker.exists():
            continue
        if step.interactive and not interactive:
            print(f"  skipped interactive step (--yes), run later: docker compose {step.run}")
            continue
        compose(path, *shlex.split(components.expand(step.run, context)))
        ran_setup = True
        if marker:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
    compose(path, "up", "-d")
    if was_running and ran_setup:
        # Setup steps may rewrite app config that a running service only reads at start.
        compose(path, "restart")

    if component.tailscale_target:
        print(f"  local:   {component.tailscale_target}/")
    if component.tailscale_port and tailnet:
        run(sudo(["tailscale", "serve", "--bg", f"--https={component.tailscale_port}", component.tailscale_target]))
        port = "" if component.tailscale_port == 443 else f":{component.tailscale_port}"
        print(f"  tailnet: https://{tailnet}{port}/")


def is_running(path: Path) -> bool:
    result = compose(path, "ps", "--status", "running", "--quiet", capture=True, check=False)
    return bool(result.stdout.strip())


def state(component: Component) -> str:
    if component.env_example.is_file() and not component.env_path.is_file():
        return "not installed"
    try:
        running = is_running(component.path)
    except CommandError:
        return "unknown (docker unavailable)"
    if running:
        return "running"
    return "installed" if component.env_path.is_file() else "stopped"


def print_list(available: list[Component]) -> None:
    rows = [(c.name, state(c), c.description) for c in available]
    width = max((len(name) for name, _, _ in rows), default=4)
    print(f"{'NAME':<{width}}  {'STATE':<14}  DESCRIPTION")
    for name, status, description in rows:
        print(f"{name:<{width}}  {status:<14}  {description}")


def confirm(question: str) -> bool:
    try:
        answer = input(f"{question} [Y/n] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("", "y", "yes")


def ask_secret(key: str) -> str:
    try:
        return getpass.getpass(f"  {key} (input hidden, blank to skip): ").strip()
    except EOFError:
        return ""
