# OpenClaw Operations and Containerized Tailscale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace host-level Tailscale with a containerized VM node (`tailscale/`) plus an OpenClaw sidecar, and give OpenClaw a documented operating model (data, memory, plugins, shell, config, backups).

**Architecture:** A new top-level `tailscale/` component runs `tailscale/tailscale` with host networking in kernel mode, making the VM a tailnet node (SSH) and serving Dockge over HTTPS. `stacks/openclaw/` gains a `tailscale` service that owns the network namespace; the gateway binds loopback inside it and is published only through that sidecar's Serve. The Python installer drops host Tailscale and learns a `[tailscale] service` manifest key, per-run secret caching, `up --wait`, and tailnet URL printing.

**Tech Stack:** Python 3.11+ stdlib (`unittest`, `tomllib`), Docker Compose v2, `tailscale/tailscale:v1.102.5`, `ghcr.io/openclaw/openclaw:2026.9.8`, rune 0.6.0.

**Spec:** `.docs/specs/2026-10-05-openclaw-tailscale-design.md`

## Global Constraints

- Python 3.11+ standard library only; tests use `unittest` (`python3 -m unittest discover -s tests`).
- Light comments: module docstring, docstrings on public functions, inline comments only for a non-obvious "why".
- No em dashes in any file.
- Image pins: `tailscale/tailscale:v1.102.5`, `ghcr.io/openclaw/openclaw:2026.9.8`, `louislam/dockge:1`.
- Both Tailscale nodes: `TS_USERSPACE=false`, `/dev/net/tun`, `NET_ADMIN`, `TS_AUTH_ONCE=true`, `TS_STATE_DIR=/var/lib/tailscale`, `TS_SERVE_CONFIG=/config/serve.json` with the config directory mounted (not the file), `TS_ENABLE_HEALTH_CHECK=true`, `TS_LOCAL_ADDR_PORT=127.0.0.1:9002`.
- Tailnet names: VM node `agents-vm`, OpenClaw node `openclaw` (overridable via `TS_HOSTNAME` in `.env`).
- OpenClaw infra keys pinned on every install: `gateway.mode=local`, `gateway.bind=loopback`, `gateway.trustedProxies=["127.0.0.1"]`, `gateway.auth.rateLimit={"maxAttempts":10,"windowMs":60000,"lockoutMs":300000}`, `logging.file=/home/node/.openclaw/logs/openclaw.log`.
- rune 0.6.0: module task bodies run from the repo root; `[working-directory("x")]` is relative to the repo root.
- Commits: only after the user approves committing. Before the first commit run `git config user.name` (expect `vladtara`) and `gh auth status`. No `Co-Authored-By` trailers, no mention of Claude or agents in messages.
- Test runner with the scratchpad rune binary: `export PATH=/tmp/claude-1000/-home-kreng-dev-agents-infra/817dcffe-1d84-40bb-93dd-d9141dacc2bd/scratchpad/bin:$PATH` (or any `rune` 0.6.0 on PATH).

## Review Focus

1. Missing or invalid `TS_AUTHKEY`: the sidecar never becomes healthy, `up --wait` fails after 180 s; `init.py` must report that component and still install the next one (Task 3 test `test_failed_component_does_not_stop_the_next`).
2. HTTPS certificates disabled or sidecar not answering: `tailscale status --json` has no `CertDomains` or is not JSON; `init.py` prints a hint instead of crashing (Task 3 tests `test_warns_when_tailnet_has_no_https_name`, `test_warns_when_status_is_not_json`).
3. Host `tailscaled` active while installing only `openclaw`: no conflict error, because only the `tailscale` component shares `tailscale0` (Task 2 test `test_host_tailscaled_ignored_without_tailscale_component`, Task 2 cli test `test_host_step_flags_tailscale_component`).
4. `TS_AUTHKEY` leaking into the gateway: OpenClaw services load `.env` via `env_file`; the gateway must see an empty value (Task 5 compose check, Task 7 smoke check).
5. Direct tailnet access to `openclaw:18789` bypassing Serve: must not connect, because the gateway binds loopback and the sidecar uses kernel mode (Task 5 compose check for `--bind loopback` and `TS_USERSPACE=false`; Task 7 optional live check).

---

### Task 1: Sidecar-aware manifest and discovery

Replace the `[tailscale]` manifest keys (`https_port`, `target`) with `service`, discover a top-level `tailscale/` component, and remove the host Serve code and `{ts_hostname}` placeholder from the installer.

**Files:**
- Modify: `installer/components.py`
- Modify: `installer/cli.py`
- Modify: `dockge/component.toml`
- Modify: `stacks/openclaw/component.toml`
- Test: `tests/test_components.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: existing `components.load/discover/select/expand`, `cli.install`.
- Produces:
  - `components.INFRA_DIRS: tuple[str, ...] = ("tailscale", "dockge")`
  - `Component.tailscale_service: str | None` (replaces `tailscale_port`, `tailscale_target`)
  - `cli.install(component: Component, context: dict[str, str], *, interactive: bool) -> None` (Task 3 changes the last parameter)
  - install context keys: `repo_dir`, `stacks_dir`, `uid`, `gid`

- [ ] **Step 1: Update component tests**

In `tests/test_components.py`:

Replace the `OPENCLAW_TOML` lines

```python
set = { HOST = "{ts_hostname}" }
```

with

```python
set = { HOST = "{uid}" }
```

and

```python
[tailscale]
https_port = 443
target = "http://127.0.0.1:18789"
"""
```

with

```python
[tailscale]
service = "tailscale"
"""
```

In `test_loads_full_manifest` replace

```python
        self.assertEqual(comp.env_set, {"HOST": "{ts_hostname}"})
```

with

```python
        self.assertEqual(comp.env_set, {"HOST": "{uid}"})
```

and replace

```python
        self.assertEqual(comp.tailscale_port, 443)
        self.assertEqual(comp.tailscale_target, "http://127.0.0.1:18789")
```

with

```python
        self.assertEqual(comp.tailscale_service, "tailscale")
```

In `test_minimal_manifest_defaults` replace

```python
        self.assertIsNone(comp.tailscale_port)
```

with

```python
        self.assertIsNone(comp.tailscale_service)
```

In `test_rejects_bad_types` replace

```python
        self.assert_invalid('description = "x"\n[tailscale]\nhttps_port = 0\ntarget = "t"\n', "https_port")
```

with

```python
        self.assert_invalid('description = "x"\n[tailscale]\nservice = 1\n', "tailscale.service")
        self.assert_invalid('description = "x"\n[tailscale]\nhttps_port = 443\n', "unknown key.*https_port")
```

Replace `test_finds_dockge_and_stacks_sorted_by_order_then_name` with

```python
    def test_finds_infra_and_stacks_sorted_by_order_then_name(self):
        self.add("dockge", 'description = "d"\norder = 10\n')
        self.add("tailscale", 'description = "t"\norder = 5\n')
        self.add("stacks/zeta", 'description = "z"\norder = 20\n')
        self.add("stacks/alpha", 'description = "a"\norder = 20\n')
        (self.repo / "stacks" / "not-a-component").mkdir()
        names = [c.name for c in components.discover(self.repo)]
        self.assertEqual(names, ["tailscale", "dockge", "alpha", "zeta"])
```

Replace the whole `ExpandTest` class with

```python
class ExpandTest(unittest.TestCase):
    def test_replaces_known_placeholders_only(self):
        text = '[{"path":"x","value":"{uid}"}] {unknown}'
        out = components.expand(text, {"uid": "1000"})
        self.assertEqual(out, '[{"path":"x","value":"1000"}] {unknown}')


REPO = Path(__file__).resolve().parent.parent


class RepoManifestTest(unittest.TestCase):
    def test_shipped_components_load_in_install_order(self):
        names = [c.name for c in components.discover(REPO)]
        self.assertEqual(names, ["dockge", "openclaw"])
```

- [ ] **Step 2: Update CLI tests**

In `tests/test_cli.py`:

Add `import os` after `import io`.

In `APP_TOML` replace

```python
[[setup]]
run = "run --rm app config set --batch-json '[{\\"origin\\":\\"https://{ts_hostname}\\"}]'"

[tailscale]
https_port = 8443
target = "http://127.0.0.1:5001"
"""
```

with

```python
[[setup]]
run = "run --rm app config set --batch-json '[{\\"uid\\":\\"{uid}\\"}]'"

[tailscale]
service = "tailscale"
"""
```

In `CliCase.setUp` delete these two lines:

```python
        self.run = mock.patch.object(cli, "run", return_value=done()).start()
        self.hostname = mock.patch.object(cli.host, "tailnet_hostname", return_value="vm.ts.net").start()
