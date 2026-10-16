import unittest
from unittest import mock

from tests.teamfixtures import StackCase, done
from teamlib import TeamError, access
from teamlib.registry import Team

TEAM = Team("tech", "git@github.com:org/openclaw-tech.git")


class UrlTest(unittest.TestCase):
    def test_kinds(self):
        self.assertTrue(access.is_local("examples/teams/tech"))
        self.assertTrue(access.is_local("/srv/teams/tech"))
        self.assertFalse(access.is_local("git@github.com:org/repo.git"))
        self.assertFalse(access.is_local("ssh://git@gitlab.example.com:2222/group/sub/repo.git"))
        self.assertFalse(access.is_local("https://github.com/org/repo.git"))

    def test_slug_and_settings_url(self):
        self.assertEqual(access.repo_slug("git@github.com:org/repo.git"), ("github.com", "org/repo"))
        self.assertEqual(access.repo_slug("ssh://git@gitlab.example.com:2222/group/sub/repo.git"), ("gitlab.example.com", "group/sub/repo"))
        self.assertIsNone(access.repo_slug("https://github.com/org/repo.git"))
        self.assertEqual(access.settings_url("github.com", "org/repo"), "https://github.com/org/repo/settings/keys/new")
        self.assertEqual(access.settings_url("gitlab.example.com", "g/r"), "https://gitlab.example.com/g/r/-/settings/repository#js-deploy-keys-settings")
        self.assertIsNone(access.settings_url("git.example.com", "g/r"))


class EnsureAccessTest(StackCase):
    def setUp(self):
        super().setUp()
        (self.paths.keys / "tech.pub").write_text("ssh-ed25519 AAAAkey openclaw-test-vm-tech\n")
        self.answers = []
        self.runner.git = self.fake_git
        self.which = mock.patch.object(access.shutil, "which", return_value="/usr/bin/gh").start()
        self.run = mock.patch.object(access.subprocess, "run", return_value=done()).start()
        self.addCleanup(mock.patch.stopall)

    def fake_git(self, cwd, *args, team=None, check=True, binary=False, input=None):
        self.assertEqual((args[0], team), ("ls-remote", "tech"))
        return self.answers.pop(0)

    def test_reachable_repo_needs_nothing(self):
        self.answers = [done()]
        access.ensure_access(self.ctx, TEAM, sleep=lambda s: None)
        self.run.assert_not_called()

    def test_adds_key_with_gh_and_retries(self):
        self.answers = [done(stderr="Permission denied (publickey).", code=128), done(stderr="denied", code=128), done()]
        access.ensure_access(self.ctx, TEAM, sleep=lambda s: None)
        add = self.run.call_args_list[-1].args[0]
        self.assertEqual(add, [
            "gh", "repo", "deploy-key", "add", str(self.paths.keys / "tech.pub"),
            "--repo", "org/openclaw-tech", "--title", "test-vm", "--allow-write",
        ])
        self.assertIn("added deploy key tech.pub to org/openclaw-tech with gh", self.output())

    def test_without_gh_prints_key_and_settings_url(self):
        self.which.return_value = None
        self.answers = [done(stderr="Permission denied (publickey).", code=128)]
        with self.assertRaises(access.NeedsKey) as caught:
            access.ensure_access(self.ctx, TEAM, sleep=lambda s: None)
        message = str(caught.exception)
        self.assertIn("Permission denied (publickey).", message)
        self.assertIn("https://github.com/org/openclaw-tech/settings/keys/new", message)
        self.assertIn("ssh-ed25519 AAAAkey openclaw-test-vm-tech", message)

    def test_glab_for_gitlab_hosts(self):
        self.which.return_value = "/usr/bin/glab"
        self.answers = [done(code=128), done()]
        access.ensure_access(self.ctx, Team("tech", "git@gitlab.example.com:g/tech.git"), sleep=lambda s: None)
        self.assertEqual(self.run.call_args_list[0].args[0], ["glab", "auth", "status", "--hostname", "gitlab.example.com"])
        self.assertEqual(self.run.call_args_list[-1].args[0][:3], ["glab", "deploy-key", "add"])
        self.assertIn("--can-push", self.run.call_args_list[-1].args[0])

    def test_gives_up_when_the_key_still_fails(self):
        self.answers = [done(code=128)] * 4
        with self.assertRaisesRegex(TeamError, "still refuses the deploy key"):
            access.ensure_access(self.ctx, TEAM, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
