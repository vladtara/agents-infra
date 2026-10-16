import os
import tempfile
import unittest
from pathlib import Path

from tests import teamfixtures  # noqa: F401
from teamlib import scan

KEY = "sk-ant-" + "a" * 30


class ScanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def file(self, name, content):
        path = self.dir / name
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return path

    def test_load_secrets_from_env_and_manifest(self):
        self.file(".env", "OPENCLAW_GATEWAY_TOKEN=abcdef0123\nTS_AUTHKEY=tskey-1234567\nSHORT_KEY=abc\nTZ=Europe/Berlin\nOPENCLAW_TZ=Europe/Berlin\n")
        self.file("component.toml", '[env]\nask = ["TS_AUTHKEY"]\n')
        self.assertEqual(scan.load_secrets(self.dir), ["abcdef0123", "tskey-1234567"])

    def test_no_env_means_no_secrets(self):
        self.assertEqual(scan.load_secrets(self.dir), [])

    def test_problems(self):
        self.assertIsNone(scan.problem(self.file("ok.md", "notes"), ["abcdef0123"]))
        self.assertEqual(scan.problem(self.file("env.md", "token abcdef0123"), ["abcdef0123"]), "contains a value from .env")
        self.assertEqual(scan.problem(self.file("key.md", f"key: {KEY}"), []), "looks like it contains a key or token")
        self.assertEqual(scan.problem(self.file("pem.md", "-----BEGIN OPENSSH PRIVATE KEY-----"), []), "looks like it contains a key or token")
        self.assertEqual(scan.problem(self.file("big.bin", b"\0" * (scan.MAX_BYTES + 1)), []), "larger than 5 MB")

    def test_deleted_files_and_symlinks_are_fine(self):
        self.assertIsNone(scan.problem(self.dir / "gone.md", []))
        os.symlink("/nonexistent", self.dir / "link")
        self.assertIsNone(scan.problem(self.dir / "link", []))


if __name__ == "__main__":
    unittest.main()
