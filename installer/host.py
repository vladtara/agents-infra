"""Check and install host prerequisites on Ubuntu/Debian: Docker and rune."""

import getpass
import os
import shutil
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from installer import envfile
from installer.shell import run, sudo

RUNE_INSTALL_URL = "https://raw.githubusercontent.com/rune-task-runner/rune/main/scripts/install.sh"
RELOGIN_HINT = (
    "Docker is installed but this session cannot reach it. If your user was just added to the "
    "docker group, log out and back in (or run `newgrp docker`), then rerun init.py. "
    "Otherwise check `sudo systemctl status docker`."
)
TAILSCALED_CONFLICT = (
    "tailscaled is running on the host. It conflicts with the tailscale component, which also "
    "uses the tailscale0 interface. Disable it with `sudo systemctl disable --now tailscaled`, "
    "or install without the tailscale component. If you are connected over that tailnet, run the "
    "command from public SSH or the cloud console: it ends the session."
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


def host_tailscaled_active() -> bool:
    """True when the host's own tailscaled systemd unit runs.

    Uses systemd state rather than the process list, which would also show
    the tailscale component's containerized tailscaled.
    """
    if not shutil.which("systemctl"):
        return False
    return run(["systemctl", "is-active", "--quiet", "tailscaled"], capture=True, check=False).returncode == 0


def install_docker(osr: dict[str, str]) -> None:
    run(sudo(["sh", "-c", docker_install_script(osr)]), label="install Docker Engine from download.docker.com")


def add_docker_group() -> None:
    run(sudo(["usermod", "-aG", "docker", getpass.getuser()]))


def install_rune() -> None:
    _run_remote_script(RUNE_INSTALL_URL, sudo(["env", "INSTALL_DIR=/usr/local/bin"]))


def ensure(*, assume_yes: bool, confirm: Callable[[str], bool], tailscale_component: bool = False) -> None:
    """Make sure Docker (required) and rune (optional) are ready.

    tailscale_component: the tailscale component is being installed, so a
    host tailscaled would fight it for tailscale0.
    """
    def ask(question: str) -> bool:
        return assume_yes or confirm(question)

    if tailscale_component and host_tailscaled_active():
        raise HostError(TAILSCALED_CONFLICT)

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
