"""Discover components and load their component.toml manifests.

A component is a folder (dockge/ or stacks/<name>/) holding compose.yaml,
an optional .env.example and a component.toml describing how to install it.
"""

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from installer import envfile

NAME_RE = re.compile(r"^[a-z0-9_-]+$")
TOP_KEYS = {"description", "order", "dirs", "env", "setup", "tailscale"}
ENV_KEYS = {"set", "generate", "ask"}
STEP_KEYS = {"run", "once", "interactive", "headless"}
TAILSCALE_KEYS = {"service"}
INFRA_DIRS = ("tailscale", "dockge")


class ManifestError(ValueError):
    """A component.toml is missing, malformed or inconsistent with its folder."""


class SelectionError(ValueError):
    """The user asked for a component that does not exist."""


@dataclass(frozen=True)
class Step:
    """A `docker compose` invocation run during install.

    once: marker path; the step is skipped while it exists and the
    installer creates it after the step succeeds.
    headless: run string used instead of an interactive step under --yes.
    """

    run: str
    once: str | None = None
    interactive: bool = False
    headless: str | None = None


@dataclass(frozen=True)
class Component:
    name: str
    path: Path
    description: str
    order: int = 100
    dirs: tuple[str, ...] = ()
    env_set: Mapping[str, str] = field(default_factory=dict)
    env_generate: tuple[str, ...] = ()
    env_ask: tuple[str, ...] = ()
    setup: tuple[Step, ...] = ()
    tailscale_service: str | None = None

    @property
    def env_example(self) -> Path:
        return self.path / ".env.example"

    @property
    def env_path(self) -> Path:
        return self.path / ".env"


def discover(repo: Path) -> list[Component]:
    """Load the infra components and every stacks/<name>/ that has a component.toml, in install order."""
    candidates = [*(repo / name for name in INFRA_DIRS), *sorted((repo / "stacks").glob("*/"))]
    found = [load(path) for path in candidates if (path / "component.toml").is_file()]
    names = [c.name for c in found]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ManifestError(f"duplicate component names: {', '.join(duplicates)}")
    return sorted(found, key=lambda c: (c.order, c.name))


def select(available: list[Component], names: Iterable[str]) -> list[Component]:
    """Return the named components in install order, or all of them when names is empty."""
    wanted = set(names)
    if not wanted:
        return list(available)
    unknown = wanted - {c.name for c in available}
    if unknown:
        raise SelectionError(
            f"unknown component(s): {', '.join(sorted(unknown))}. "
            f"Available: {', '.join(c.name for c in available)}"
        )
    return [c for c in available if c.name in wanted]


def expand(text: str, context: Mapping[str, str]) -> str:
    """Replace {key} placeholders for known context keys, leaving other braces (JSON) alone."""
    for key, value in context.items():
        text = text.replace("{" + key + "}", value)
    return text


def load(path: Path) -> Component:
    """Parse and validate path/component.toml."""
    name = path.name
    manifest = path / "component.toml"
    if not NAME_RE.match(name):
        raise ManifestError(f"{path}: folder name must match {NAME_RE.pattern} (it is the compose project name)")
    try:
        data = tomllib.loads(manifest.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{manifest}: {exc}") from exc

    where = str(manifest)
    _check_keys(data, TOP_KEYS, where)
    if not isinstance(data.get("description"), str):
        raise ManifestError(f"{where}: 'description' (string) is required")
    if not (path / "compose.yaml").is_file():
        raise ManifestError(f"{path}: compose.yaml is missing")

    order = data.get("order", 100)
    if not isinstance(order, int) or isinstance(order, bool):
        raise ManifestError(f"{where}: 'order' must be an integer")

    env = data.get("env", {})
    _check_keys(env, ENV_KEYS, f"{where} [env]")
    env_set = env.get("set", {})
    if not isinstance(env_set, dict) or not all(isinstance(v, str) for v in env_set.values()):
        raise ManifestError(f"{where}: 'env.set' must map keys to strings")
    generate = _str_list(env, "generate", f"{where} [env]")
    ask = _str_list(env, "ask", f"{where} [env]")
    _check_env_keys(path, [*env_set, *generate, *ask], where)

    dirs = _str_list(data, "dirs", where)
    for rel in dirs:
        _check_relative(rel, "dirs", where)

    tailscale = data.get("tailscale")
    service = None
    if tailscale is not None:
        _check_keys(tailscale, TAILSCALE_KEYS, f"{where} [tailscale]")
        service = tailscale.get("service")
        if not isinstance(service, str) or not service:
            raise ManifestError(f"{where}: 'tailscale.service' (compose service name) is required")

    return Component(
        name=name,
        path=path,
        description=data["description"],
        order=order,
        dirs=tuple(dirs),
        env_set=dict(env_set),
        env_generate=tuple(generate),
        env_ask=tuple(ask),
        setup=tuple(_step(raw, where) for raw in data.get("setup", [])),
        tailscale_service=service,
    )


def _step(raw: dict, where: str) -> Step:
    _check_keys(raw, STEP_KEYS, f"{where} [[setup]]")
    run, once, interactive = raw.get("run"), raw.get("once"), raw.get("interactive", False)
    if not isinstance(run, str) or not run.strip():
        raise ManifestError(f"{where}: every [[setup]] needs a 'run' string")
    if once is not None:
        if not isinstance(once, str):
            raise ManifestError(f"{where}: 'setup.once' must be a path string")
        _check_relative(once, "setup.once", where)
    if not isinstance(interactive, bool):
        raise ManifestError(f"{where}: 'setup.interactive' must be true or false")
    headless = raw.get("headless")
    if headless is not None:
        if not isinstance(headless, str) or not headless.strip():
            raise ManifestError(f"{where}: 'setup.headless' must be a run string")
        if not interactive:
            raise ManifestError(f"{where}: 'setup.headless' only applies to steps with interactive = true")
    return Step(run=run, once=once, interactive=interactive, headless=headless)


def _check_keys(table, allowed: set[str], where: str) -> None:
    if not isinstance(table, dict):
        raise ManifestError(f"{where}: expected a table")
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ManifestError(f"{where}: unknown key(s) {', '.join(unknown)}")


def _str_list(table: dict, key: str, where: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ManifestError(f"{where}: '{key}' must be a list of strings")
    return value


def _check_relative(rel: str, key: str, where: str) -> None:
    pure = PurePosixPath(rel)
    if pure.is_absolute() or ".." in pure.parts:
        raise ManifestError(f"{where}: '{key}' entries must stay inside the component folder: {rel}")


def _check_env_keys(path: Path, keys: list[str], where: str) -> None:
    if not keys:
        return
    example = path / ".env.example"
    if not example.is_file():
        raise ManifestError(f"{where}: [env] is set but .env.example is missing")
    missing = sorted(set(keys) - set(envfile.parse(example.read_text())))
    if missing:
        raise ManifestError(f"{where}: [env] keys not in .env.example: {', '.join(missing)}")
