import contextlib
import io
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
                ("run", "--rm", "app", "config", "set", "--batch-json", '[{"uid":"%d"}]' % os.getuid()),
                ("up", "-d"),
            ],
        )

    def test_restarts_already_running_stack_after_setup(self):
        def fake_compose(path, *args, **kwargs):
            return done("abc123\n") if args[:1] == ("ps",) else done()

        self.compose.side_effect = fake_compose
        self.main("--skip-host", "app")
        self.assertEqual(self.compose_calls()[-2:], [("up", "-d"), ("restart",)])

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


if __name__ == "__main__":
    unittest.main()
