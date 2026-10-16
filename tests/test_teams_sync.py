import json
import unittest

from tests.teamfixtures import EXAMPLE, StackCase, run_git
from teamlib import commands, gateway

KEY = "sk-ant-" + "b" * 30


class SyncCase(StackCase):
    """The example team added from a local folder, plus a person's clone of the same remote."""

    def setUp(self):
        super().setUp()
        self.assertEqual(commands.add(self.ctx, "tech", str(EXAMPLE)), [])
        self.clone = self.paths.clone("tech")
        self.person = self.root / "person"
        run_git(self.root, "clone", "-q", str(self.paths.remote("tech")), "person")
        run_git(self.person, "config", "user.name", "Person")
        run_git(self.person, "config", "user.email", "person@example.com")

    def person_push(self, files, message="person edit"):
        run_git(self.person, "pull", "-q", "--rebase", "origin", "main")
        for rel, text in files.items():
            path = self.person / rel
            if text is None:
                run_git(self.person, "rm", "-q", "-r", rel)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        run_git(self.person, "add", "-A")
        run_git(self.person, "commit", "-q", "-m", message)
        run_git(self.person, "push", "-q", "origin", "HEAD:main")

    def vm_write(self, rel, text):
        path = self.clone / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def upstream(self, *args):
        return run_git(self.paths.remote("tech"), *args).stdout

    def sync(self):
        self.out.truncate(0)
        self.out.seek(0)
        return commands.sync(self.ctx, "tech")