```

Replace `test_full_install_flow` with

```python
    def test_full_install_flow(self):
        code, _, _ = self.main("--skip-host", "app")
        self.assertEqual(code, 0)

        env = envfile.parse((self.app / ".env").read_text())
        self.assertEqual(env["STACKS_DIR"], str(self.repo / "stacks"))
        self.assertRegex(env["TOKEN"], r"^[0-9a-f]{64}$")
        self.assertEqual(env["API_KEY"], "sk-test")
        self.assertTrue((self.app / "data" / "config").is_dir())

        self.assertEqual(
            self.compose_calls(),
            [
                ("pull",),
                ("run", "--rm", "app", "chown"),
                ("run", "--rm", "app", "onboard"),
                ("run", "--rm", "app", "config", "set", "--batch-json", '[{"uid":"%d"}]' % os.getuid()),
                ("up", "-d"),
            ],
        )
```

Delete the whole `test_without_tailscale_uses_localhost_placeholder_and_no_serve` method.

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_components tests.test_cli 2>&1 | tail -3`
Expected: `FAILED` (errors mention the missing `tailscale_service` attribute and `unknown key(s) service`). `RepoManifestTest` passes at this point and must keep passing after Step 6.

- [ ] **Step 4: Implement the manifest change**

In `installer/components.py`:

Replace

```python
TAILSCALE_KEYS = {"https_port", "target"}
```

with

```python
TAILSCALE_KEYS = {"service"}
INFRA_DIRS = ("tailscale", "dockge")
```

In `Component` replace

```python
    tailscale_port: int | None = None
    tailscale_target: str | None = None
```

with

```python
    tailscale_service: str | None = None
```

Replace the body of `discover` up to `found = ...` with

```python
def discover(repo: Path) -> list[Component]:
    """Load the infra components and every stacks/<name>/ that has a component.toml, in install order."""
    candidates = [*(repo / name for name in INFRA_DIRS), *sorted((repo / "stacks").glob("*/"))]
    found = [load(path) for path in candidates if (path / "component.toml").is_file()]
```

(keep the duplicate check and the sorted return unchanged).

In `load` replace the block from `tailscale = data.get("tailscale")` through the `target` check

```python
    tailscale = data.get("tailscale")
    port = target = None
    if tailscale is not None:
        _check_keys(tailscale, TAILSCALE_KEYS, f"{where} [tailscale]")
        port, target = tailscale.get("https_port"), tailscale.get("target")
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ManifestError(f"{where}: 'tailscale.https_port' must be a port number")
        if not isinstance(target, str):
            raise ManifestError(f"{where}: 'tailscale.target' (string) is required")
```

with

```python
    tailscale = data.get("tailscale")
    service = None
    if tailscale is not None:
        _check_keys(tailscale, TAILSCALE_KEYS, f"{where} [tailscale]")
        service = tailscale.get("service")
        if not isinstance(service, str) or not service:
            raise ManifestError(f"{where}: 'tailscale.service' (compose service name) is required")
```

and in the `return Component(...)` call replace

```python
        tailscale_port=port,
        tailscale_target=target,
```

with

```python
        tailscale_service=service,
```

- [ ] **Step 5: Remove host Serve from the installer**

In `installer/cli.py`:

Replace the import line

```python
from installer.shell import CommandError, compose, run, sudo
```

with

```python
from installer.shell import CommandError, compose
```

In `main` replace

```python
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
```

with

```python
    context = {
        "repo_dir": str(repo),
        "stacks_dir": str(repo / "stacks"),
        "uid": str(os.getuid()),
        "gid": str(os.getgid()),
    }
    failed = []
    for component in selected:
        try:
            install(component, context, interactive=not args.yes)
```

Replace the `install` signature

```python
def install(component: Component, context: dict[str, str], *, tailnet: str, interactive: bool) -> None:
```

with

```python
def install(component: Component, context: dict[str, str], *, interactive: bool) -> None:
```

Delete the block at the end of `install`:

```python

    if component.tailscale_target:
        print(f"  local:   {component.tailscale_target}/")
    if component.tailscale_port and tailnet:
        run(sudo(["tailscale", "serve", "--bg", f"--https={component.tailscale_port}", component.tailscale_target]))
        port = "" if component.tailscale_port == 443 else f":{component.tailscale_port}"
        print(f"  tailnet: https://{tailnet}{port}/")
```

- [ ] **Step 6: Update the shipped manifests**

In `dockge/component.toml` delete

```toml

[tailscale]
https_port = 8443
target = "http://127.0.0.1:5001"
```

In `stacks/openclaw/component.toml` replace

```toml
# Pinned after onboarding on every install; rateLimit clears the security
# audit warning. Rerun init.py after `tailscale up` so the tailnet origin
# replaces the localhost placeholder.
```

with

```toml
# Pinned after onboarding on every install; rateLimit clears the security
# audit warning.
```

replace `,"https://{ts_hostname}"]` with `]` in the same step's JSON, and delete

```toml

[tailscale]
https_port = 443
target = "http://127.0.0.1:18789"
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`

Run: `grep -rn "ts_hostname\|tailscale_port\|tailscale_target\|https_port" installer tests dockge stacks`
Expected: one line only, the unknown-key assertion in `tests/test_components.py` (`https_port = 443`).

- [ ] **Step 8: Commit (only if the user approved commits)**

```bash
git add installer/components.py installer/cli.py tests/test_components.py tests/test_cli.py dockge/component.toml stacks/openclaw/component.toml
git commit -m "refactor: replace host tailscale serve with [tailscale] service manifest key"
```

---

### Task 2: Host step without host Tailscale

Stop installing Tailscale on the host; refuse to install the `tailscale` component while the host's own `tailscaled` systemd unit is active.

**Files:**
- Modify: `installer/host.py` (full replacement below)
- Modify: `installer/cli.py` (the `host.ensure` call)
- Test: `tests/test_host.py` (full replacement below), `tests/test_cli.py`

**Interfaces:**
- Consumes: `installer.shell.run`, `installer.shell.sudo`, `installer.envfile.parse`.
- Produces:
  - `host.ensure(*, assume_yes: bool, confirm: Callable[[str], bool], tailscale_component: bool = False) -> None`
  - `host.host_tailscaled_active() -> bool`
  - `host.TAILSCALED_CONFLICT: str`
  - Removed: `tailscale_status`, `hostname_from_status`, `tailnet_hostname`, `install_tailscale`, `TAILSCALE_INSTALL_URL`.

- [ ] **Step 1: Replace `tests/test_host.py`**

