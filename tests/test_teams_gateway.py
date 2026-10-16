import json
import unittest
from pathlib import Path

from tests.teamfixtures import EXAMPLE, StackCase, commit_dir, git_in
from teamlib import TeamError, gateway
from teamlib.tree import Tree, from_rev

INC = gateway.include


class PlanRosterTest(unittest.TestCase):
    ENTRIES = {"tech-a": {"workspace": "/home/node/teams/tech/workspaces/a"}}

    def test_first_team_lists_implicit_main_and_sets_ownership(self):
        new = gateway.plan_roster({"gateway": {}}, "tech", self.ENTRIES)
        self.assertEqual(new["agents"], {
            "entries": {"main": {"workspace": "/home/node/.openclaw/workspace"}, "tech-a": INC("tech-a")},
            "ownership": "explicit",
            "defaults": {"systemAgent": {"agentId": "main"}},
        })

    def test_sole_agent_keeps_the_default_workspace(self):
        # The gateway's startup doctor writes main: {} before teams.py runs.
        new = gateway.plan_roster({"agents": {"entries": {"main": {"model": "m"}}}}, "tech", self.ENTRIES)
        self.assertEqual(new["agents"]["entries"]["main"], {"model": "m", "workspace": "/home/node/.openclaw/workspace"})
        custom = {"agents": {"entries": {"main": {}}, "defaults": {"workspace": "/data/ws"}}}
        self.assertEqual(gateway.plan_roster(custom, "tech", self.ENTRIES)["agents"]["entries"]["main"], {"workspace": "/data/ws"})

    def test_no_pinning_once_ownership_is_explicit(self):
        config = {"agents": {"ownership": "explicit", "entries": {"main": {}, "ops": {}}}}
        new = gateway.plan_roster(config, "tech", self.ENTRIES)
        self.assertEqual((new["agents"]["entries"]["main"], new["agents"]["entries"]["ops"]), ({}, {}))

    def test_inline_entries_for_validation(self):
        new = gateway.plan_roster({}, "tech", self.ENTRIES, inline=True)
        self.assertEqual(new["agents"]["entries"]["tech-a"], self.ENTRIES["tech-a"])

    def test_keeps_an_existing_roster_and_system_agent(self):
        config = {"agents": {"entries": {"ops": {"model": "x"}}, "defaults": {"systemAgent": {"agentId": "ops"}, "model": "y"}}}
        new = gateway.plan_roster(config, "tech", self.ENTRIES)
        self.assertEqual(list(new["agents"]["entries"]), ["ops", "tech-a"])
        self.assertEqual(new["agents"]["entries"]["ops"]["workspace"], "/home/node/.openclaw/workspace")
        self.assertEqual(new["agents"]["defaults"], {"systemAgent": {"agentId": "ops"}, "model": "y"})
        self.assertEqual(config["agents"]["entries"], {"ops": {"model": "x"}})

    def test_legacy_default_marker_keeps_ownership_unset(self):
        config = {"agents": {"entries": {"main": {"default": True}}}}
        self.assertNotIn("ownership", gateway.plan_roster(config, "tech", self.ENTRIES)["agents"])

    def test_refuses_ids_owned_by_someone_else(self):
        config = {"agents": {"entries": {"tech-a": {"model": "mine"}}}}
        with self.assertRaisesRegex(TeamError, "already used outside team tech: tech-a"):
            gateway.plan_roster(config, "tech", self.ENTRIES)

    def test_drops_agents_gone_from_the_team_and_leaves_others(self):
        config = {"agents": {"ownership": "explicit", "entries": {"main": {}, "tech-old": INC("tech-old"), "fin-a": INC("fin-a"), "tech-x": {"model": "m"}}}}
        new = gateway.plan_roster(config, "tech", self.ENTRIES)
        self.assertEqual(list(new["agents"]["entries"]), ["main", "fin-a", "tech-x", "tech-a"])
        removed = gateway.plan_roster(new, "tech", None)
        self.assertEqual(list(removed["agents"]["entries"]), ["main", "fin-a", "tech-x"])


