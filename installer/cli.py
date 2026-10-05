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
LOGIN_FIX = (
    "set TS_AUTHKEY in {env} (or open the login URL from `docker compose logs {service}`, run in {path}), "
    "then rerun init.py."
)
LOGIN_HINT = "If the {service} container is unhealthy, its node is not logged in: " + LOGIN_FIX
NOT_LOGGED_IN = "the {service} node is not logged in: " + LOGIN_FIX
HTTPS_OFF = (
    "HTTPS certificates are off in your tailnet, so Serve is not applied and the URL does not load. "
    "Enable them at https://login.tailscale.com/admin/dns (HTTPS Certificates > Enable HTTPS); "
    "the node picks it up within a minute, no restart needed."
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
    failed, actions = [], []
    for component in selected:
        try:
            actions += [f"{component.name}: {problem}" for problem in install(component, context, prompt=prompt)]
        except (CommandError, OSError) as exc:
            failed.append(f"{component.name}: {exc}")
    if actions:
        # Repeated at the end so it is not lost in the docker output above.
        print("\nAction needed:")
        for action in actions:
            print(f"  - {action}")
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


def install(component: Component, context: dict[str, str], *, prompt: Callable[[str], str] | None) -> list[str]:
    """Install or update one component. Every step is idempotent.

    prompt asks for missing secrets; None means non-interactive (--yes).
    Returns problems the user has to act on (for example tailnet access).
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
        run = step.run
        if step.interactive and prompt is None:
            if not step.headless:
                print(f"  skipped interactive step (--yes), run later: docker compose {step.run}")
                continue
            run = step.headless
        if step.note:
            print(f"  note: {step.note}")
        compose(path, *shlex.split(components.expand(run, context)))
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

    if not component.tailscale_service:
        return []
    url, problem = tailnet_check(component)
    print(f"  tailnet: {url or problem}")
    return [problem] if problem else []


def tailnet_check(component: Component) -> tuple[str, str]:
    """(https URL, "") for a ready node, or ("", what blocks it)."""
    service = component.tailscale_service
    result = compose(component.path, "exec", "-T", service, "tailscale", "status", "--json", capture=True, check=False)
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError:
        status = None
    if not isinstance(status, dict) or status.get("BackendState") != "Running":
        return "", NOT_LOGGED_IN.format(service=service, env=component.env_path, path=component.path)
    domains = status.get("CertDomains") or []
    if not domains:
        return "", HTTPS_OFF
    return f"https://{domains[0]}/", ""


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