```python
import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer import host
from installer.host import HostError

UBUNTU = {"ID": "ubuntu", "VERSION_CODENAME": "noble", "UBUNTU_CODENAME": "noble", "PRETTY_NAME": "Ubuntu 24.04"}
DEBIAN = {"ID": "debian", "VERSION_CODENAME": "bookworm", "PRETTY_NAME": "Debian 12"}
MINT = {"ID": "linuxmint", "ID_LIKE": "ubuntu debian", "VERSION_CODENAME": "wilma", "UBUNTU_CODENAME": "noble"}


class OsReleaseTest(unittest.TestCase):
    def test_parses_and_unquotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "os-release"
            path.write_text('ID=ubuntu\nPRETTY_NAME="Ubuntu 24.04 LTS"\nID_LIKE=debian\n')
            self.assertEqual(
                host.os_release(path),
                {"ID": "ubuntu", "PRETTY_NAME": "Ubuntu 24.04 LTS", "ID_LIKE": "debian"},
            )

    def test_missing_file_is_empty(self):
        self.assertEqual(host.os_release(Path("/nonexistent/os-release")), {})

    def test_supported_distros(self):
        self.assertTrue(host.is_supported(UBUNTU))
        self.assertTrue(host.is_supported(DEBIAN))
        self.assertTrue(host.is_supported(MINT))
        self.assertFalse(host.is_supported({"ID": "fedora", "ID_LIKE": "rhel"}))
        self.assertFalse(host.is_supported({}))


class DockerScriptTest(unittest.TestCase):
    def test_ubuntu_repo(self):
        script = host.docker_install_script(UBUNTU)
        self.assertIn("https://download.docker.com/linux/ubuntu noble stable", script)
        self.assertIn("docker-compose-plugin", script)

    def test_debian_repo(self):
        self.assertIn("https://download.docker.com/linux/debian bookworm stable", host.docker_install_script(DEBIAN))

    def test_ubuntu_derivative_uses_ubuntu_codename(self):
        self.assertIn("linux/ubuntu noble stable", host.docker_install_script(MINT))


class HostTailscaledTest(unittest.TestCase):
    def check(self, which, returncode=0):
        with mock.patch.object(host.shutil, "which", return_value=which), mock.patch.object(
            host, "run", return_value=subprocess.CompletedProcess([], returncode)
        ) as run:
            return host.host_tailscaled_active(), run

    def test_active_unit(self):
        active, run = self.check("/usr/bin/systemctl", 0)
        self.assertTrue(active)
        self.assertEqual(run.call_args.args[0], ["systemctl", "is-active", "--quiet", "tailscaled"])

    def test_inactive_unit(self):
        self.assertFalse(self.check("/usr/bin/systemctl", 3)[0])

    def test_no_systemd(self):
        active, run = self.check(None)
        self.assertFalse(active)
        run.assert_not_called()


class EnsureTest(unittest.TestCase):
    def setUp(self):
        patches = {
            "os_release": mock.patch.object(host, "os_release", return_value=UBUNTU),
            "docker_state": mock.patch.object(host, "docker_state", return_value="ok"),
            "tailscaled_active": mock.patch.object(host, "host_tailscaled_active", return_value=False),
            "which": mock.patch.object(host.shutil, "which", return_value="/usr/local/bin/rune"),
            "install_docker": mock.patch.object(host, "install_docker"),
            "install_rune": mock.patch.object(host, "install_rune"),
            "add_docker_group": mock.patch.object(host, "add_docker_group"),
            "run": mock.patch.object(host, "run"),
        }
        self.m = {name: p.start() for name, p in patches.items()}
        self.addCleanup(mock.patch.stopall)

    def ensure(self, answer=True, assume_yes=False, tailscale_component=False):
        self.asked = []
        with contextlib.redirect_stdout(io.StringIO()):
            host.ensure(
                assume_yes=assume_yes,
                confirm=lambda q: self.asked.append(q) or answer,
                tailscale_component=tailscale_component,
            )

    def test_everything_present_installs_nothing(self):
        self.ensure()
        self.assertEqual(self.asked, [])
        for name in ("install_docker", "install_rune", "run"):
            self.m[name].assert_not_called()

    def test_unsupported_os_only_matters_when_docker_is_missing(self):
        self.m["os_release"].return_value = {"ID": "fedora", "PRETTY_NAME": "Fedora"}
        self.ensure()
        self.m["docker_state"].return_value = "missing"
        with self.assertRaisesRegex(HostError, "Fedora"):
            self.ensure()
        self.m["install_docker"].assert_not_called()

    def test_docker_declined_is_fatal(self):
        self.m["docker_state"].return_value = "missing"
        with self.assertRaisesRegex(HostError, "Docker is required"):
            self.ensure(answer=False)
        self.m["install_docker"].assert_not_called()

    def test_docker_installed_then_needs_relogin(self):
        self.m["docker_state"].side_effect = ["missing", "no-access"]
        with self.assertRaisesRegex(HostError, "newgrp docker"):
            self.ensure()
        self.m["install_docker"].assert_called_once_with(UBUNTU)
        self.m["add_docker_group"].assert_called_once()

    def test_docker_without_compose_is_fatal(self):
        self.m["docker_state"].return_value = "no-compose"
        with self.assertRaisesRegex(HostError, "compose plugin"):
            self.ensure()

    def test_assume_yes_skips_questions(self):
        self.m["docker_state"].side_effect = ["missing", "ok"]
        self.m["which"].return_value = None
        self.ensure(answer=False, assume_yes=True)
        self.assertEqual(self.asked, [])
        self.m["install_docker"].assert_called_once()
        self.m["install_rune"].assert_called_once()

    def test_rune_declined_continues(self):
        self.m["which"].return_value = None
        self.ensure(answer=False)
        self.assertEqual(len(self.asked), 1)
        self.m["install_rune"].assert_not_called()

    def test_host_tailscaled_conflicts_with_tailscale_component(self):
        self.m["tailscaled_active"].return_value = True
        with self.assertRaisesRegex(HostError, "systemctl disable --now tailscaled"):
            self.ensure(tailscale_component=True)
        self.m["install_docker"].assert_not_called()

    def test_host_tailscaled_ignored_without_tailscale_component(self):
        self.m["tailscaled_active"].return_value = True
        self.ensure(tailscale_component=False)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Add the CLI test for the flag**

In `tests/test_cli.py`, add to `CliCase` (after `compose_calls`):

```python
    def add_component(self, rel, toml, env_example="API_KEY=\n"):
        path = self.repo / rel
        path.mkdir(parents=True)
        (path / "component.toml").write_text(toml)
        (path / "compose.yaml").write_text("services: {}\n")
        (path / ".env.example").write_text(env_example)
        return path
```

Add to `InstallTest`:

```python
    def test_host_step_flags_tailscale_component(self):
        self.add_component("tailscale", 'description = "ts"\norder = 5\n')
        self.main("app")
        self.assertFalse(self.ensure.call_args.kwargs["tailscale_component"])
        self.main()
        self.assertTrue(self.ensure.call_args.kwargs["tailscale_component"])
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_host tests.test_cli 2>&1 | tail -3`
Expected: `FAILED` (no attribute `host_tailscaled_active`; `ensure()` got an unexpected keyword `tailscale_component`; `KeyError: 'tailscale_component'`).

- [ ] **Step 4: Replace `installer/host.py`**

```python
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
    "or install without the tailscale component."
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
```

- [ ] **Step 5: Pass the flag from the CLI**

In `installer/cli.py` `main`, replace

```python
        if not args.skip_host:
            host.ensure(assume_yes=args.yes, confirm=confirm)
```

with

```python
        if not args.skip_host:
            host.ensure(
                assume_yes=args.yes,
                confirm=confirm,
                tailscale_component=any(c.name == "tailscale" for c in selected),
            )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`

Run: `grep -rn "tailnet_hostname\|install_tailscale\|tailscale_status" installer tests`
Expected: no output.

- [ ] **Step 7: Commit (only if the user approved commits)**

```bash
git add installer/host.py installer/cli.py tests/test_host.py tests/test_cli.py
git commit -m "feat: drop host tailscale install and guard against host tailscaled"
```

---

### Task 3: Secrets once per run, wait for health, print tailnet URL

**Files:**
- Modify: `installer/cli.py` (full replacement below)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Component.tailscale_service` (Task 1), `host.ensure(..., tailscale_component=...)` (Task 2), `installer.shell.compose(directory, *args, capture=False, check=True)`.
- Produces:
  - `cli.install(component: Component, context: dict[str, str], *, prompt: Callable[[str], str] | None) -> None` (`None` = non-interactive)
  - `cli.remembering(ask: Callable[[str], str]) -> Callable[[str], str]`
  - `cli.tailnet_url(component: Component) -> str` (`""` when no HTTPS name)
  - `cli.WAIT_TIMEOUT = "180"`

- [ ] **Step 1: Write the failing tests**

In `tests/test_cli.py`:

Add `import json` after `import io`.

Add after `APP_TOML`:

```python
OTHER_TOML = """\
description = "Other"
order = 30

[env]
ask = ["API_KEY"]
"""

UP = ("up", "-d", "--wait", "--wait-timeout", "180")
```

In `test_full_install_flow` replace the last expected entry `("up", "-d"),` with `UP,`.

Replace `test_restarts_already_running_stack_after_setup` with

```python
    def test_restarts_running_stack_after_setup_except_the_sidecar(self):
        def fake_compose(path, *args, **kwargs):
            if args[:1] == ("ps",):
                return done("abc123\n")
            if args[:2] == ("config", "--services"):
                return done("app\ntailscale\n")
            return done()

        self.compose.side_effect = fake_compose
        self.main("--skip-host", "app")
        self.assertEqual(self.compose_calls()[-2:], [UP, ("restart", "app")])
```

Add to `InstallTest`:

```python
    def test_same_secret_is_asked_once_per_run(self):
        other = self.add_component("stacks/other", OTHER_TOML)
        self.main("--skip-host")
        self.prompt.assert_called_once_with("API_KEY")
        self.assertEqual(envfile.parse((other / ".env").read_text())["API_KEY"], "sk-test")

    def test_prints_tailnet_url_from_sidecar(self):
        def fake_compose(path, *args, **kwargs):
            if args[:2] == ("exec", "-T"):
                return done(json.dumps({"CertDomains": ["app.tail1234.ts.net"]}))
            return done()

        self.compose.side_effect = fake_compose
        _, out, _ = self.main("--skip-host", "app")
        self.assertIn("tailnet: https://app.tail1234.ts.net/", out)
        exec_calls = [c.args[1:] for c in self.compose.call_args_list if c.args[1:3] == ("exec", "-T")]
        self.assertEqual(exec_calls, [("exec", "-T", "tailscale", "tailscale", "status", "--json")])

    def test_warns_when_tailnet_has_no_https_name(self):
        def fake_compose(path, *args, **kwargs):
            if args[:2] == ("exec", "-T"):
                return done(json.dumps({"CertDomains": None}))
            return done()

        self.compose.side_effect = fake_compose
        code, out, _ = self.main("--skip-host", "app")
        self.assertEqual(code, 0)
        self.assertIn("MagicDNS and HTTPS certificates", out)

    def test_warns_when_status_is_not_json(self):
        code, out, _ = self.main("--skip-host", "app")
        self.assertEqual(code, 0)
        self.assertIn("MagicDNS and HTTPS certificates", out)

    def test_failed_component_does_not_stop_the_next(self):
        other = self.add_component("stacks/other", OTHER_TOML)

        def fake_compose(path, *args, **kwargs):
            if path == self.app and args[:1] == ("up",):
                raise cli.CommandError("`docker compose up` exited with 1")
            return done()

        self.compose.side_effect = fake_compose
        code, _, err = self.main("--skip-host")
        self.assertEqual(code, 1)
        self.assertIn("app: `docker compose up` exited with 1", err)
        up_paths = [c.args[0] for c in self.compose.call_args_list if c.args[1:2] == ("up",)]
        self.assertIn(other, up_paths)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_cli 2>&1 | tail -3`
