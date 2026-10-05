"""Check and install host prerequisites on Ubuntu/Debian: Docker, Tailscale and rune."""

import getpass
import json
import os
import shutil
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from installer import envfile
from installer.shell import run, sudo

TAILSCALE_INSTALL_URL = "https://tailscale.com/install.sh"
RUNE_INSTALL_URL = "https://raw.githubusercontent.com/rune-task-runner/rune/main/scripts/install.sh"
RELOGIN_HINT = (
    "Docker is installed but this session cannot reach it. If your user was just added to the "
    "docker group, log out and back in (or run `newgrp docker`), then rerun init.py. "
    "Otherwise check `sudo systemctl status docker`."
)


class HostError(RuntimeError):
    """A required prerequisite is missing and could not be installed."""


def os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    return {key: value.strip("\"'") for key, value in envfile.parse(text).items()}


def is_supported(osr: dict[str, str]) -> bool:
    return bool(_distro_ids(osr) & {"ubuntu", "debian"})


def docker_install_script(osr: dict[str, str]) -> str:
    """Shell script for Docker's official apt repository install."""
    ubuntu = "ubuntu" in _distro_ids(osr)
    distro = "ubuntu" if ubuntu else "debian"
    codename = (osr.get("UBUNTU_CODENAME") if ubuntu else None) or osr.get("VERSION_CODENAME", "")
    return f"""set -eu
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/{distro}/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/{distro} {codename} stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
"""


def docker_state() -> str:
    """One of: missing, no-compose, no-access, ok."""
    if not shutil.which("docker"):
        return "missing"
    if run(["docker", "compose", "version"], capture=True, check=False).returncode != 0:
        return "no-compose"
    if run(["docker", "info"], capture=True, check=False).returncode != 0:
        return "no-access"
    return "ok"


def tailscale_status() -> dict | None:
    """Parsed `tailscale status --json`, {} if it cannot be read, None if not installed."""
    if not shutil.which("tailscale"):
        return None
    result = run(["tailscale", "status", "--json"], capture=True, check=False)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}


def hostname_from_status(status: dict | None) -> str:
    if not status or status.get("BackendState") != "Running":
        return ""
    return (status.get("Self") or {}).get("DNSName", "").rstrip(".")


def tailnet_hostname() -> str:
    """MagicDNS name of this machine, or "" when Tailscale is not up."""
    return hostname_from_status(tailscale_status())


def install_docker(osr: dict[str, str]) -> None:
    run(sudo(["sh", "-c", docker_install_script(osr)]), label="install Docker Engine from download.docker.com")


def add_docker_group() -> None:
    run(sudo(["usermod", "-aG", "docker", getpass.getuser()]))


def install_tailscale() -> None:
    _run_remote_script(TAILSCALE_INSTALL_URL, [])


def install_rune() -> None:
    _run_remote_script(RUNE_INSTALL_URL, sudo(["env", "INSTALL_DIR=/usr/local/bin"]))


def ensure(*, assume_yes: bool, confirm: Callable[[str], bool]) -> None:
    """Make sure Docker (required), Tailscale and rune (optional) are ready.

    Only the Docker install is distro specific; the Tailscale and rune
    installers handle other Linux distributions themselves.
    """
    def ask(question: str) -> bool:
        return assume_yes or confirm(question)

    state = docker_state()
    if state == "missing":
        osr = os_release()
        if not is_supported(osr):
            raise HostError(
                f"Docker auto-install supports Ubuntu/Debian only, found {osr.get('PRETTY_NAME', 'unknown OS')}. "
                "Install Docker with the compose plugin yourself, then rerun init.py."
            )
        if not ask("Docker is not installed. Install Docker Engine + compose plugin?"):
            raise HostError("Docker is required. Install it or rerun once it is available.")
        install_docker(osr)
        add_docker_group()
        state = docker_state()
    if state == "no-compose":
        raise HostError("the Docker compose plugin is missing: https://docs.docker.com/compose/install/linux/")
    if state == "no-access":
        raise HostError(RELOGIN_HINT)
    _report("docker", "ok")

    status = tailscale_status()
    if status is None and ask("Tailscale is not installed. Install it?"):
        install_tailscale()
        status = tailscale_status()
    if status is None:
        _report("tailscale", "skipped; UIs stay reachable through an SSH tunnel only")
    elif hostname_from_status(status):
        _report("tailscale", f"ok ({hostname_from_status(status)})")
    else:
        print("  Tailscale is installed but not logged in. Open the URL it prints to authorize this VM.")
        run(sudo(["tailscale", "up"]))

    if shutil.which("rune"):
        _report("rune", "ok")
    elif ask("rune (task runner) is not installed. Install it to /usr/local/bin?"):
        install_rune()
    else:
        _report("rune", "skipped; use python3 init.py and docker compose directly")


def _distro_ids(osr: dict[str, str]) -> set[str]:
    return {osr.get("ID", ""), *osr.get("ID_LIKE", "").split()} - {""}


def _run_remote_script(url: str, prefix: list[str]) -> None:
    with urllib.request.urlopen(url, timeout=60) as response:
        body = response.read()
    with tempfile.NamedTemporaryFile("wb", suffix=".sh", delete=False) as handle:
        handle.write(body)
    try:
        run([*prefix, "sh", handle.name], label=f"sh <(curl -fsSL {url})")
    finally:
        os.unlink(handle.name)


def _report(name: str, message: str) -> None:
    print(f"[host] {name:<10} {message}")
