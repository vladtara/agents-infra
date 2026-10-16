import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.teamfixtures import done
from teamlib import TeamError, runner
from teamlib.context import Paths
from teamlib.runner import DockerRunner


class DockerRunnerTest(unittest.TestCase):
    def setUp(self):
        self.paths = Paths(Path("/srv/stacks/openclaw"))
        self.runner = DockerRunner(self.paths)
        self.run = mock.patch.object(runner.subprocess, "run", return_value=done("{}")).start()
        self.addCleanup(mock.patch.stopall)

    def cmd(self):
        return self.run.call_args.args[0]

    def test_git_runs_in_team_git_with_the_team_key(self):
        self.runner.git(self.paths.clone("tech"), "fetch", "origin", "main", team="tech", git_dir=self.paths.git_dir("tech"))
        self.assertEqual(self.cmd(), [
            "docker", "compose", "run", "--rm", "-T",
            "-e", "GIT_TERMINAL_PROMPT=0", "-e", "GIT_EDITOR=true",
            "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=core.hooksPath", "-e", "GIT_CONFIG_VALUE_0=/dev/null",
            "-e", "GIT_SSH_COMMAND=" + runner.ssh_command("tech"),
            "-e", "GIT_DIR=/gitdirs/tech", "-e", "GIT_WORK_TREE=/teams/tech",
            "-w", "/teams/tech", "team-git", "fetch", "origin", "main",
        ])
        self.assertEqual(self.run.call_args.kwargs["cwd"], self.paths.stack)
        self.assertIn("-i /keys/tech -o IdentitiesOnly=yes", runner.ssh_command("tech"))
        self.assertIn("UserKnownHostsFile=/keys/known_hosts", runner.ssh_command("tech"))

    def test_path_mapping(self):
        self.assertEqual(self.runner.git_path(self.paths.remote("tech")), "/remotes/tech.git")
        self.assertEqual(self.runner.git_path(self.paths.teams), "/teams")
        self.assertEqual(self.runner.git_path(self.paths.git_dir("tech")), "/gitdirs/tech")
        with self.assertRaisesRegex(TeamError, "outside the folders"):
            self.runner.git_path(Path("/etc"))

    def test_failed_command_raises_with_stderr(self):
        self.run.return_value = done(stderr="fatal: boom", code=128)
        with self.assertRaisesRegex(TeamError, "`git status` failed: fatal: boom"):
            self.runner.git(self.paths.clone("tech"), "status")
        self.assertEqual(self.runner.git(self.paths.clone("tech"), "status", check=False).returncode, 128)

    def test_keygen(self):
        self.runner.keygen("tech", "openclaw-vm-tech")
        self.assertEqual(self.cmd()[-12:], [
            "--entrypoint", "ssh-keygen", "team-git", "-q", "-t", "ed25519", "-N", "", "-C", "openclaw-vm-tech", "-f", "/keys/tech",
        ])

    def test_copy_in_streams_a_tar_without_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            (src / ".git").mkdir()
            (src / ".git" / "HEAD").write_text("x")
            (src / "team.json5").write_text("{}")
            self.runner.copy_in(src, self.paths.clone("tech"))
        self.assertEqual(self.cmd()[-6:], ["--entrypoint", "tar", "team-git", "-x", "-C", "/teams/tech"])
        with tarfile.open(fileobj=io.BytesIO(self.run.call_args.kwargs["input"])) as tar:
            names = tar.getnames()
        self.assertIn("./team.json5", names)
        self.assertFalse(any(".git" in Path(n).parts for n in names))

    def test_remove_tree_refuses_mount_roots(self):
        with self.assertRaisesRegex(TeamError, "refusing"):
            self.runner.remove_tree(self.paths.teams)
        with self.assertRaisesRegex(TeamError, "refusing"):
            self.runner.remove_tree(self.paths.gitdirs)
        self.runner.remove_tree(self.paths.clone("tech"))
        self.assertEqual(self.cmd()[-4:], ["team-git", "-rf", "--", "/teams/tech"])

    def test_files_go_through_the_cli_container(self):
        self.run.return_value = done(json.dumps({"parsed": {}, "errors": {}, "files": {"/home/node/.openclaw/openclaw.json": "{}"}}))
        found = self.runner.read_files([self.paths.openclaw_json])
        self.assertEqual(found, {self.paths.openclaw_json: "{}"})
        self.assertEqual(self.cmd()[5:9], ["--entrypoint", "node", "openclaw-cli", "-e"])
        self.assertEqual(json.loads(self.run.call_args.kwargs["input"]), {"read": ["/home/node/.openclaw/openclaw.json"]})

    def test_write_files_maps_paths_and_reports_conflicts(self):
        self.run.return_value = done(json.dumps({"conflict": "/home/node/.openclaw/openclaw.json"}))
        agent = self.paths.agents_d / "tech-a.json5"
        with self.assertRaisesRegex(TeamError, "changed while teams.py was applying"):
            self.runner.write_files([(agent, "{}\n"), (self.paths.openclaw_json, None)], expect={self.paths.openclaw_json: "abc"})
        self.assertEqual(json.loads(self.run.call_args.kwargs["input"]), {
            "expect": {"/home/node/.openclaw/openclaw.json": "abc"},
            "ops": [
                {"path": "/home/node/.openclaw/agents.d/tech-a.json5", "text": "{}\n"},
                {"path": "/home/node/.openclaw/openclaw.json", "text": None},
            ],
        })

    def test_validate_uses_a_copy_next_to_the_config(self):
        self.run.return_value = done('[plugins] loading\n{\n  "valid": false,\n  "issues": []\n}\n', code=1)
        self.assertEqual(self.runner.validate('{"a": 1}'), {"valid": False, "issues": []})
        cmd = self.cmd()
        self.assertIn("OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.check.json", cmd)
        self.assertEqual(cmd[-3:-1], ["openclaw-cli", "-c"])
        self.assertEqual(self.run.call_args.kwargs["input"], '{"a": 1}')

    def test_json_out_rejects_non_json(self):
        with self.assertRaisesRegex(TeamError, "expected JSON"):
            runner.json_out("Error: gateway not running")


if __name__ == "__main__":
    unittest.main()