Expected: `FAILED` (up call lacks `--wait`, prompt called twice, no `tailnet:` output, restart has no service list).

- [ ] **Step 3: Replace `installer/cli.py`**

```python
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
    compose(path, "up", "-d", "--wait", "--wait-timeout", WAIT_TIMEOUT)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`

Run: `uvx ruff check --line-length 120 --target-version py311 --output-format concise installer tests init.py`
Expected: only `init.py:11:4: UP036` (the intentional Python version guard).

- [ ] **Step 5: Commit (only if the user approved commits)**

```bash
git add installer/cli.py tests/test_cli.py
git commit -m "feat: ask shared secrets once, wait for health checks, print tailnet URLs"
```

---

### Task 4: `tailscale/` VM node component

**Files:**
- Create: `tailscale/compose.yaml`, `tailscale/.env.example`, `tailscale/component.toml`, `tailscale/serve/serve.json`, `tailscale/tailscale.rune`
- Modify: `Runefile`, `.gitignore`, `dockge/.env.example`
- Test: `tests/test_components.py` (`RepoManifestTest`)

**Interfaces:**
- Consumes: manifest key `[tailscale] service` (Task 1), discovery of `tailscale/` (Task 1), host conflict check (Task 2).
- Produces: component `tailscale` (order 5), compose service `tailscale`, rune module `tailscale::up|down|logs|status`.

- [ ] **Step 1: Write the failing test**

In `tests/test_components.py` replace `RepoManifestTest` with

```python
class RepoManifestTest(unittest.TestCase):
    def test_shipped_components_load_in_install_order(self):
        found = {c.name: c for c in components.discover(REPO)}
        self.assertEqual(list(found), ["tailscale", "dockge", "openclaw"])
        self.assertEqual(found["tailscale"].tailscale_service, "tailscale")
        self.assertEqual(found["tailscale"].env_ask, ("TS_AUTHKEY",))
        self.assertIsNone(found["dockge"].tailscale_service)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_components.RepoManifestTest 2>&1 | tail -3`
Expected: `FAILED` (`['dockge', 'openclaw'] != ['tailscale', 'dockge', 'openclaw']`).

- [ ] **Step 3: Create `tailscale/compose.yaml`**

```yaml
# The VM's own tailnet node. Host networking puts tailscale0 on the VM, so its
# sshd is reachable over the tailnet, and Serve publishes Dockge over HTTPS.
# Kernel mode on purpose: userspace mode would forward tailnet connections to
# every service bound to the VM's loopback.
services:
  tailscale:
    image: ${TS_IMAGE:-tailscale/tailscale:v1.102.5}
    network_mode: host
    restart: unless-stopped
    environment:
      TS_AUTHKEY: ${TS_AUTHKEY:-}
      TS_HOSTNAME: ${TS_HOSTNAME:-agents-vm}
      TS_STATE_DIR: /var/lib/tailscale
      TS_USERSPACE: "false"
      TS_AUTH_ONCE: "true"
      TS_SERVE_CONFIG: /config/serve.json
      TS_ENABLE_HEALTH_CHECK: "true"
      TS_LOCAL_ADDR_PORT: 127.0.0.1:9002
    volumes:
      - ./data:/var/lib/tailscale
      # Mounted as a directory so edits to serve.json are picked up live.
      - ./serve:/config:ro
    devices:
      - /dev/net/tun:/dev/net/tun
    cap_add:
      - NET_ADMIN
      - NET_RAW
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://127.0.0.1:9002/healthz"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 20s
```

- [ ] **Step 4: Create `tailscale/.env.example`**

```
# Pin a version; tags at https://hub.docker.com/r/tailscale/tailscale/tags
TS_IMAGE=tailscale/tailscale:v1.102.5

# Reusable, pre-approved auth key (admin console > Settings > Keys).
# Only used for the first login; the node identity is kept in data/.
TS_AUTHKEY=

# Machine name on the tailnet: ssh <user>@<name>, https://<name>.<tailnet>.ts.net (Dockge)
TS_HOSTNAME=agents-vm
```

- [ ] **Step 5: Create `tailscale/component.toml`**

```toml
description = "Tailscale: puts the VM on your tailnet (SSH) and serves Dockge over HTTPS"
order = 5
dirs = ["data"]

[env]
ask = ["TS_AUTHKEY"]

[tailscale]
service = "tailscale"
```

- [ ] **Step 6: Create `tailscale/serve/serve.json`**

```json
{
  "TCP": {
    "443": { "HTTPS": true }
  },
  "Web": {
    "${TS_CERT_DOMAIN}:443": {
      "Handlers": {
        "/": { "Proxy": "http://127.0.0.1:5001" }
      }
    }
  }
}
```

- [ ] **Step 7: Create `tailscale/tailscale.rune`**

```
# Start the VM's tailnet node.
[group("tailscale")]
[working-directory("tailscale")]
up:
    docker compose up -d --wait

# Stop the VM's tailnet node.
[group("tailscale")]
[working-directory("tailscale")]
[confirm("Stop Tailscale on this VM? Tailnet SSH and the Dockge URL stop working.")]
down:
    docker compose down

# Follow tailscaled logs.
[group("tailscale")]
[working-directory("tailscale")]
logs:
    docker compose logs -f --tail 100

# Show this node and its tailnet peers.
[group("tailscale")]
[working-directory("tailscale")]
status:
    docker compose exec tailscale tailscale status
```

- [ ] **Step 8: Wire up Runefile, .gitignore and Dockge's env comment**

In `Runefile` replace

```
mod dockge "dockge/dockge.rune"
```

with

```
mod tailscale "tailscale/tailscale.rune"
mod dockge "dockge/dockge.rune"
```

and replace both occurrences of `for d in dockge stacks/*/` with `for d in tailscale dockge stacks/*/`.

In `.gitignore` replace

```
dockge/data/
```

with

```
tailscale/data/
dockge/data/
```

In `dockge/.env.example` replace

```
# Host address the UI listens on. Keep loopback; reach it through Tailscale Serve or an SSH tunnel.
```

with

```
# Host address the UI listens on. Keep loopback; the tailscale component serves it on the tailnet.
```