class ApplyTest(StackCase):
    def setUp(self):
        super().setUp()
        repo = commit_dir(EXAMPLE, self.root / "example")
        self.tree = from_rev(git_in(repo), "HEAD")

    def test_writes_entries_and_includes(self):
        self.assertEqual(gateway.apply_team(self.ctx, "tech", self.tree), [])
        self.assertEqual(self.roster()["tech-coordinator"], INC("tech-coordinator"))
        written = json.loads((self.paths.agents_d / "tech-reviewer.json5").read_text())
        self.assertEqual(written["workspace"], "/home/node/teams/tech/workspaces/reviewer")
        self.assertEqual(self.config()["gateway"], {"mode": "local"})
        self.assertIn("agents: tech-coordinator, tech-reviewer (added tech-coordinator, tech-reviewer)", self.output())
        candidate, after = self.runner.validated
        self.assertEqual(candidate["agents"]["entries"]["tech-reviewer"]["name"], "Reviewer")
        self.assertEqual(after["agents"]["entries"]["tech-reviewer"], INC("tech-reviewer"))

    def test_second_apply_writes_nothing(self):
        gateway.apply_team(self.ctx, "tech", self.tree)
        before = self.paths.openclaw_json.stat().st_mtime_ns
        self.runner.validated.clear()
        gateway.apply_team(self.ctx, "tech", self.tree)
        self.assertEqual(self.paths.openclaw_json.stat().st_mtime_ns, before)
        self.assertEqual(self.runner.validated, [])

    def test_control_ui_edit_is_overwritten(self):
        gateway.apply_team(self.ctx, "tech", self.tree)
        (self.paths.agents_d / "tech-reviewer.json5").write_text('{"name": "edited in the UI"}\n')
        gateway.apply_team(self.ctx, "tech", self.tree)
        self.assertEqual(json.loads((self.paths.agents_d / "tech-reviewer.json5").read_text())["name"], "Reviewer")

    def test_rejected_settings_write_nothing(self):
        self.runner.validation = {"valid": False, "issues": [{"path": "agents.entries.tech-reviewer.name", "message": "Expected string"}]}
        with self.assertRaisesRegex(TeamError, "agents.entries.tech-reviewer.name: Expected string"):
            gateway.apply_team(self.ctx, "tech", self.tree)
        self.assertEqual(self.config(), {"gateway": {"mode": "local"}})
        self.assertEqual(list(self.paths.agents_d.iterdir()), [])

    def test_missing_openclaw_json(self):
        self.paths.openclaw_json.unlink()
        with self.assertRaisesRegex(TeamError, "install OpenClaw first"):
            gateway.apply_team(self.ctx, "tech", self.tree)

    def test_config_changed_meanwhile(self):
        original = self.runner.read_files

        def racing_read(paths):
            found = original(paths)
            self.write_config({"gateway": {"mode": "local"}, "edited": True})
            return found

        self.runner.read_files = racing_read
        with self.assertRaisesRegex(TeamError, "changed while teams.py was applying"):
            gateway.apply_team(self.ctx, "tech", self.tree)
        self.assertTrue(self.config()["edited"])

    def race_on(self, agent, text):
        original = self.runner.read_files

        def racing_read(paths):
            found = original(paths)
            if agent in paths:
                agent.write_text(text)
            return found

        self.runner.read_files = racing_read

    def test_agent_file_changed_meanwhile(self):
        gateway.apply_team(self.ctx, "tech", self.tree)
        agent = self.paths.agents_d / "tech-reviewer.json5"
        agent.write_text('{"name": "first UI save"}\n')
        self.race_on(agent, '{"name": "second UI save"}\n')
        with self.assertRaisesRegex(TeamError, "changed while teams.py was applying"):
            gateway.apply_team(self.ctx, "tech", self.tree)
        self.assertEqual(json.loads(agent.read_text())["name"], "second UI save")

    def test_removed_agent_file_changed_meanwhile(self):
        gateway.apply_team(self.ctx, "tech", self.tree)
        agent = self.paths.agents_d / "tech-reviewer.json5"
        smaller = Tree({"coordinator": self.tree.workspaces["coordinator"]}, frozenset(), {"team.json5": "{}"})
        self.race_on(agent, '{"name": "UI save"}\n')
        with self.assertRaisesRegex(TeamError, "changed while teams.py was applying"):
            gateway.apply_team(self.ctx, "tech", smaller)
        self.assertTrue(agent.exists())

    def test_remove_team(self):
        gateway.apply_team(self.ctx, "tech", self.tree)
        (self.paths.agents_d / "tech-reviewer.json5.bak").write_text("{}")
        (self.paths.agents_d / "tech-reviewer.json5.bak.1").write_text("{}")
        self.assertEqual(gateway.remove_team(self.ctx, "tech"), ["tech-coordinator", "tech-reviewer"])
        self.assertEqual(list(self.roster()), ["main"])
        self.assertEqual(list(self.paths.agents_d.iterdir()), [])


class PluginTest(StackCase):
    def test_installs_missing_and_reports_refused(self):
        self.runner.installed = [{"id": "voice-call", "packageName": "@openclaw/voice-call", "origin": "global"}]
        self.runner.refuse = {"risky"}
        problems = gateway.install_plugins(self.ctx, ["@openclaw/voice-call@1.0.0", "calendar", "risky"])
        self.assertEqual(self.runner.openclaw_calls[1:], [("plugins", "install", "calendar"), ("plugins", "install", "risky")])
        self.assertEqual(len(problems), 1)
        self.assertIn("plugin risky was not installed (plugin requests capabilities", problems[0])
        self.assertIn("rune openclaw::cli plugins install risky", problems[0])

    def test_unlisted_plugins(self):
        installed = [
            {"id": "a", "packageName": "pkg-a", "origin": "global"},
            {"id": "b", "packageName": "pkg-b", "origin": "global"},
            {"id": "c", "origin": "bundled"},
        ]
        self.assertEqual(gateway.unlisted_plugins(installed, ["pkg-a@2"]), ["b"])


if __name__ == "__main__":
    unittest.main()
