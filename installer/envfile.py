"""Render a component's .env from its .env.example without losing existing values."""

import os
import secrets
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path


def parse(text: str) -> dict[str, str]:
    """Return KEY=value pairs from dotenv text, ignoring comments and blank lines."""
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def render(
    example: str,
    existing: Mapping[str, str],
    *,
    set_values: Mapping[str, str],
    generate: Iterable[str],
    ask: Iterable[str],
    prompt: Callable[[str], str] | None,
) -> str:
    """Build .env text following the layout of .env.example.

    Precedence per key: set_values, then the existing .env value, then a
    generated secret, then the prompt answer, then the example default.
    Keys present only in the existing .env (for example added in Dockge)
    are appended so nothing is dropped.
    """
    generate, ask = set(generate), set(ask)
    lines, seen = [], set()
    for line in example.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            lines.append(line)
            continue
        key, default = (part.strip() for part in stripped.split("=", 1))
        seen.add(key)
        lines.append(f"{key}={_value(key, default, existing, set_values, generate, ask, prompt)}")

    extra = [f"{key}={value}" for key, value in existing.items() if key not in seen]
    if extra:
        lines += ["", "# Added outside .env.example", *extra]
    return "\n".join(lines) + "\n"


def _value(key, default, existing, set_values, generate, ask, prompt):
    if key in set_values:
        return set_values[key]
    if key in existing and not (key in generate and not existing[key]):
        return existing[key]
    if key in generate:
        return secrets.token_hex(32)
    if key in ask and prompt:
        return prompt(key) or default
    return default


def write(path: Path, text: str) -> None:
    """Write text to path readable by the owner only, since .env holds secrets."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(text)
    os.chmod(path, 0o600)
