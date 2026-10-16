import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import teamfixtures  # noqa: F401  (puts teamlib on sys.path)
from teamlib import TeamError, registry
from teamlib.context import Paths
from teamlib.registry import Registry, Team


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "teams.toml"

    def test_missing_file_means_no_teams_and_hostname_identity(self):
        with mock.patch.object(registry.socket, "gethostname", return_value="Agents_VM.example.com"):
            reg = registry.load(self.path)
        self.assertEqual((reg.vm, reg.git_name, reg.git_email), ("agents-vm", "OpenClaw agents-vm", "openclaw@agents-vm"))
        self.assertEqual(reg.teams, {})

    def test_round_trip(self):
        reg = Registry(vm="agents-vm", git_name='OpenClaw "vm"', git_email="openclaw@agents-vm")
        reg.teams["tech"] = Team("tech", "git@github.com:org/openclaw-tech.git")
        reg.teams["fin"] = Team("fin", "examples/teams/tech", "trunk")
        registry.save(self.path, reg)
        self.assertEqual(registry.load(self.path), reg)
        self.assertIn("[teams.fin]\nurl = \"examples/teams/tech\"\nbranch = \"trunk\"", self.path.read_text())

    def test_rejects_bad_files(self):
        for text, message in (
            ("[git\n", "teams.toml"),
            ('[git]\nvm = "a b"\n', "git.vm"),
            ('[teams.Tech]\nurl = "x"\n', "team name"),
            ("[teams.tech]\n", "needs a url"),
            ('[teams.tech]\nurl = "x"\nbranch = "-x"\n', "branch"),
        ):
            self.path.write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(TeamError, message):
                registry.load(self.path)

    def test_rejects_overlapping_teams_in_file(self):
        self.path.write_text('[teams.tech]\nurl = "a"\n\n[teams.tech-ops]\nurl = "b"\n')
        with self.assertRaisesRegex(TeamError, "teams 'tech' and 'tech-ops' overlap"):
            registry.load(self.path)

    def test_new_team_names(self):
        registry.check_new_name("fin", ["tech"])
        for name, existing, message in (
            ("Tech", [], "must match"),
            ("-x", [], "must match"),
            ("tech", ["tech"], "already in teams.toml"),
            ("tech-ops", ["tech"], "overlaps team 'tech'"),
            ("tech", ["tech-ops"], "overlaps team 'tech-ops'"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(TeamError, message):
                registry.check_new_name(name, existing)


class PathsTest(unittest.TestCase):
    def test_layout(self):
        paths = Paths(Path("/s"))
        self.assertEqual(paths.clone("tech"), Path("/s/data/teams/tech"))
        self.assertEqual(paths.agents_d, Path("/s/data/config/agents.d"))
        self.assertEqual(paths.openclaw_json, Path("/s/data/config/openclaw.json"))
        self.assertEqual(paths.keys, Path("/s/data/team-keys"))
        self.assertEqual(paths.remote("tech"), Path("/s/data/team-remotes/tech.git"))
        self.assertEqual(paths.registry, Path("/s/teams.toml"))


if __name__ == "__main__":
    unittest.main()
