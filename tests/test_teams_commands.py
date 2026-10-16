import io
import shutil
import unittest
from pathlib import Path
from unittest import mock

from tests.teamfixtures import EXAMPLE, REPO, StackCase, run_git
from teamlib import TeamError, access, commands, registry

import teams


class AddTest(StackCase):
    def test_add_local_folder(self):
        self.assertEqual(commands.add(self.ctx, "tech", str(EXAMPLE)), [])
        self.assertEqual(registry.load(self.paths.registry).teams["tech"].url, "examples/teams/tech")
        clone = self.paths.clone("tech")
        self.assertEqual(run_git(clone, "config", "pull.rebase").stdout.strip(), "true")
        self.assertEqual(run_git(clone, "config", "rebase.autoStash").stdout.strip(), "true")
        self.assertEqual(run_git(clone, "config", "user.email").stdout.strip(), "openclaw@test-vm")
        self.assertEqual(run_git(clone, "remote", "get-url", "origin").stdout.strip(), str(self.paths.remote("tech")))
        self.assertIn("tech-coordinator", self.roster())

    def test_contract_violation_leaves_nothing_behind(self):
        broken = self.root / "broken"
        shutil.copytree(EXAMPLE, broken)
        (broken / "workspaces" / "reviewer" / "SOUL.md").unlink()
        with self.assertRaisesRegex(TeamError, "workspaces/reviewer: missing SOUL.md"):
            commands.add(self.ctx, "tech", str(broken))
        self.assertFalse(self.paths.clone("tech").exists())
        self.assertFalse(self.paths.remote("tech").exists())
        self.assertFalse(self.paths.registry.exists())
        self.assertEqual(self.config(), {"gateway": {"mode": "local"}})

    def test_rejects_bad_names_and_sources(self):
        with self.assertRaisesRegex(TeamError, "must match"):
            commands.add(self.ctx, "Tech", str(EXAMPLE))
        with self.assertRaisesRegex(TeamError, "no such folder"):
            commands.add(self.ctx, "tech", str(self.root / "nope"))
        with self.assertRaisesRegex(TeamError, "use an SSH URL"):
            commands.add(self.ctx, "tech", "https://github.com/org/repo.git")

    def test_plugin_problems_do_not_undo_the_add(self):
        team = self.root / "plugged"
        shutil.copytree(EXAMPLE, team)
        (team / "plugins.json").write_text('{"plugins": ["risky"]}\n')
        self.runner.refuse = {"risky"}
        problems = commands.add(self.ctx, "tech", str(team))
        self.assertEqual(len(problems), 1)
        self.assertIn("tech", registry.load(self.paths.registry).teams)


class RemoveTest(StackCase):
    def test_refuses_unpushed_changes_then_removes(self):
        commands.add(self.ctx, "tech", str(EXAMPLE))
        (self.paths.clone("tech") / "workspaces/coordinator/memory/x.md").write_text("x\n")
        with self.assertRaisesRegex(TeamError, "not in git: workspaces/coordinator/memory/x.md"):
            commands.remove(self.ctx, "tech")
        self.assertTrue(commands.sync(self.ctx, "tech"))
        commands.remove(self.ctx, "tech")
        self.assertFalse(self.paths.clone("tech").exists())
        self.assertEqual(list(self.roster()), ["main"])
        self.assertEqual(registry.load(self.paths.registry).teams, {})
        self.assertIn("team tech removed: tech-coordinator, tech-reviewer", self.output())
        self.assertIn("kept: sessions in data/config/agents/<id>/, data/team-remotes/tech.git", self.output())

    def test_unknown_team(self):
        with self.assertRaisesRegex(TeamError, "unknown team nope"):
            commands.remove(self.ctx, "nope")


class ListTest(StackCase):
    def test_lists_agents_git_state_and_unlisted_plugins(self):
        commands.add(self.ctx, "tech", str(EXAMPLE))
        (self.paths.clone("tech") / "workspaces/coordinator/memory/x.md").write_text("x\n")
        self.runner.installed = [{"id": "stray", "origin": "global"}]
        commands.list_teams(self.ctx)
        out = self.output()
        self.assertIn("tech  examples/teams/tech (main)", out)
        self.assertIn("agents: tech-coordinator, tech-reviewer", out)
        self.assertIn("git: 0 to push, 0 to pull (as of the last fetch), 1 changed file(s)", out)
        self.assertIn("plugins no team lists: stray", out)

    def test_no_teams(self):
        commands.list_teams(self.ctx)
        self.assertIn("no teams yet", self.output())


class ApplyTest(StackCase):
    def test_new_vm_recreates_local_teams(self):
        self.ctx.registry.teams["tech"] = registry.Team("tech", "examples/teams/tech")
        commands.apply(self.ctx)
        self.assertTrue(self.paths.remote("tech").is_dir())
        self.assertIn("tech-reviewer", self.roster())
        self.assertNotIn("action:", self.output())
        commands.apply(self.ctx)
        self.assertNotIn("action:", self.output())

    def test_missing_access_becomes_an_action(self):
        self.ctx.registry.teams["tech"] = registry.Team("tech", "git@github.com:org/tech.git")
        with mock.patch.object(access, "ensure_access", side_effect=access.NeedsKey("git@github.com:org/tech.git refuses access.\nAdd key")):
            commands.apply(self.ctx)
        self.assertIn("action: team tech: git@github.com:org/tech.git refuses access. Add key\n", self.output())


class MainTest(StackCase):
    def main(self, *argv):
        out = io.StringIO()
        code = teams.main(list(argv), runner=self.runner, stack=self.paths.stack, repo=REPO, out=out)
        return code, out.getvalue()

    def test_add_sync_list_remove(self):
        self.assertEqual(self.main("add", "tech", str(EXAMPLE))[0], 0)
        self.assertEqual(self.main("sync")[0], 0)
        code, out = self.main("list")
        self.assertIn("tech-reviewer", out)
        self.assertEqual(self.main("remove", "tech")[0], 0)

    def test_errors_exit_non_zero(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(self.main("sync", "nope")[0], 1)
        self.assertIn("error: unknown team nope", err.getvalue())

    def test_apply_without_teams_does_nothing(self):
        self.assertEqual(self.main("apply"), (0, ""))
        self.assertEqual(self.runner.openclaw_calls, [])


if __name__ == "__main__":
    unittest.main()