class SyncTest(SyncCase):
    def test_agent_memory_reaches_git(self):
        self.vm_write("workspaces/coordinator/memory/2026-10-16.md", "learned something\n")
        self.assertTrue(self.sync(), self.output())
        self.assertRegex(self.upstream("log", "-1", "--format=%s", "main"), r"^sync: test-vm \d{4}-\d{2}-\d{2} \d{2}:\d{2}\n$")
        self.assertEqual(self.upstream("log", "-1", "--format=%an", "main").strip(), "OpenClaw test-vm")
        self.assertIn("pushed 1 commit(s)", self.output())

    def test_persona_change_from_git_reaches_the_vm(self):
        self.person_push({"workspaces/reviewer/SOUL.md": "# Soul\n\nStricter.\n"})
        self.assertTrue(self.sync(), self.output())
        self.assertEqual((self.clone / "workspaces/reviewer/SOUL.md").read_text(), "# Soul\n\nStricter.\n")
        self.assertIn("pulled 1 commit(s)", self.output())

    def test_memory_edited_on_both_sides_merges(self):
        self.person_push({"workspaces/coordinator/MEMORY.md": "# Memory\n\n- from person\n"})
        self.vm_write("workspaces/coordinator/MEMORY.md", "# Memory\n\n- from agent\n")
        self.assertTrue(self.sync(), self.output())
        merged = (self.clone / "workspaces/coordinator/MEMORY.md").read_text()
        self.assertIn("- from person", merged)
        self.assertIn("- from agent", merged)
        self.assertNotIn("rescue", self.output())

    def test_persona_conflict_saves_vm_version_and_git_wins(self):
        self.person_push({"workspaces/reviewer/SOUL.md": "# Soul\n\nPerson version.\n"})
        self.vm_write("workspaces/reviewer/SOUL.md", "# Soul\n\nVM version.\n")
        self.vm_write("workspaces/reviewer/memory/note.md", "kept\n")
        self.assertTrue(self.sync(), self.output())
        self.assertEqual((self.clone / "workspaces/reviewer/SOUL.md").read_text(), "# Soul\n\nPerson version.\n")
        self.assertEqual((self.clone / "workspaces/reviewer/memory/note.md").read_text(), "kept\n")
        rescue = [b.strip() for b in self.upstream("branch", "--list", "rescue/*").splitlines()]
        self.assertEqual(len(rescue), 1, self.output())
        self.assertRegex(rescue[0], r"^rescue/test-vm/\d{8}-\d{6}$")
        self.assertIn("VM version", self.upstream("show", f"{rescue[0]}:workspaces/reviewer/SOUL.md"))
        self.assertIn("git wins conflicting lines", self.output())

    def test_file_deleted_in_git_and_edited_on_vm(self):
        self.person_push({"workspaces/reviewer/USER.md": None})
        self.vm_write("workspaces/reviewer/USER.md", "edited on the vm\n")
        self.assertTrue(self.sync(), self.output())
        self.assertFalse((self.clone / "workspaces/reviewer/USER.md").exists())
        self.assertIn("upstream kept for workspaces/reviewer/USER.md", self.output())

    def test_settings_edited_on_the_vm_are_rescued_and_reset(self):
        original = (self.clone / "team.json5").read_text()
        self.vm_write("team.json5", '{"description": "edited on vm"}\n')
        self.vm_write("notes.txt", "stray file\n")
        self.assertTrue(self.sync(), self.output())
        self.assertEqual((self.clone / "team.json5").read_text(), original)
        self.assertFalse((self.clone / "notes.txt").exists())
        branch = next(b.strip() for b in self.upstream("branch", "--list", "rescue/*").splitlines())
        self.assertTrue(branch.endswith("-settings"))
        self.assertIn("edited on vm", self.upstream("show", f"{branch}:team.json5"))
        self.assertIn("reset to git: notes.txt, team.json5", self.output())

    def test_secret_files_stay_local(self):
        self.ctx.secrets = ["gateway-token-123"]
        self.vm_write("workspaces/coordinator/memory/leak.md", f"key {KEY}\n")
        self.vm_write("workspaces/coordinator/memory/env.md", "token gateway-token-123\n")
        self.vm_write("workspaces/coordinator/memory/fine.md", "fine\n")
        self.assertTrue(self.sync(), self.output())
        files = self.upstream("ls-tree", "-r", "--name-only", "main")
        self.assertIn("workspaces/coordinator/memory/fine.md", files)
        self.assertNotIn("leak.md", files)
        self.assertNotIn("env.md", files)
        self.assertIn("not committed: workspaces/coordinator/memory/leak.md (looks like it contains a key or token)", self.output())
        self.assertTrue((self.clone / "workspaces/coordinator/memory/leak.md").exists())

    def test_skipped_file_changed_in_git_too(self):
        self.vm_write("workspaces/reviewer/USER.md", f"my key {KEY}\n")
        self.person_push({"workspaces/reviewer/USER.md": "# The people\n\nUpdated by a person.\n"})
        self.assertTrue(self.sync(), self.output())
        self.assertEqual((self.clone / "workspaces/reviewer/USER.md").read_text(), "# The people\n\nUpdated by a person.\n")
        self.assertIn("kept in `git stash` in data/teams/tech", self.output())
        self.assertIn(KEY, run_git(self.clone, "stash", "show", "-p").stdout)
        self.assertTrue(self.sync(), self.output())

    def test_nested_git_folder_is_skipped(self):
        run_git(self.clone / "workspaces/reviewer", "init", "-q")
        self.vm_write("workspaces/reviewer/memory/x.md", "x\n")
        self.vm_write("workspaces/coordinator/memory/y.md", "y\n")
        self.assertTrue(self.sync(), self.output())
        files = self.upstream("ls-tree", "-r", "--name-only", "main")
        self.assertIn("workspaces/coordinator/memory/y.md", files)
        self.assertNotIn("workspaces/reviewer/memory/x.md", files)
        self.assertIn("workspaces/reviewer holds its own .git", self.output())

    def test_broken_settings_in_git_do_not_go_live(self):
        self.vm_write("workspaces/coordinator/memory/today.md", "local\n")
        self.person_push({"team.json5": '{"workspace": "/elsewhere"}\n', "workspaces/reviewer/SOUL.md": "# new\n"})
        head = run_git(self.clone, "rev-parse", "HEAD").stdout
        self.assertFalse(self.sync())
        self.assertIn("must not set workspace", self.output())
        self.assertEqual((self.clone / "workspaces/reviewer/SOUL.md").read_text().splitlines()[0], "# Soul")
        self.assertNotEqual(run_git(self.clone, "rev-parse", "HEAD").stdout, head)
        self.assertEqual(run_git(self.clone, "rev-list", "--count", "origin/main..HEAD").stdout.strip(), "1")

    def test_settings_openclaw_rejects_do_not_go_live(self):
        self.person_push({"team.json5": '{"model": 5}\n'})
        self.runner.validation = {"valid": False, "issues": [{"path": "agents.entries.tech-coordinator.model", "message": "Invalid input"}]}
        self.assertFalse(self.sync())
        self.assertIn("agents.entries.tech-coordinator.model: Invalid input", self.output())
        self.assertNotIn("model", json.loads((self.paths.agents_d / "tech-coordinator.json5").read_text()))

    def test_agent_removed_in_git_leaves_the_gateway(self):
        self.vm_write("workspaces/reviewer/.openclaw/state.json", "{}\n")
        self.person_push({
            "workspaces/reviewer": None,
            "agents/reviewer.json5": None,
            "team.json5": '{"description": "Tech team agent"}\n',
        })
        self.assertTrue(self.sync(), self.output())
        self.assertTrue((self.clone / "workspaces/reviewer/.openclaw/state.json").exists())
        self.assertNotIn("tech-reviewer", self.roster())
        self.assertFalse((self.paths.agents_d / "tech-reviewer.json5").exists())
        self.assertIn("removed tech-reviewer", self.output())

    def test_agent_added_in_git_joins_the_gateway(self):
        self.person_push({"workspaces/writer/SOUL.md": "# Soul\n", "workspaces/writer/AGENTS.md": "# Notes\n"})
        self.assertTrue(self.sync(), self.output())
        self.assertEqual(self.roster()["tech-writer"], gateway.include("tech-writer"))

    def test_rejected_push_is_retried(self):
        self.vm_write("workspaces/coordinator/memory/a.md", "vm\n")
        fired = []

        def race(cwd, args):
            if args[:1] == ("push",) and args[-1] == "HEAD:refs/heads/main" and not fired:
                fired.append(True)
                self.person_push({"workspaces/reviewer/memory/b.md": "person\n"}, "race")

        self.runner.before_git = race
        self.assertTrue(self.sync(), self.output())
        files = self.upstream("ls-tree", "-r", "--name-only", "main")
        self.assertIn("workspaces/coordinator/memory/a.md", files)
        self.assertIn("workspaces/reviewer/memory/b.md", files)

    def test_offline_keeps_the_local_commit(self):
        self.vm_write("workspaces/coordinator/memory/a.md", "vm\n")
        self.paths.remote("tech").rename(self.root / "moved.git")
        self.assertFalse(self.sync())
        self.assertIn("fetch failed", self.output())
        self.assertIn("sync: test-vm", run_git(self.clone, "log", "-1", "--format=%s").stdout)

    def test_sync_all_continues_after_a_failing_team(self):
        commands.add(self.ctx, "fin", str(EXAMPLE))
        self.paths.remote("tech").rename(self.root / "moved.git")
        self.assertFalse(commands.sync(self.ctx))
        self.assertIn("==> team fin", self.output())
        self.assertIn("error: fetch failed", self.output())


if __name__ == "__main__":
    unittest.main()