- [ ] **Step 9: Verify**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`

Run: `python3 -m json.tool tailscale/serve/serve.json >/dev/null && echo json-ok`
Expected: `json-ok`

Run: `rune analyze && rune validate`
Expected: `0 errors, 0 warnings`, then `ok  tailscale`, `ok  dockge`, `ok  stacks/openclaw/`

Run: `rune --list | grep tailscale::`
Expected: `tailscale::up`, `tailscale::down`, `tailscale::logs`, `tailscale::status`

- [ ] **Step 10: Commit (only if the user approved commits)**

```bash
git add tailscale Runefile .gitignore dockge/.env.example tests/test_components.py
git commit -m "feat: add tailscale component for VM SSH and Dockge access"
```

---

### Task 5: OpenClaw sidecar, pinned config, and new tasks

**Files:**
- Modify: `stacks/openclaw/compose.yaml` (full replacement)
- Modify: `stacks/openclaw/.env.example` (full replacement)
- Modify: `stacks/openclaw/component.toml` (full replacement)
- Modify: `stacks/openclaw/openclaw.rune` (full replacement)
- Create: `stacks/openclaw/tailscale/serve.json`
- Modify: `.gitignore`
- Test: `tests/test_components.py` (`RepoManifestTest`)

**Interfaces:**
- Consumes: `[tailscale] service` (Task 1), `tailnet_url` behaviour (Task 3), `restart` excludes the sidecar (Task 3).
- Produces: compose services `tailscale`, `openclaw-gateway`, `openclaw-cli`; rune tasks `openclaw::restart|shell|tui|status|doctor|configure|backup|url` plus the existing ones.

- [ ] **Step 1: Write the failing test**

In `tests/test_components.py` add to `RepoManifestTest`:

```python
    def test_openclaw_manifest_uses_its_sidecar(self):
        openclaw = {c.name: c for c in components.discover(REPO)}["openclaw"]
        self.assertEqual(openclaw.tailscale_service, "tailscale")
        self.assertEqual(openclaw.env_ask, ("TS_AUTHKEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"))
        self.assertIn("backups", openclaw.dirs)
        self.assertIn("data/config/logs", openclaw.dirs)
        config_step = openclaw.setup[-1].run
        for pinned in ('"gateway.bind","value":"loopback"', '"gateway.trustedProxies","value":["127.0.0.1"]',
                       '"logging.file","value":"/home/node/.openclaw/logs/openclaw.log"'):
            self.assertIn(pinned, config_step)
        self.assertNotIn("--no-deps", " ".join(step.run for step in openclaw.setup))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_components.RepoManifestTest 2>&1 | tail -3`
Expected: `FAILED` (`None != 'tailscale'`).

- [ ] **Step 3: Replace `stacks/openclaw/compose.yaml`**

```yaml
# Based on openclaw/openclaw docker-compose.yml (v2026.9.8), adapted for Dockge:
# prebuilt image, relative bind mounts, CLI behind a profile, and a Tailscale
# sidecar that owns the network namespace. The gateway binds loopback and is
# reachable only through the sidecar's Tailscale Serve (HTTPS).
services:
  # Tailnet node "openclaw". Kernel mode on purpose: in userspace mode a direct
  # tailnet connection to port 18789 would reach the gateway as a local client.
  tailscale:
    image: ${TS_IMAGE:-tailscale/tailscale:v1.102.5}
    restart: unless-stopped
    environment:
      TS_AUTHKEY: ${TS_AUTHKEY:-}
      TS_HOSTNAME: ${TS_HOSTNAME:-openclaw}
      TS_STATE_DIR: /var/lib/tailscale
      TS_USERSPACE: "false"
      TS_AUTH_ONCE: "true"
      TS_SERVE_CONFIG: /config/serve.json
      TS_ENABLE_HEALTH_CHECK: "true"
      TS_LOCAL_ADDR_PORT: 127.0.0.1:9002
    volumes:
      - ./data/tailscale:/var/lib/tailscale
      # Mounted as a directory so edits to serve.json are picked up live.
      - ./tailscale:/config:ro
    devices:
      - /dev/net/tun:/dev/net/tun
    cap_add:
      - NET_ADMIN
    # Services sharing this namespace cannot set extra_hosts themselves.
    extra_hosts:
      - "host.docker.internal:host-gateway"
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://127.0.0.1:9002/healthz"]
      interval: 10s
      timeout: 5s
      retries: 3
      start_period: 20s

  openclaw-gateway:
    image: ${OPENCLAW_IMAGE:-ghcr.io/openclaw/openclaw:2026.9.8}
    network_mode: service:tailscale
    env_file:
      - path: .env
        required: false
    environment:
      HOME: /home/node
      OPENCLAW_HOME: /home/node
      TERM: xterm-256color
      # Pin container-side paths so nothing in .env can point them at host paths.
      OPENCLAW_STATE_DIR: /home/node/.openclaw
      OPENCLAW_CONFIG_PATH: /home/node/.openclaw/openclaw.json
      OPENCLAW_CONFIG_DIR: /home/node/.openclaw
      OPENCLAW_WORKSPACE_DIR: /home/node/.openclaw/workspace
      OPENCLAW_GATEWAY_PORT: "18789"
      OPENCLAW_GATEWAY_TOKEN: ${OPENCLAW_GATEWAY_TOKEN:-}
      # env_file passes the whole .env; keep the Tailscale auth key away from the agent.
      TS_AUTHKEY: ""
      TZ: ${OPENCLAW_TZ:-UTC}
    volumes:
      - ./data/config:/home/node/.openclaw
      - ./data/workspace:/home/node/.openclaw/workspace
      - ./data/auth:/home/node/.config/openclaw
      - ./backups:/home/node/backups
      ## Sandbox isolation (agents.defaults.sandbox) needs the host Docker socket,
      ## which is root-equivalent on the host. Set DOCKER_GID to: stat -c '%g' /var/run/docker.sock
      # - /var/run/docker.sock:/var/run/docker.sock
    # group_add:
    #   - "${DOCKER_GID:-999}"
    cap_drop:
      - NET_RAW
      - NET_ADMIN
    security_opt:
      - no-new-privileges:true
    init: true
    restart: unless-stopped
    command: ["node", "dist/index.js", "gateway", "--bind", "loopback", "--port", "18789"]
    healthcheck:
      test: ["CMD", "node", "dist/docker-healthcheck.js"]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 20s

  # One-off CLI: docker compose run --rm openclaw-cli <command>
  # The profile keeps Dockge's "Start" (up -d) from launching it.
  openclaw-cli:
    image: ${OPENCLAW_IMAGE:-ghcr.io/openclaw/openclaw:2026.9.8}
    profiles: ["cli"]
    network_mode: service:tailscale
    env_file:
      - path: .env
        required: false
    environment:
      HOME: /home/node
      OPENCLAW_HOME: /home/node
      TERM: xterm-256color
      OPENCLAW_STATE_DIR: /home/node/.openclaw
      OPENCLAW_CONFIG_PATH: /home/node/.openclaw/openclaw.json
      OPENCLAW_CONFIG_DIR: /home/node/.openclaw
      OPENCLAW_WORKSPACE_DIR: /home/node/.openclaw/workspace
      OPENCLAW_GATEWAY_PORT: "18789"
      OPENCLAW_GATEWAY_TOKEN: ${OPENCLAW_GATEWAY_TOKEN:-}
      TS_AUTHKEY: ""
      BROWSER: echo
      TZ: ${OPENCLAW_TZ:-UTC}
    volumes:
      - ./data/config:/home/node/.openclaw
      - ./data/workspace:/home/node/.openclaw/workspace
      - ./data/auth:/home/node/.config/openclaw
      - ./backups:/home/node/backups
    cap_drop:
      - NET_RAW
      - NET_ADMIN
    security_opt:
      - no-new-privileges:true
    stdin_open: true
    tty: true
    init: true
    entrypoint: ["node", "dist/index.js"]
    depends_on:
      - openclaw-gateway
```

- [ ] **Step 4: Replace `stacks/openclaw/.env.example`**

```
# Pin a version tag; moving tags (latest, main) are rebuilt weekly.
OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.9.8

# Gateway auth token. Generated by init.py; keep it secret.
OPENCLAW_GATEWAY_TOKEN=

# Model provider keys, set at least one. Other providers (GEMINI_API_KEY,
# OPENROUTER_API_KEY, ...) can be added here; every key is passed to the gateway.
# OPENAI_API_KEY also enables vector search for memory (embeddings).
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

OPENCLAW_TZ=UTC

# Tailscale sidecar. Reusable, pre-approved auth key; only used for the first login.
TS_IMAGE=tailscale/tailscale:v1.102.5
TS_AUTHKEY=
# Tailnet machine name: https://<name>.<tailnet>.ts.net
TS_HOSTNAME=openclaw
```

- [ ] **Step 5: Replace `stacks/openclaw/component.toml`**

```toml
description = "OpenClaw: personal AI assistant gateway on its own tailnet node"
order = 20
dirs = ["data/config", "data/config/logs", "data/workspace", "data/auth", "data/tailscale", "backups"]

