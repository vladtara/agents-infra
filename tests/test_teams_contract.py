import json
import tempfile
import unittest
from pathlib import Path

from tests.teamfixtures import EXAMPLE, commit_dir, git_in, run_git
from teamlib import TeamError, contract
from teamlib.tree import Tree, from_rev

GOOD = frozenset({"SOUL.md", "AGENTS.md"})


def tree(workspaces=None, texts=None, nested=()):
    workspaces = {"a": GOOD} if workspaces is None else workspaces
    texts = {"team.json5": "{}"} if texts is None else texts
    return Tree(workspaces, frozenset(nested), texts)


def resolve(t, team="tech"):
    parsed, errors = {}, {}
    for name, text in t.texts.items():
        try:
            parsed[name] = json.loads(text)
        except json.JSONDecodeError as exc:
            errors[name] = str(exc)
    return contract.resolve(team, t, parsed, errors)


class MergeTest(unittest.TestCase):
    def test_objects_merge_and_other_values_replace(self):
        base = {"model": {"primary": "a", "fallbacks": ["b"]}, "skills": ["x", "y"], "name": "base"}
        override = {"model": {"primary": "c"}, "skills": ["z"]}
        self.assertEqual(
            contract.merge(base, override),
            {"model": {"primary": "c", "fallbacks": ["b"]}, "skills": ["z"], "name": "base"},
        )
        self.assertEqual(base["model"], {"primary": "a", "fallbacks": ["b"]})


class ResolveTest(unittest.TestCase):
    def test_example_team(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = commit_dir(EXAMPLE, Path(tmp) / "tech")
            resolved = resolve(from_rev(git_in(repo), "HEAD"))
        self.assertEqual(resolved.entries, {
            "tech-coordinator": {
                "description": "Tech team agent",
                "subagents": {"allowAgents": ["tech-reviewer"]},
                "workspace": "/home/node/teams/tech/workspaces/coordinator",
            },
            "tech-reviewer": {
                "description": "Reviews code and designs for the tech team",
                "subagents": {"allowAgents": []},
                "name": "Reviewer",
                "workspace": "/home/node/teams/tech/workspaces/reviewer",
            },
        })
        self.assertEqual((resolved.plugins, resolved.warnings), ([], []))

    def test_allow_agents_prefix_only_team_folders(self):
        t = tree({"a": GOOD, "b": GOOD}, {"team.json5": '{"subagents": {"allowAgents": ["b", "main", "*"]}}'})
        self.assertEqual(resolve(t).entries["tech-a"]["subagents"]["allowAgents"], ["tech-b", "main", "*"])

    def test_plugins(self):
        t = tree(texts={"team.json5": "{}", "plugins.json": '{"plugins": ["@openclaw/voice-call", "foo@1.2.0"]}'})
        self.assertEqual(resolve(t).plugins, ["@openclaw/voice-call", "foo@1.2.0"])

    def test_nested_git_is_a_warning(self):
        self.assertEqual(len(resolve(tree(nested=["a"])).warnings), 1)

    def assert_problem(self, t, message, team="tech"):
        with self.assertRaisesRegex(TeamError, message):
            resolve(t, team)

    def test_problems(self):
        self.assert_problem(tree({}), "no agents")
        self.assert_problem(tree({"Bad_Name": GOOD}), "workspaces/Bad_Name: folder names must match")
        self.assert_problem(tree({"a": frozenset({"SOUL.md"})}), "workspaces/a: missing AGENTS.md")
        self.assert_problem(tree({"a" * 60: GOOD}), "longer than 64")
        self.assert_problem(tree(texts={}), "team.json5 is missing")
        self.assert_problem(tree(texts={"team.json5": "[]"}), "team.json5: must be an object")
        self.assert_problem(tree(texts={"team.json5": "{oops"}), "team.json5: ")
        self.assert_problem(tree(texts={"team.json5": '{"workspace": "/x", "default": true}'}), "must not set workspace, default")
        self.assert_problem(tree(texts={"team.json5": "{}", "agents/ghost.json5": "{}"}), "no matching workspaces/ghost/")
        self.assert_problem(tree(texts={"team.json5": "{}", "plugins.json": '["x"]'}), "plugins.json: expected")
        self.assert_problem(tree(texts={"team.json5": "{}", "plugins.json": '{"plugins": ["--force"]}'}), "plugins.json: expected")

    def test_lists_every_problem_at_once(self):
        with self.assertRaises(TeamError) as caught:
            resolve(tree({"a": frozenset()}, {}))
        self.assertIn("missing SOUL.md, AGENTS.md", str(caught.exception))
        self.assertIn("team.json5 is missing", str(caught.exception))


class FromRevTest(unittest.TestCase):
    def test_reads_committed_state_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = commit_dir(EXAMPLE, Path(tmp) / "tech")
            (repo / "workspaces" / "untracked").mkdir()
            (repo / "workspaces" / "untracked" / "SOUL.md").write_text("x")
            t = from_rev(git_in(repo), "HEAD")
        self.assertEqual(sorted(t.workspaces), ["coordinator", "reviewer"])
        self.assertIn("memory", t.workspaces["coordinator"])
        self.assertEqual(sorted(t.texts), ["agents/reviewer.json5", "plugins.json", "team.json5"])

    def test_committed_nested_repo_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = commit_dir(EXAMPLE, Path(tmp) / "tech")
            inner = repo / "workspaces" / "inner"
            inner.mkdir()
            run_git(inner, "init", "-q")
            (inner / "SOUL.md").write_text("x")
            run_git(inner, "add", "-A")
            run_git(inner, "-c", "user.name=P", "-c", "user.email=p@x", "commit", "-q", "-m", "x")
            run_git(repo, "add", "workspaces/inner")
            run_git(repo, "-c", "user.name=P", "-c", "user.email=p@x", "commit", "-q", "-m", "nested")
            t = from_rev(git_in(repo), "HEAD")
        self.assertEqual(t.nested_git, frozenset({"inner"}))


if __name__ == "__main__":
    unittest.main()
