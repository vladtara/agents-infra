"""Run git and OpenClaw in one-off containers of the openclaw stack.

git runs in team-git (uid 1000, deploy keys, no .env) with the git data of each
clone in /gitdirs, which the gateway never mounts, and hooks disabled. Config
files are read and written through openclaw-cli, because data/config belongs
to uid 1000 and openclaw.json is mode 0600.
"""

import io
import json
import subprocess
import tarfile
from pathlib import Path

from teamlib import TeamError
from teamlib.context import GATEWAY_CONFIG, GATEWAY_TEAMS, Paths

CHECK_PATH = f"{GATEWAY_CONFIG}/openclaw.check.json"
VALIDATE_SH = (
    'umask 077; cat > "$OPENCLAW_CONFIG_PATH" && node dist/index.js config validate --json; '
    'status=$?; rm -f "$OPENCLAW_CONFIG_PATH"; exit $status'
)
NODE_HELPER = r"""
const fs = require("fs");
const crypto = require("crypto");
const req = JSON.parse(fs.readFileSync(0, "utf8"));
const out = { parsed: {}, errors: {}, files: {} };
if (req.parse) {
  const JSON5 = require("json5");
  for (const [name, text] of Object.entries(req.parse)) {
    try { out.parsed[name] = JSON5.parse(text); } catch (e) { out.errors[name] = e.message; }
  }
}
for (const p of req.read || []) {
  try { out.files[p] = fs.readFileSync(p, "utf8"); } catch (e) { if (e.code !== "ENOENT") throw e; out.files[p] = null; }
}
const sha = (p) => fs.existsSync(p) ? crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex") : null;
for (const [p, expected] of Object.entries(req.expect || {})) {
  if (sha(p) !== expected) { out.conflict = p; break; }
}
if (!out.conflict) {
  for (const op of req.ops || []) {
    if (op.text === null) { fs.rmSync(op.path, { force: true }); continue; }
    const tmp = `${op.path}.tmp-${process.pid}`;
    fs.writeFileSync(tmp, op.text, { mode: 0o600 });
    fs.renameSync(tmp, op.path);
  }
}
process.stdout.write(JSON.stringify(out));
"""


def ssh_command(team: str) -> str:
    return (
        f"ssh -i /keys/{team} -o IdentitiesOnly=yes -o UserKnownHostsFile=/keys/known_hosts "
        "-o StrictHostKeyChecking=accept-new"
    )


