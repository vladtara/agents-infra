import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from installer import cli, envfile

APP_TOML = """\
description = "Test app"
order = 20
dirs = ["data/config"]

[env]
set = { STACKS_DIR = "{stacks_dir}" }
generate = ["TOKEN"]
ask = ["API_KEY"]

[[setup]]
run = "run --rm app chown"

[[setup]]
run = "run --rm app onboard"
once = "data/.onboarded"
interactive = true

[[setup]]
run = "run --rm app config set --batch-json '[{\\"uid\\":\\"{uid}\\"}]'"

[tailscale]
service = "tailscale"
"""

OTHER_TOML = """\
description = "Other"
order = 30

[env]
ask = ["API_KEY"]
"""

HEADLESS_TOML = """\
description = "Headless"
order = 40

[[setup]]
run = "run --rm hl onboard"
once = "data/.onboarded"
interactive = true
headless = "run -T --rm hl onboard --non-interactive"
"""

UP = ("up", "-d", "--wait", "--wait-timeout", "180")


def done(stdout=""):
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


class CliCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self.app = self.repo / "stacks" / "app"
        self.app.mkdir(parents=True)
        (self.app / "component.toml").write_text(APP_TOML)
        (self.app / "compose.yaml").write_text("services: {}\n")
        (self.app / ".env.example").write_text("STACKS_DIR=\nTOKEN=\nAPI_KEY=\nTZ=UTC\n")

        self.compose = mock.patch.object(cli, "compose", return_value=done()).start()
        self.ensure = mock.patch.object(cli.host, "ensure").start()
        self.prompt = mock.patch.object(cli, "ask_secret", return_value="sk-test").start()
        self.addCleanup(mock.patch.stopall)

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv), repo=self.repo)
        return code, out.getvalue(), err.getvalue()

    def compose_calls(self):
        return [call.args[1:] for call in self.compose.call_args_list if not call.kwargs.get("capture")]

    def add_component(self, rel, toml, env_example="API_KEY=\n"):
        path = self.repo / rel
        path.mkdir(parents=True)
        (path / "component.toml").write_text(toml)
        (path / "compose.yaml").write_text("services: {}\n")
        (path / ".env.example").write_text(env_example)
        return path


class SelectionTest(CliCase):
    def test_unknown_component_fails_with_available_names(self):
        code, _, err = self.main("nope")
        self.assertEqual(code, 1)
        self.assertIn("unknown component(s): nope. Available: app", err)
        self.compose.assert_not_called()

    def test_list_shows_state_without_installing(self):
        code, out, _ = self.main("--list")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"app\s+not installed\s+Test app")
        self.ensure.assert_not_called()
        self.compose.assert_not_called()

    def test_list_reports_running(self):
        (self.app / ".env").write_text("TOKEN=x\n")
        self.compose.return_value = done("abc123\n")
        _, out, _ = self.main("--list")
        self.assertRegex(out, r"app\s+running")


class InstallTest(CliCase):
    def test_host_step_flags_tailscale_component(self):
        self.add_component("tailscale", 'description = "ts"\norder = 5\n')
        self.main("app")
        self.assertFalse(self.ensure.call_args.kwargs["tailscale_component"])
        self.main()
        self.assertTrue(self.ensure.call_args.kwargs["tailscale_component"])

    def test_host_step_runs_unless_skipped(self):
        self.main("app")
        self.ensure.assert_called_once()
        self.ensure.reset_mock()
        self.main("--skip-host", "app")
        self.ensure.assert_not_called()

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
                ("run", "--rm", "app", "config", "set", "--batch-json", f'[{{"uid":"{os.getuid()}"}}]'),
                UP,
            ],
        )

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

    def test_rerun_keeps_secrets_and_skips_done_steps(self):
        self.main("--skip-host", "app")
        token = envfile.parse((self.app / ".env").read_text())["TOKEN"]
        self.assertTrue((self.app / "data" / ".onboarded").is_file())
        self.compose.reset_mock()
        self.prompt.reset_mock()

        self.main("--skip-host", "app")
        self.assertEqual(envfile.parse((self.app / ".env").read_text())["TOKEN"], token)
        self.prompt.assert_not_called()
        self.assertNotIn(("run", "--rm", "app", "onboard"), self.compose_calls())

    def test_yes_skips_prompts_and_interactive_steps(self):
        _, out, _ = self.main("--skip-host", "--yes", "app")
        self.prompt.assert_not_called()
        self.assertNotIn(("run", "--rm", "app", "onboard"), self.compose_calls())
        self.assertIn("skipped interactive step", out)
        self.assertFalse((self.app / "data" / ".onboarded").exists())

    def test_failed_once_step_leaves_no_marker(self):
        def fake_compose(path, *args, **kwargs):
            if "onboard" in args:
                raise cli.CommandError("boom")
            return done()

        self.compose.side_effect = fake_compose
        code, _, _ = self.main("--skip-host", "app")
        self.assertEqual(code, 1)
        self.assertFalse((self.app / "data" / ".onboarded").exists())

    def test_failed_component_reports_and_exits_non_zero(self):
        self.compose.side_effect = cli.CommandError("`docker compose pull` exited with 1")
        code, _, err = self.main("--skip-host", "app")
        self.assertEqual(code, 1)
        self.assertIn("app: `docker compose pull` exited with 1", err)


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


    def test_failed_up_explains_tailscale_login(self):
        def fake_compose(path, *args, **kwargs):
            if args[:1] == ("up",):
                raise cli.CommandError("`docker compose up -d --wait --wait-timeout 180` exited with 1")
            return done()

        self.compose.side_effect = fake_compose
        code, _, err = self.main("--skip-host", "app")
        self.assertEqual(code, 1)
        self.assertIn(f"TS_AUTHKEY in {self.app / '.env'}", err)
        self.assertIn("docker compose logs tailscale", err)

    def test_failed_up_without_sidecar_has_no_login_hint(self):
        self.add_component("stacks/other", OTHER_TOML)

        def fake_compose(path, *args, **kwargs):
            if args[:1] == ("up",):
                raise cli.CommandError("`docker compose up` exited with 1")
            return done()

        self.compose.side_effect = fake_compose
        code, _, err = self.main("--skip-host", "other")
        self.assertEqual(code, 1)
        self.assertNotIn("TS_AUTHKEY", err)

    def calls_for(self, path):
        return [c.args[1:] for c in self.compose.call_args_list if c.args[0] == path and not c.kwargs.get("capture")]

    def test_yes_runs_headless_variant_of_interactive_step(self):
        hl = self.add_component("stacks/hl", HEADLESS_TOML)
        _, out, _ = self.main("--skip-host", "--yes", "hl")
        calls = self.calls_for(hl)
        self.assertIn(("run", "-T", "--rm", "hl", "onboard", "--non-interactive"), calls)
        self.assertNotIn(("run", "--rm", "hl", "onboard"), calls)
        self.assertTrue((hl / "data" / ".onboarded").is_file())
        self.assertNotIn("skipped interactive step", out)

    def test_interactive_run_ignores_headless_variant(self):
        hl = self.add_component("stacks/hl", HEADLESS_TOML)
        self.main("--skip-host", "hl")
        calls = self.calls_for(hl)
        self.assertIn(("run", "--rm", "hl", "onboard"), calls)
        self.assertNotIn(("run", "-T", "--rm", "hl", "onboard", "--non-interactive"), calls)


if __name__ == "__main__":
    unittest.main()