[env]
generate = ["OPENCLAW_GATEWAY_TOKEN"]
ask = ["TS_AUTHKEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]

# Setup steps mirror upstream scripts/docker/setup.sh (v2026.9.8). No --no-deps:
# one-off containers join the tailscale sidecar's namespace, so it must be running.

# The image runs as uid 1000 ("node"). Fix ownership from a short-lived root
# container, leaving user files inside the workspace untouched.
[[setup]]
run = '''run --rm --user root --entrypoint sh openclaw-gateway -c 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; export PATH; /usr/bin/find -P /home/node/.openclaw -xdev \( ! -path /home/node/.openclaw/workspace -o -prune \) -execdir /usr/bin/chown -h node:node {} +; /usr/bin/chown -h node:node /home/node/.config /home/node/backups; /usr/bin/find -P /home/node/.config/openclaw -xdev -execdir /usr/bin/chown -h node:node {} +; if [ -d /home/node/.openclaw/workspace/.openclaw ] && [ ! -L /home/node/.openclaw/workspace/.openclaw ]; then /usr/bin/find -P /home/node/.openclaw/workspace/.openclaw -xdev -execdir /usr/bin/chown -h node:node {} +; fi || true' '''

[[setup]]
run = "run --rm --entrypoint node openclaw-gateway dist/index.js onboard --mode local --no-install-daemon --gateway-auth token --gateway-token-ref-env OPENCLAW_GATEWAY_TOKEN --skip-ui --suppress-gateway-token-output"
once = "data/.onboarded"
interactive = true

# Infra keys, pinned after onboarding on every install. Serve reaches the
# loopback-bound gateway from 127.0.0.1 with forwarded headers, hence
# trustedProxies. Logs go to the mounted state dir instead of /tmp.
[[setup]]
run = '''run --rm --entrypoint node openclaw-gateway dist/index.js config set --batch-json '[{"path":"gateway.mode","value":"local"},{"path":"gateway.bind","value":"loopback"},{"path":"gateway.trustedProxies","value":["127.0.0.1"]},{"path":"gateway.auth.rateLimit","value":{"maxAttempts":10,"windowMs":60000,"lockoutMs":300000}},{"path":"logging.file","value":"/home/node/.openclaw/logs/openclaw.log"}]' '''

[tailscale]
service = "tailscale"
```

- [ ] **Step 6: Create `stacks/openclaw/tailscale/serve.json`**

```json
{
  "TCP": {
    "443": { "HTTPS": true }
  },
  "Web": {
    "${TS_CERT_DOMAIN}:443": {
      "Handlers": {
        "/": { "Proxy": "http://127.0.0.1:18789" }
      }
    }
  }
}
```

- [ ] **Step 7: Replace `stacks/openclaw/openclaw.rune`**

```
# Start the stack (tailscale sidecar + gateway).
[group("openclaw")]
[working-directory("stacks/openclaw")]
up:
    docker compose up -d --wait

# Stop the stack.
[group("openclaw")]
[working-directory("stacks/openclaw")]
[confirm("Stop OpenClaw and its tailnet node?")]
down:
    docker compose down

# Recreate the gateway, e.g. after the sidecar restarted on its own.
[group("openclaw")]
[working-directory("stacks/openclaw")]
restart:
    docker compose up -d --force-recreate --wait openclaw-gateway

# Follow gateway logs.
[group("openclaw")]
[working-directory("stacks/openclaw")]
logs:
    docker compose logs -f --tail 100 openclaw-gateway

# Pull the image tags set in .env and recreate the stack.
[group("openclaw")]
[working-directory("stacks/openclaw")]
update:
    docker compose pull
    docker compose up -d --wait

# Run interactive onboarding (model provider, channels) again.
[group("openclaw")]
[working-directory("stacks/openclaw")]
[confirm("Rerun OpenClaw onboarding? It can change existing settings.")]
onboard:
    docker compose run --rm --entrypoint node openclaw-gateway dist/index.js onboard --mode local --no-install-daemon --gateway-auth token --gateway-token-ref-env OPENCLAW_GATEWAY_TOKEN --skip-ui --suppress-gateway-token-output
    touch data/.onboarded
    docker compose restart openclaw-gateway

# Run any openclaw CLI command, e.g. `rune openclaw::cli channels list`.
[group("openclaw")]
[working-directory("stacks/openclaw")]
cli +args:
    docker compose run --rm openclaw-cli {{args}}

# Open a bash shell in the gateway container (openclaw is on PATH).
[group("openclaw")]
[working-directory("stacks/openclaw")]
shell:
    docker compose exec openclaw-gateway bash

# Chat in the terminal. Works when Tailscale is down.
[group("openclaw")]
[working-directory("stacks/openclaw")]
tui:
    docker compose run --rm openclaw-cli tui

# Show gateway, channel and session status.
[group("openclaw")]
[working-directory("stacks/openclaw")]
status:
    docker compose run --rm openclaw-cli status

# Diagnose config and state problems.
[group("openclaw")]
[working-directory("stacks/openclaw")]
doctor:
    docker compose run --rm openclaw-cli doctor

# Interactive configuration wizard: models, channels, gateway.
[group("openclaw")]
[working-directory("stacks/openclaw")]
configure:
    docker compose run --rm openclaw-cli configure

# Print the Control UI link with its auth token.
[group("openclaw")]
[working-directory("stacks/openclaw")]
dashboard:
    docker compose run --rm openclaw-cli dashboard --no-open

# List paired and pending devices.
[group("openclaw")]
[working-directory("stacks/openclaw")]
devices:
    docker compose run --rm openclaw-cli devices list

# Approve a pending device by request id.
[group("openclaw")]
[working-directory("stacks/openclaw")]
approve id:
    docker compose run --rm openclaw-cli devices approve {{id}}

# Run OpenClaw's security audit.
[group("openclaw")]
[working-directory("stacks/openclaw")]
audit:
    docker compose run --rm openclaw-cli security audit

# Write a verified backup archive to stacks/openclaw/backups/.
[group("openclaw")]
[working-directory("stacks/openclaw")]
backup:
    docker compose run --rm openclaw-cli backup create --output /home/node/backups --verify

# Print this stack's tailnet URL.
[group("openclaw")]
[working-directory("stacks/openclaw")]
url:
    @docker compose exec -T tailscale tailscale status --json | python3 -c 'import json, sys; d = json.load(sys.stdin).get("CertDomains") or []; print("https://" + d[0] + "/" if d else "no HTTPS name yet: log the node in and enable MagicDNS + HTTPS certificates")'
```

- [ ] **Step 8: Ignore backups**

In `.gitignore` replace

```
stacks/*/data/
```

with

```
stacks/*/data/
stacks/*/backups/
```

- [ ] **Step 9: Verify**

Run: `python3 -m unittest discover -s tests 2>&1 | tail -3`
Expected: `OK`

Run: `python3 -m json.tool stacks/openclaw/tailscale/serve.json >/dev/null && echo json-ok`
Expected: `json-ok`

Run: `rune analyze && rune validate`
Expected: `0 errors, 0 warnings`, then three `ok` lines.

Run the compose checks (Review Focus 4 and 5):

```bash
cd stacks/openclaw && docker compose --profile cli config --format json | python3 -c '
import json, sys
s = json.load(sys.stdin)["services"]
gw, cli, ts = s["openclaw-gateway"], s["openclaw-cli"], s["tailscale"]
assert "ports" not in gw and "ports" not in ts, "no published ports"
assert gw["network_mode"] == cli["network_mode"] == "service:tailscale"
assert gw["environment"]["TS_AUTHKEY"] == "" and cli["environment"]["TS_AUTHKEY"] == ""
assert ts["environment"]["TS_USERSPACE"] == "false"
assert gw["command"][3:5] == ["--bind", "loopback"]
print("compose-ok")
'; cd ../..
```

Expected: `compose-ok`

- [ ] **Step 10: Commit (only if the user approved commits)**

```bash
git add stacks/openclaw .gitignore tests/test_components.py
git commit -m "feat: run openclaw behind its own tailscale sidecar with loopback gateway"
```

---

### Task 6: Documentation

**Files:**
- Create: `stacks/openclaw/README.md`
- Modify: `README.md` (full replacement)
- Modify: `.docs/specs/2026-10-05-openclaw-tailscale-design.md` (caveat wording)

**Interfaces:**
- Consumes: task names from Tasks 4 and 5, pinned keys from Task 5.
- Produces: operator docs.

- [ ] **Step 1: Create `stacks/openclaw/README.md`**

````markdown
# OpenClaw

[OpenClaw](https://github.com/openclaw/openclaw) personal AI assistant gateway, pinned to `ghcr.io/openclaw/openclaw:2026.9.8`, on its own tailnet node.

## How it runs

| Service | Role |
|---------|------|
| `tailscale` | Tailnet node `openclaw`. Owns the network namespace and serves `https://openclaw.<tailnet>.ts.net` to `http://127.0.0.1:18789` (`tailscale/serve.json`). |
| `openclaw-gateway` | The gateway, `--bind loopback`, inside the sidecar's namespace. No host ports. |
| `openclaw-cli` | One-off CLI in the same namespace (`docker compose run --rm openclaw-cli <command>`). The `cli` profile keeps Dockge's Start from launching it. |

Requests from Serve arrive over loopback with forwarded headers. `init.py` sets `gateway.trustedProxies: ["127.0.0.1"]` so they are accepted; they still need the gateway token and a one-time device approval. The sidecar runs Tailscale in kernel mode: in userspace mode a tailnet peer could reach `127.0.0.1:18789` directly and look like a local client.

## First login

1. `python3 init.py openclaw` (or `rune install openclaw`) prints the URL.
2. `rune openclaw::dashboard` prints a link that carries the gateway token. Open it on a device in your tailnet.
3. The browser shows up as a pending device: `rune openclaw::devices`, then `rune openclaw::approve <requestId>`.

If onboarding was skipped (`init.py --yes`), run `rune openclaw::onboard`.

## Where the data lives

Everything is under `stacks/openclaw/`, ignored by git, owned by uid 1000.

| Host | In the container | Contents |
|------|------------------|----------|
| `data/config/` | `~/.openclaw` | `openclaw.json` (config); `state/openclaw.sqlite` (devices, pairing, cron, plugin state, shared auth); `agents/<id>/agent/openclaw-agent.sqlite` (sessions, transcripts, memory index); `credentials/` (channel logins); plugins in `npm/`, `git/`, `extensions/`; global skills in `skills/`; logs in `logs/` |
| `data/workspace/` | `~/.openclaw/workspace` | The agent's files: `AGENTS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md`, `MEMORY.md`, `memory/YYYY-MM-DD.md`, `DREAMS.md`, workspace `skills/` |
| `data/auth/` | `~/.config/openclaw` | Key for legacy encrypted OAuth credentials |
| `data/tailscale/` | sidecar `/var/lib/tailscale` | Tailnet node identity |
| `backups/` | `/home/node/backups` | `rune openclaw::backup` archives |

Treat `data/config`, `data/auth` and `backups/` as credentials: OAuth tokens are stored as plain text in SQLite. Never copy the live `*.sqlite` files; use `rune openclaw::backup` or stop the stack first. The workspace is a good candidate for its own private git repo.

## Memory

- Memory is Markdown in the workspace. `MEMORY.md` is loaded at the start of every session; daily notes in `memory/YYYY-MM-DD.md` are searched on demand. Edit or delete these files to change what the agent remembers.
- The search index (full text plus vectors) lives in the per-agent SQLite database and reindexes when the files change.
- Vector search needs an embeddings provider. The default is OpenAI (`OPENAI_API_KEY`); with only an Anthropic key you get keyword search. Settings live under `memory.search` (`provider`, `model`, `sources`); this repo does not pin them.
- Commands: `rune openclaw::cli memory status`, `rune openclaw::cli memory search "query"`, `rune openclaw::cli memory index --force`, `rune openclaw::cli memory reset`. Never delete the SQLite files to reset memory.

## Plugins and skills

- Plugins: `rune openclaw::cli plugins list|search|install <pkg>|enable <id>|disable <id>|update --all`, or the Control UI. Changes apply without a restart and persist in `data/config`. Per-plugin settings live under `plugins.entries.<id>`.
- Skills: `rune openclaw::cli skills search|install @owner/<slug>|list`. Installs go to the workspace `skills/`; add `--global` for `data/config/skills/`.
- OS packages a plugin or skill needs (`apt install ...`) do not survive container recreation. They need a custom image set in `OPENCLAW_IMAGE`.

## Shell and management

| Task | Does |
|------|------|
| `rune openclaw::shell` | bash in the gateway container; `openclaw` is on PATH |
| `rune openclaw::tui` | terminal chat; works when Tailscale is down |
| `rune openclaw::status` | gateway, channels, sessions |
| `rune openclaw::doctor` | diagnose config and state |
| `rune openclaw::configure` | interactive wizard: models, channels, gateway |
| `rune openclaw::cli <args>` | any `openclaw` command, e.g. `channels list`, `agents list`, `logs` |
| `rune openclaw::dashboard` | Control UI link with token |
| `rune openclaw::devices`, `rune openclaw::approve <id>` | pair browsers and apps |
| `rune openclaw::audit` | security audit |
| `rune openclaw::backup` | verified archive into `backups/` |
| `rune openclaw::url` | tailnet URL |
| `rune openclaw::up`, `down`, `restart`, `logs`, `update` | lifecycle |

Without rune: `cd stacks/openclaw`, then `docker compose run --rm openclaw-cli <command>` or `docker compose exec openclaw-gateway bash`.

## Configuration

OpenClaw owns `data/config/openclaw.json` (JSON5 with a strict schema: unknown keys stop the gateway). Change it with:

- the Control UI **Config** tab (form or raw JSON),
- `rune openclaw::configure`,
- `rune openclaw::cli config get|set|unset <path> [value]` and `rune openclaw::cli config validate`.

Most changes apply live (agents, models, channels, tools, skills, plugins, logging); gateway network settings restart the gateway automatically. OpenClaw rewrites the file as plain JSON, so comments do not survive. Secrets belong in `.env` (passed to the gateway) and can be referenced from config as `${VAR}`.

`init.py` re-applies these keys on every run. Change them in `component.toml`, not by hand:

| Key | Value | Why |
|-----|-------|-----|
| `gateway.mode` | `local` | Docker setup |
| `gateway.bind` | `loopback` | only the sidecar's Serve can reach the gateway |
| `gateway.trustedProxies` | `["127.0.0.1"]` | accept Serve's forwarded requests |
| `gateway.auth.rateLimit` | 10 attempts per minute, 5 minute lockout | brute-force protection |
| `logging.file` | `/home/node/.openclaw/logs/openclaw.log` | logs survive container recreation (default is `/tmp`) |

## Troubleshooting

- **URL does not load:** `rune openclaw::url`. "no HTTPS name" means the node is not logged in (`docker compose logs tailscale`) or MagicDNS and HTTPS certificates are off in the admin console.
- **Gateway lost its network after the sidecar restarted on its own:** `rune openclaw::restart` recreates the gateway in the sidecar's current namespace.
- **`proxy_attribution_required`:** `gateway.trustedProxies` is missing; rerun `python3 init.py openclaw`.
- **`blocked plugin candidate: suspicious ownership`:** files not owned by uid 1000; rerun `python3 init.py openclaw`, which fixes ownership.
- **Logs:** `data/config/logs/openclaw.log`, or `rune openclaw::logs` for container output.
````

- [ ] **Step 2: Replace `README.md`**

````markdown
# agents-infra

Run and manage AI tools in Docker on a single Linux VM. The repo is cloned on the VM, every tool is a Docker Compose stack, and [Dockge](https://github.com/louislam/dockge) gives a web UI over them. Git stays the source of truth: edits made in Dockge show up in `git diff`. Access goes through [Tailscale](https://tailscale.com) running in containers; nothing listens on public interfaces.

| Component | What it is | Tailnet address |
|-----------|------------|-----------------|
| `tailscale` | The VM's own tailnet node: SSH to the VM, serves Dockge | `ssh <user>@agents-vm` |
| `dockge` | Web UI for the stacks in `stacks/` | `https://agents-vm.<tailnet>.ts.net` |
| `openclaw` | [OpenClaw](https://github.com/openclaw/openclaw) AI assistant gateway on its own tailnet node ([guide](stacks/openclaw/README.md)) | `https://openclaw.<tailnet>.ts.net` |

## Before you start

In the [Tailscale admin console](https://login.tailscale.com/admin):

1. **DNS:** enable MagicDNS and HTTPS certificates. Without them the HTTPS URLs do not work.
2. **Settings > Keys:** generate an auth key that is **reusable** and **pre-approved**. `init.py` asks for it once and uses it for both nodes (`agents-vm`, `openclaw`). It is only needed for the first login.
3. After the first install, open **Machines** and choose **Disable key expiry** for both nodes, or use a tagged auth key (expiry is off for tagged nodes).

If the VM already runs Tailscale from a package, disable it first (`sudo systemctl disable --now tailscaled`): the containerized node uses the same `tailscale0` interface, and `init.py` refuses to continue while it runs.

## Quick start

On a fresh Ubuntu 24.04+ or Debian 12+ VM (Python 3.11+ and sudo):

```sh
sudo git clone https://github.com/vladtara/agents-infra.git /opt/agents-infra
sudo chown -R "$USER": /opt/agents-infra
cd /opt/agents-infra
python3 init.py
```

`init.py` asks before installing anything on the host, then:

1. installs Docker Engine + compose plugin (Docker's apt repo) and [rune](https://github.com/rune-task-runner/rune) if missing
2. writes each component's `.env` from `.env.example`, generating secrets and asking for the Tailscale auth key and API keys (each asked once per run)
3. pulls images, runs first-time setup (OpenClaw onboarding), starts the stacks and waits for their health checks
4. prints each tailnet URL

It is safe to rerun: existing `.env` values and secrets are kept, one-time steps are skipped.

```sh
python3 init.py openclaw     # install or update one component
python3 init.py --list       # components and their state
python3 init.py --yes        # no questions; interactive steps are skipped
python3 init.py --skip-host  # components only, no host checks
```

If Docker was just installed, log out and back in (or run `newgrp docker`) and rerun `init.py`.

## Access

Every UI listens on loopback only and is published on the tailnet with Tailscale Serve (HTTPS). Tailscale runs in kernel mode, so tailnet peers reach only what Serve publishes plus the VM's SSH server.

- **SSH:** `ssh <user>@agents-vm`, the VM's own sshd over the tailnet. Closing public SSH in your cloud firewall is optional and up to you.
- **Dockge:** `https://agents-vm.<tailnet>.ts.net`. It asks you to create an admin account on first visit. Without Tailscale: `ssh -L 5001:127.0.0.1:5001 <user>@<public-ip>`, then http://127.0.0.1:5001.
- **OpenClaw:** `https://openclaw.<tailnet>.ts.net`. Without Tailscale there is no network path by design; use `rune openclaw::tui` on the VM. First login and operations: [stacks/openclaw/README.md](stacks/openclaw/README.md).

`rune tailscale::status` shows the VM node; `rune openclaw::url` prints OpenClaw's address.

## Daily tasks

`rune --list` shows everything. Main ones:

```sh
rune install [components...]   # same as python3 init.py
rune list                      # component state
rune ps                        # all compose projects on the host
rune validate                  # docker compose config for every component
rune update                    # git pull, then pull images and recreate installed components
rune test                      # installer unit tests

rune tailscale::up | down | logs | status
rune dockge::up | down | logs | update
rune openclaw::up | down | restart | logs | update
rune openclaw::shell | tui | status | doctor | configure | backup | url
rune openclaw::cli channels list
```

Without rune, use `docker compose` inside a component folder; the project name is the folder name, the same one Dockge uses.

## Layout

```
init.py            entry point (Python stdlib only)
installer/         install logic: host prerequisites, manifests, .env rendering
Runefile           root rune tasks; each component adds a module
tailscale/         the VM's tailnet node, outside stacks/ so Dockge cannot stop it
dockge/            Dockge itself, outside stacks/ so it never manages itself
stacks/            DOCKGE_STACKS_DIR: one folder per tool
  openclaw/
tests/             python3 -m unittest discover -s tests
.docs/             design specs and implementation plans
```

Each component folder holds:

| File | Purpose | In git |
|------|---------|--------|
| `compose.yaml` | the stack | yes |
| `.env.example` | every setting, with defaults and comments | yes |
| `component.toml` | how `init.py` installs it | yes |
| `<name>.rune` | component tasks (optional) | yes |
| `.env` | real settings and secrets, mode 0600 | no |
| `data/` | persistent state | no |

## Adding a tool

1. Create `stacks/<name>/` (lowercase, digits, `-`, `_`) with `compose.yaml` and `.env.example`. Keep state in relative `./data/...` mounts and do not publish ports on public interfaces.
2. Add `component.toml`:

   ```toml
   description = "What it is"
   order = 30                      # install order; tailscale = 5, dockge = 10, openclaw = 20
   dirs = ["data"]                 # created before the stack starts

   [env]
   generate = ["APP_SECRET"]       # random 64-hex value when empty
   ask = ["SOME_API_KEY"]          # asked once per run; blank keeps the default
   set = { DATA_ROOT = "{stacks_dir}/<name>/data" }  # rewritten on every run

   [[setup]]                       # `docker compose <run>`, in order, before `up -d --wait`
   run = "run --rm app migrate"
   once = "data/.migrated"         # skip while this marker exists; created on success
   interactive = false             # true: skipped with --yes

   [tailscale]                     # optional: print the URL of this stack's Tailscale sidecar
   service = "tailscale"
   ```

   Placeholders in `set` and `run`: `{repo_dir}`, `{stacks_dir}`, `{uid}`, `{gid}`. Every key named in `[env]` must exist in `.env.example`. If a setup step ran while the stack was already running, `init.py` restarts it (except the sidecar).
3. To give the tool its own tailnet name, copy the `tailscale` service and the `tailscale/serve.json` folder from `stacks/openclaw/`, point `Proxy` at the app's port, set `network_mode: service:tailscale` on the app, add `TS_IMAGE`, `TS_AUTHKEY`, `TS_HOSTNAME` to `.env.example` and `TS_AUTHKEY` to `[env] ask`. If the app loads `.env` with `env_file`, set `TS_AUTHKEY: ""` in its `environment:`.
4. Optionally add `stacks/<name>/<name>.rune` (use `[working-directory("stacks/<name>")]` on each task) and a `mod <name> "stacks/<name>/<name>.rune"` line in `Runefile`.
5. `rune validate && python3 init.py <name>`

Stacks created from the Dockge UI work too. They have no `component.toml`, so `init.py` ignores them.

## Security notes

- Dockge mounts the Docker socket, which is root-equivalent on the host. It listens on loopback only; leave its console disabled (the default).
- The Tailscale containers have `NET_ADMIN` and `/dev/net/tun`; the VM node shares the host network. Treat `tailscale/data/` and `stacks/*/data/tailscale/` as credentials (node keys).
- OpenClaw's sandbox (agents in sibling containers) is off. Enabling it needs the Docker socket; see the comments in `stacks/openclaw/compose.yaml`.
- OpenClaw keeps OAuth tokens in plain SQLite under `stacks/openclaw/data/`. Treat that folder and `stacks/openclaw/backups/` as credentials.
- Dockge 1.5.0 (`louislam/dockge:1`) ignores `PUID`/`PGID`, so stacks *created* in the UI are owned by root until a release ships that support. Editing existing files keeps their owner. `DOCKGE_IMAGE=louislam/dockge:nightly` in `dockge/.env` has it today.
````

- [ ] **Step 3: Fix the spec's caveat wording**

In `.docs/specs/2026-10-05-openclaw-tailscale-design.md` replace

```
- If the sidecar restarts outside Compose (crash), the gateway and cli lose networking. Fix: `rune openclaw::up` (recreates dependents). Documented.
```

with

```
- If the sidecar restarts outside Compose (crash), the gateway loses networking. Fix: `rune openclaw::restart` (recreates the gateway in the sidecar's current namespace). Documented.
```

and in the same file replace

```
`openclaw.rune` adds: `shell` (`exec openclaw-gateway bash`), `tui`, `status`, `doctor`, `configure`, `backup` (`backup create --output /home/node/backups --verify`), `url` (prints tailnet URL). Existing tasks stay.
```

with

```
`openclaw.rune` adds: `restart` (force-recreate the gateway), `shell` (`exec openclaw-gateway bash`), `tui`, `status`, `doctor`, `configure`, `backup` (`backup create --output /home/node/backups --verify`), `url` (prints tailnet URL). Existing tasks stay. Setup steps and `onboard` drop `--no-deps`: one-off containers join the sidecar's namespace, so the sidecar must be running.
```

- [ ] **Step 4: Verify**

Run: `grep -rnP "\x{2014}" README.md stacks/openclaw/README.md .docs || echo no-em-dash`
Expected: `no-em-dash`

Run: `grep -n "tailscale serve\|BIND_IP=.*tailnet\|https_port" README.md stacks/openclaw/README.md`
Expected: no output.

Run: `rune --list | grep -c "openclaw::"`
Expected: `18`

- [ ] **Step 5: Commit (only if the user approved commits)**

```bash
git add README.md stacks/openclaw/README.md .docs/specs/2026-10-05-openclaw-tailscale-design.md
git commit -m "docs: document tailscale access and openclaw operations"
```

---

### Task 7: Smoke test on a scratchpad copy

No repo changes. Validates the OpenClaw stack end to end without a tailnet (and optionally with one). The host-network VM node is not started on the developer machine.

**Files:** none (scratchpad only)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Copy the repo**

```bash
S=/tmp/claude-1000/-home-kreng-dev-agents-infra/817dcffe-1d84-40bb-93dd-d9141dacc2bd/scratchpad/smoke
rm -rf "$S" && mkdir -p "$S"
git -C /home/kreng/dev/agents-infra ls-files -co --exclude-standard | tar -C /home/kreng/dev/agents-infra -cf - -T - | tar -xf - -C "$S"
```

- [ ] **Step 2: Install OpenClaw without an auth key**

Run: `cd "$S" && python3 init.py --skip-host --yes openclaw; echo "rc=$?"`
Expected: `.env` written, both images pulled, ownership step and config-set step succeed (OpenClaw prints how many config paths it updated), onboarding skipped, then after up to 180 s `error: openclaw: ... exited with 1` because the sidecar is not logged in, and `rc=1`.

- [ ] **Step 3: Check the stack from inside**

```bash
cd "$S/stacks/openclaw"
docker compose ps --format '{{.Service}} {{.Status}}'
docker compose exec -T openclaw-gateway curl -fsS --retry 20 --retry-delay 3 --retry-all-errors http://127.0.0.1:18789/healthz
ss -ltn | grep -E ':18789\b' || echo "no host listener"
docker compose exec -T openclaw-gateway printenv TS_AUTHKEY | wc -c
docker compose run --rm -T openclaw-cli config get gateway.bind
docker compose run --rm -T openclaw-cli config get gateway.trustedProxies
ls data/config/logs/
docker compose logs tailscale 2>&1 | grep -iE "login|authkey|NeedsLogin" | head -3
```

Expected, in order: `tailscale Up ... (unhealthy or health: starting)` and `openclaw-gateway Up ... (healthy)`; `{"ok":true,"status":"live"}`; `no host listener`; `1` (empty value plus newline); `loopback`; `["127.0.0.1"]` (formatting may differ); `openclaw.log`; a line asking for login.

- [ ] **Step 4: Backup task**

Run: `cd "$S" && PATH=/tmp/claude-1000/-home-kreng-dev-agents-infra/817dcffe-1d84-40bb-93dd-d9141dacc2bd/scratchpad/bin:$PATH rune openclaw::backup 2>&1 | tail -3 && ls stacks/openclaw/backups/`
Expected: backup reports a verified archive; one archive file listed.

- [ ] **Step 5: Optional live tailnet check (only with a throwaway auth key from the user)**

```bash
cd "$S/stacks/openclaw"
sed -i 's/^TS_AUTHKEY=.*/TS_AUTHKEY=<key from the user>/' .env
docker compose up -d --force-recreate --wait
PATH=/tmp/claude-1000/-home-kreng-dev-agents-infra/817dcffe-1d84-40bb-93dd-d9141dacc2bd/scratchpad/bin:$PATH rune openclaw::url
```

Expected: `https://openclaw.<tailnet>.ts.net/`. From another tailnet device: the URL loads the Control UI; `curl -m 5 http://openclaw.<tailnet>.ts.net:18789/` fails to connect (Review Focus 5). Remove the `openclaw` machine from the admin console afterwards.

- [ ] **Step 6: Tear down**

```bash
cd "$S/stacks/openclaw" && docker compose --profile cli down -t 5
docker run --rm -v "$S:/w" --entrypoint rm tailscale/tailscale:v1.102.5 -rf /w/stacks/openclaw/data
rm -rf "$S"
docker compose ls --all
```

Expected: no `openclaw` project listed.

- [ ] **Step 7: Report**

Report to the user: unit test count, `rune validate` result, each Step 3 check, backup result, whether Step 5 ran, and what remains for the VM run (`python3 init.py` on the VM, `ssh <user>@agents-vm`, both HTTPS URLs, `rune openclaw::audit`).
