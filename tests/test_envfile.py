import os
import stat
import tempfile
import unittest
from pathlib import Path

from installer import envfile

EXAMPLE = """\
# Gateway auth token.
TOKEN=
API_KEY=
STACKS_DIR=
TZ=UTC
"""


def render(existing=None, *, set_values=None, generate=(), ask=(), prompt=None):
    return envfile.render(
        EXAMPLE,
        existing or {},
        set_values=set_values or {},
        generate=generate,
        ask=ask,
        prompt=prompt,
    )


class ParseTest(unittest.TestCase):
    def test_reads_pairs_and_skips_comments_and_blanks(self):
        text = "# c\n\nA=1\nB=\nC=x=y\n  D = spaced \n"
        self.assertEqual(envfile.parse(text), {"A": "1", "B": "", "C": "x=y", "D": "spaced"})


class RenderTest(unittest.TestCase):
    def test_keeps_comments_and_example_defaults(self):
        out = render()
        self.assertIn("# Gateway auth token.\n", out)
        self.assertIn("TZ=UTC\n", out)

    def test_set_values_always_win(self):
        out = render({"STACKS_DIR": "/old"}, set_values={"STACKS_DIR": "/new"})
        self.assertIn("STACKS_DIR=/new\n", out)

    def test_existing_values_are_preserved(self):
        out = render({"TZ": "Europe/Kyiv", "API_KEY": ""}, ask=["API_KEY"], prompt=self.fail)
        self.assertIn("TZ=Europe/Kyiv\n", out)
        self.assertIn("API_KEY=\n", out)

    def test_generates_secret_when_missing_or_empty(self):
        for existing in ({}, {"TOKEN": ""}):
            token = envfile.parse(render(existing, generate=["TOKEN"]))["TOKEN"]
            self.assertRegex(token, r"^[0-9a-f]{64}$")

    def test_does_not_regenerate_existing_secret(self):
        out = render({"TOKEN": "keep-me"}, generate=["TOKEN"])
        self.assertIn("TOKEN=keep-me\n", out)

    def test_asks_only_for_keys_not_yet_in_env(self):
        asked = []
        out = render(ask=["API_KEY"], prompt=lambda key: asked.append(key) or "sk-1")
        self.assertEqual(asked, ["API_KEY"])
        self.assertIn("API_KEY=sk-1\n", out)

    def test_blank_answer_falls_back_to_default(self):
        out = render(ask=["TZ"], prompt=lambda key: "")
        self.assertIn("TZ=UTC\n", out)

    def test_no_prompt_means_default(self):
        out = render(ask=["API_KEY"], prompt=None)
        self.assertIn("API_KEY=\n", out)

    def test_keeps_keys_added_outside_example(self):
        out = render({"EXTRA": "1"})
        self.assertTrue(out.endswith("EXTRA=1\n"))


class WriteTest(unittest.TestCase):
    def test_writes_owner_only_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("old")
            os.chmod(path, 0o644)
            envfile.write(path, "A=1\n")
            self.assertEqual(path.read_text(), "A=1\n")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
