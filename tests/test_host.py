import contextlib
import io
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


class TailnetHostnameTest(unittest.TestCase):
    def test_running_returns_dns_name_without_dot(self):
        status = {"BackendState": "Running", "Self": {"DNSName": "vm.tail1234.ts.net."}}
        self.assertEqual(host.hostname_from_status(status), "vm.tail1234.ts.net")

    def test_not_running_or_missing(self):
        self.assertEqual(host.hostname_from_status({"BackendState": "NeedsLogin", "Self": {"DNSName": "x."}}), "")
        self.assertEqual(host.hostname_from_status(None), "")


class EnsureTest(unittest.TestCase):
    def setUp(self):
        patches = {
            "os_release": mock.patch.object(host, "os_release", return_value=UBUNTU),
            "docker_state": mock.patch.object(host, "docker_state", return_value="ok"),
            "tailscale_status": mock.patch.object(
                host, "tailscale_status", return_value={"BackendState": "Running", "Self": {"DNSName": "vm."}}
            ),
            "which": mock.patch.object(host.shutil, "which", return_value="/usr/local/bin/rune"),
            "install_docker": mock.patch.object(host, "install_docker"),
            "install_tailscale": mock.patch.object(host, "install_tailscale"),
            "install_rune": mock.patch.object(host, "install_rune"),
            "add_docker_group": mock.patch.object(host, "add_docker_group"),
            "run": mock.patch.object(host, "run"),
        }
        self.m = {name: p.start() for name, p in patches.items()}
        self.addCleanup(mock.patch.stopall)

    def ensure(self, answer=True, assume_yes=False):
        self.asked = []
        with contextlib.redirect_stdout(io.StringIO()):
            host.ensure(assume_yes=assume_yes, confirm=lambda q: self.asked.append(q) or answer)

    def test_everything_present_installs_nothing(self):
        self.ensure()
        self.assertEqual(self.asked, [])
        for name in ("install_docker", "install_tailscale", "install_rune", "run"):
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

    def test_optional_tools_declined_continue(self):
        self.m["tailscale_status"].return_value = None
        self.m["which"].return_value = None
        self.ensure(answer=False)
        self.assertEqual(len(self.asked), 2)
        self.m["install_tailscale"].assert_not_called()
        self.m["install_rune"].assert_not_called()

    def test_tailscale_logged_out_runs_up(self):
        self.m["tailscale_status"].return_value = {"BackendState": "NeedsLogin"}
        self.ensure()
        self.m["run"].assert_called_once()
        self.assertEqual(self.m["run"].call_args.args[0][-2:], ["tailscale", "up"])


if __name__ == "__main__":
    unittest.main()
