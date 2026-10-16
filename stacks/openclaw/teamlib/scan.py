"""Keep secrets and large files out of sync commits."""

import re
import tomllib
from pathlib import Path

from installer import envfile

MAX_BYTES = 5 * 1024 * 1024
MIN_SECRET = 8
SECRET_KEY_RE = re.compile(r"_(KEY|TOKEN|SECRET|PASSWORD)$")
PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"sk-ant-[A-Za-z0-9_-]{20,}",
        r"sk-[A-Za-z0-9]{20,}",
        r"ghp_[A-Za-z0-9]{36}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"xox[abp]-[A-Za-z0-9-]{10,}",
        r"AKIA[0-9A-Z]{16}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    )
]


def load_secrets(stack: Path) -> list[str]:
    """Values from stack/.env that must never be committed.

    Keys named *_KEY, *_TOKEN, *_SECRET, *_PASSWORD or listed in [env] generate/ask
    of component.toml, when the value has at least 8 characters.
    """
    env_path = stack / ".env"
    if not env_path.is_file():
        return []
    listed = set()
    manifest = stack / "component.toml"
    if manifest.is_file():
        env_table = tomllib.loads(manifest.read_text()).get("env", {})
        listed = {*env_table.get("generate", []), *env_table.get("ask", [])}
    env = envfile.parse(env_path.read_text())
    return sorted({
        value for key, value in env.items()
        if (SECRET_KEY_RE.search(key) or key in listed) and len(value) >= MIN_SECRET
    })


def problem(path: Path, secrets: list[str]) -> str | None:
    """Why path must not be committed, or None. Deleted files and symlinks are fine."""
    if path.is_symlink() or not path.exists():
        return None
    try:
        if path.stat().st_size > MAX_BYTES:
            return "larger than 5 MB"
        data = path.read_bytes()
    except OSError as exc:
        return f"unreadable ({exc.strerror})"
    return content_problem(data, secrets)


def content_problem(data: bytes, secrets: list[str]) -> str | None:
    """Why this content must not be committed, or None."""
    if len(data) > MAX_BYTES:
        return "larger than 5 MB"
    text = data.decode("utf-8", errors="ignore")
    if any(secret in text for secret in secrets):
        return "contains a value from .env"
    if any(pattern.search(text) for pattern in PATTERNS):
        return "looks like it contains a key or token"
    return None
