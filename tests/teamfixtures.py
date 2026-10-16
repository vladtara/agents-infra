"""Shared helpers for the teamlib tests: real git in temp dirs, OpenClaw faked."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "stacks" / "openclaw"))

from teamlib import TeamError  # noqa: E402
from teamlib.context import Context, Paths  # noqa: E402
from teamlib.registry import Registry  # noqa: E402

EXAMPLE = REPO / "examples" / "teams" / "tech"
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_EDITOR": "true",
    "GIT_TERMINAL_PROMPT": "0",
}


def done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


def run_git(cwd, *args, check=True, binary=False, input=None):
    if input is None:
        input = b"" if binary else ""
    result = subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, input=input, capture_output=True, text=not binary)
    if check and result.returncode:
        stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
        raise TeamError(f"git {' '.join(args)} failed: {stderr.strip()}")
    return result


def git_in(cwd):
    return lambda *args, **kwargs: run_git(cwd, *args, **kwargs)


def commit_dir(src: Path, dest: Path) -> Path:
    """Copy src into a new git repo at dest and commit it."""
    shutil.copytree(src, dest)
    run_git(dest, "init", "-q", "-b", "main")
    run_git(dest, "add", "-A")
    run_git(dest, "-c", "user.name=Person", "-c", "user.email=person@example.com", "commit", "-q", "-m", "init")
    return dest


class LocalRunner:
    """The runner interface of teamlib.runner.DockerRunner, without Docker."""

    def __init__(self):
        self.validation = {"valid": True, "warnings": []}
        self.validated = []
        self.installed = []
        self.refuse = set()
        self.openclaw_calls = []
        self.before_git = None

    def git(self, cwd, *args, team=None, check=True, binary=False, input=None):
        if self.before_git:
            self.before_git(cwd, args)
        return run_git(cwd, *args, check=check, binary=binary, input=input)

    def git_path(self, host):
        return str(host)

    def keygen(self, team, comment):
        raise AssertionError("tests use local sources or pre-made keys")

    def copy_in(self, src, dest):
        shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))

    def remove_tree(self, path):
        shutil.rmtree(path, ignore_errors=True)

    def parse_json5(self, texts):
        parsed, errors = {}, {}
        for name, text in texts.items():
            try:
                parsed[name] = json.loads(text)
            except json.JSONDecodeError as exc:
                errors[name] = str(exc)
        return parsed, errors

    def read_files(self, paths):
        return {p: p.read_bytes().decode() if p.is_file() else None for p in paths}

    def write_files(self, ops, expect):
        for path, sha in expect.items():
            current = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            if current != sha:
                raise TeamError(f"{path} changed while teams.py was applying; run the command again")
        for path, text in ops:
            if text is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(text)

    def validate(self, config_text):
        self.validated.append(json.loads(config_text))
        return self.validation

    def openclaw(self, *args):
        self.openclaw_calls.append(args)
        if args[:2] == ("plugins", "list"):
            return done(json.dumps({"plugins": self.installed}))
        if args[:2] == ("plugins", "install"):
            if args[2] in self.refuse:
                return done(stderr="plugin requests capabilities: rerun with --accept-capabilities", code=1)
            self.installed.append({"id": args[2], "packageName": args[2], "origin": "global"})
            return done()
        raise AssertionError(f"unexpected openclaw call {args}")


class StackCase(unittest.TestCase):
    """A temp stacks/openclaw with data dirs, an openclaw.json and a Context on LocalRunner."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.paths = Paths(self.root / "stack")
        for folder in (self.paths.teams, self.paths.agents_d, self.paths.keys, self.paths.remotes):
            folder.mkdir(parents=True)
        self.write_config({"gateway": {"mode": "local"}})
        self.runner = LocalRunner()
        self.out = io.StringIO()
        self.ctx = Context(
            repo=REPO,
            paths=self.paths,
            runner=self.runner,
            registry=Registry(vm="test-vm", git_name="OpenClaw test-vm", git_email="openclaw@test-vm"),
            out=self.out,
        )

    def write_config(self, config):
        self.paths.openclaw_json.write_text(json.dumps(config, indent=2) + "\n")

    def config(self):
        return json.loads(self.paths.openclaw_json.read_text())

    def roster(self):
        return self.config().get("agents", {}).get("entries", {})

    def output(self):
        return self.out.getvalue()
