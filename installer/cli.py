"""init.py command line: install host prerequisites, then the selected components."""

import argparse
import getpass
import json
import os
import shlex
import sys
from collections.abc import Callable
from pathlib import Path

from installer import components, envfile, host
from installer.components import Component, ManifestError, SelectionError
from installer.shell import CommandError, compose

REPO = Path(__file__).resolve().parent.parent
WAIT_TIMEOUT = "180"
NO_HTTPS_HINT = (
    "no HTTPS name yet. Check that the node is logged in (docker compose logs {service}) and that "
    "MagicDNS and HTTPS certificates are enabled in the Tailscale admin console."
)
LOGIN_HINT = (
    "If the {service} container is unhealthy, its node is not logged in: set TS_AUTHKEY in {env} "
    "(or open the login URL from `docker compose logs {service}`, run in {path}), then rerun init.py."
)


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
            host.ensure(
                assume_yes=args.yes,
                confirm=confirm,
                tailscale_component=any(c.name == "tailscale" for c in selected),
            )
    except (ManifestError, SelectionError, host.HostError, CommandError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    context = {
        "repo_dir": str(repo),
        "stacks_dir": str(repo / "stacks"),
        "uid": str(os.getuid()),
        "gid": str(os.getgid()),
    }
    prompt = None if args.yes else remembering(ask_secret)
    failed = []
    for component in selected:
        try:
            install(component, context, prompt=prompt)
        except (CommandError, OSError) as exc:
            failed.append(f"{component.name}: {exc}")
    for message in failed:
        print(f"error: {message}", file=sys.stderr)
    return 1 if failed else 0


def remembering(ask: Callable[[str], str]) -> Callable[[str], str]:
    """Wrap ask so each key is asked once per run, even when several components need it."""
    answers: dict[str, str] = {}

    def prompt(key: str) -> str:
        if key not in answers:
            answers[key] = ask(key)
        return answers[key]

    return prompt


def install(component: Component, context: dict[str, str], *, prompt: Callable[[str], str] | None) -> None:
    """Install or update one component. Every step is idempotent.

    prompt asks for missing secrets; None means non-interactive (--yes).
    """
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
            prompt=prompt,
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
        if step.interactive and prompt is None:
            print(f"  skipped interactive step (--yes), run later: docker compose {step.run}")
            continue
        compose(path, *shlex.split(components.expand(step.run, context)))
        ran_setup = True
        if marker:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
    try:
        compose(path, "up", "-d", "--wait", "--wait-timeout", WAIT_TIMEOUT)
    except CommandError as exc:
        if not component.tailscale_service:
            raise
        hint = LOGIN_HINT.format(service=component.tailscale_service, env=component.env_path, path=path)
        raise CommandError(f"{exc}. {hint}") from exc
    if was_running and ran_setup:
        # Setup steps may rewrite app config read at start. The sidecar stays up:
        # restarting it would cut the namespace out from under the app.
        services = compose(path, "config", "--services", capture=True).stdout.split()
        compose(path, "restart", *[s for s in services if s != component.tailscale_service])

    if component.tailscale_service:
        url = tailnet_url(component)
        print(f"  tailnet: {url or NO_HTTPS_HINT.format(service=component.tailscale_service)}")


def tailnet_url(component: Component) -> str:
    """https URL of the component's Tailscale node, or "" while it has no HTTPS name."""
    result = compose(
        component.path, "exec", "-T", component.tailscale_service, "tailscale", "status", "--json",
        capture=True, check=False,
    )
    try:
        domains = json.loads(result.stdout).get("CertDomains") or []
    except (json.JSONDecodeError, AttributeError):
        return ""
    return f"https://{domains[0]}/" if domains else ""


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