def json_out(text: str) -> dict:
    """Parse JSON printed by the OpenClaw CLI, skipping any log lines before it."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("{"):
            try:
                return json.loads("\n".join(lines[index:]))
            except json.JSONDecodeError:
                continue
    raise TeamError(f"expected JSON from OpenClaw, got: {text.strip()[:300]}")


class DockerRunner:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.git_mounts = {paths.teams: "/teams", paths.keys: "/keys", paths.remotes: "/remotes", paths.gitdirs: "/gitdirs"}
        self.gateway_mounts = {paths.config: GATEWAY_CONFIG, paths.teams: GATEWAY_TEAMS}

    def git(self, cwd: Path, *args: str, team: str | None = None, git_dir: Path | None = None,
            check: bool = True, binary: bool = False, input: str | bytes | None = None) -> subprocess.CompletedProcess:
        env = ["-e", "GIT_TERMINAL_PROMPT=0", "-e", "GIT_EDITOR=true"]
        env += ["-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=core.hooksPath", "-e", "GIT_CONFIG_VALUE_0=/dev/null"]
        if team:
            env += ["-e", f"GIT_SSH_COMMAND={ssh_command(team)}"]
        if git_dir:
            env += ["-e", f"GIT_DIR={self.git_path(git_dir)}", "-e", f"GIT_WORK_TREE={self.git_path(cwd)}"]
        if binary and isinstance(input, str):
            input = input.encode()
        return self._compose(
            "run", "--rm", "-T", *env, "-w", self.git_path(cwd), "team-git", *args,
            label=f"git {' '.join(args)}", check=check, binary=binary, input=input,
        )

    def git_path(self, host: Path) -> str:
        return _map(host, self.git_mounts)

    def keygen(self, team: str, comment: str) -> None:
        self._compose(
            "run", "--rm", "-T", "--entrypoint", "ssh-keygen", "team-git",
            "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", f"/keys/{team}",
            label="ssh-keygen",
        )

    def copy_in(self, src: Path, dest: Path) -> None:
        """Copy a host folder (without .git) into dest under data/teams."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            tar.add(src, arcname=".", filter=lambda info: None if ".git" in Path(info.name).parts else info)
        self._compose(
            "run", "--rm", "-T", "--entrypoint", "tar", "team-git", "-x", "-C", self.git_path(dest),
            label=f"copy {src}", binary=True, input=buffer.getvalue(),
        )

    def remove_tree(self, path: Path) -> None:
        if path in self.git_mounts:
            raise TeamError(f"refusing to remove {path}")
        self._compose("run", "--rm", "-T", "--entrypoint", "rm", "team-git", "-rf", "--", self.git_path(path), label=f"rm -rf {path}")

    def parse_json5(self, texts: dict[str, str]) -> tuple[dict, dict[str, str]]:
        out = self._node({"parse": texts})
        return out["parsed"], out["errors"]

    def read_files(self, paths: list[Path]) -> dict[Path, str | None]:
        mapped = {_map(p, self.gateway_mounts): p for p in paths}
        out = self._node({"read": list(mapped)})
        return {host: out["files"][inner] for inner, host in mapped.items()}

    def write_files(self, ops: list[tuple[Path, str | None]], expect: dict[Path, str | None]) -> None:
        """Apply ops in order, each file written atomically, unless an expected file changed."""
        out = self._node({
            "expect": {_map(p, self.gateway_mounts): sha for p, sha in expect.items()},
            "ops": [{"path": _map(p, self.gateway_mounts), "text": text} for p, text in ops],
        })
        if out.get("conflict"):
            raise TeamError(f"{out['conflict']} changed while teams.py was applying; run the command again")

    def validate(self, config_text: str) -> dict:
        """Run `openclaw config validate --json` on config_text placed next to openclaw.json."""
        result = self._compose(
            "run", "--rm", "-T", "-e", f"OPENCLAW_CONFIG_PATH={CHECK_PATH}", "--entrypoint", "sh",
            "openclaw-cli", "-c", VALIDATE_SH, label="openclaw config validate", check=False, input=config_text,
        )
        return json_out(result.stdout)

    def openclaw(self, *args: str) -> subprocess.CompletedProcess:
        return self._compose("run", "--rm", "-T", "openclaw-cli", *args, label=f"openclaw {' '.join(args)}", check=False)

    def _node(self, request: dict) -> dict:
        result = self._compose(
            "run", "--rm", "-T", "--entrypoint", "node", "openclaw-cli", "-e", NODE_HELPER,
            label="openclaw-cli node helper", input=json.dumps(request),
        )
        return json_out(result.stdout)

    def _compose(self, *args: str, label: str, check: bool = True, binary: bool = False,
                 input: str | bytes | None = None) -> subprocess.CompletedProcess:
        if input is None:
            input = b"" if binary else ""
        try:
            result = subprocess.run(
                ["docker", "compose", *args], cwd=self.paths.stack, input=input,
                capture_output=True, text=not binary, check=False,
            )
        except FileNotFoundError as exc:
            raise TeamError("docker: command not found") from exc
        if check and result.returncode != 0:
            stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode(errors="replace")
            raise TeamError(f"`{label}` failed: {stderr.strip()}")
        return result


def _map(host: Path, mounts: dict[Path, str]) -> str:
    for root, inner in mounts.items():
        if host == root:
            return inner
        if root in host.parents:
            return f"{inner}/{host.relative_to(root).as_posix()}"
    raise TeamError(f"{host} is outside the folders mounted into the containers")
