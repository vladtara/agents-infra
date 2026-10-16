"""Two-way git sync of one team clone: commit, rescue, fetch, rebase, push."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from teamlib import TeamError, scan
from teamlib.contract import REQUIRED_FILES
from teamlib.tree import Tree, from_rev

ATTEMPTS = 3
MAX_REBASE_STEPS = 200


def nul(paths: list[str]) -> str:
    return "\0".join(paths) + "\0"


@dataclass
class Sync:
    runner: object
    clone: Path
    git_dir: Path
    team: str
    branch: str
    vm: str
    say: Callable[[str], None]

    @property
    def upstream(self) -> str:
        return f"origin/{self.branch}"

    def git(self, *args: str, check: bool = True, binary: bool = False, input: str | None = None):
        return self.runner.git(self.clone, *args, team=self.team, git_dir=self.git_dir, check=check, binary=binary, input=input)

    def status(self, *pathspec: str) -> list[tuple[str, str]]:
        """(XY, path) for every changed or untracked file, as `git status --porcelain` reports it."""
        out = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *pathspec).stdout
        items, entries = out.split("\0"), []
        index = 0
        while index < len(items):
            item = items[index]
            index += 1
            if not item:
                continue
            xy, path = item[:2], item[3:]
            if xy[0] in "RC":
                index += 1  # the next item is the rename source
            entries.append((xy, path))
        return entries

    def stage_workspaces(self, secrets: list[str]) -> None:
        """Stage changes under workspaces/, leaving out secrets, big files, nested repos and non-agent folders."""
        nested = {p.parent.name for p in (self.clone / "workspaces").glob("*/.git")}
        for folder in sorted(nested):
            self.say(f"  warning: workspaces/{folder} holds its own .git; it is not synced until you remove it")
        self.git("reset", "-q")  # the commit holds only what this sync checked
        listing = self.git("ls-tree", "-z", "--name-only", "HEAD", "workspaces/").stdout
        agents = {path.split("/")[1] for path in listing.split("\0") if path}
        paths, strays = [], set()
        for _, path in self.status("workspaces"):
            parts = path.split("/")
            if len(parts) > 1 and parts[1] in nested:
                continue
            if len(parts) < 3 or parts[1] not in agents:
                strays.add(f"workspaces/{parts[1]}/" if len(parts) > 2 else path)
                continue
            reason = scan.problem(self.clone / path, secrets)
            if reason:
                self.say(f"  not committed: {path} ({reason})")
                continue
            paths.append(path)
        for stray in sorted(strays):
            self.say(
                f"  not committed: {stray} is not an agent folder in git "
                "(new agents are added in git: workspaces/<id>/ with SOUL.md and AGENTS.md)"
            )
        if paths:
            self.git("add", "--all", "--pathspec-from-file=-", "--pathspec-file-nul", input=nul(paths))
        # A file can change between the scan above and `git add`: check what was really staged.
        blocked = self.staged_problems(secrets)
        if blocked:
            self.git("reset", "-q", "--pathspec-from-file=-", "--pathspec-file-nul", input=nul(sorted(blocked)))
            for path, reason in sorted(blocked.items()):
                self.say(f"  not committed: {path} ({reason})")

    def staged_problems(self, secrets: list[str]) -> dict[str, str]:
        """Why each staged file must not be committed, judged on the staged content itself."""
        raw = self.git("diff", "--cached", "--raw", "-z", "--no-renames", "--no-abbrev", "--diff-filter=d").stdout
        items = [item for item in raw.split("\0") if item]
        problems, blobs = {}, []
        for meta, path in zip(items[0::2], items[1::2]):
            _, mode, _, oid, _ = meta.split()
            if mode == "160000":
                problems[path] = "nested repository"
            else:
                blobs.append((path, oid))
        if blobs:
            data = self.git("cat-file", "--batch", binary=True, input="".join(f"{oid}\n" for _, oid in blobs)).stdout
            offset = 0
            for path, _ in blobs:
                end = data.index(b"\n", offset)
                size = int(data[offset:end].split()[2])
                reason = scan.content_problem(data[end + 1:end + 1 + size], secrets)
                if reason:
                    problems[path] = reason
                offset = end + 1 + size + 1
        return problems

    def commit(self, when: datetime) -> bool:
        if self.git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        self.git("commit", "-q", "--no-verify", "-m", f"sync: {self.vm} {when:%Y-%m-%d %H:%M}")
        return True

    def rescue_settings(self, stamp: str, secrets: list[str]) -> str | None:
        """Move edits made on the VM outside workspaces/ to a rescue branch and reset them to git.

        The branch is pushed unless a rescued file holds a secret; then it stays on this VM.
        """
        changed = self.status(".", ":(exclude)workspaces/")
        if not changed:
            return None
        paths = [path for _, path in changed]
        self.git("add", "--all", "--pathspec-from-file=-", "--pathspec-file-nul", input=nul(paths))
        blocked = [f"{path}: {reason}" for path, reason in sorted(self.staged_problems(secrets).items())]
        tree = self.git("write-tree").stdout.strip()
        commit = self.git("commit-tree", tree, "-p", "HEAD", "-m", f"rescue: settings edited on {self.vm}").stdout.strip()
        branch = f"rescue/{self.vm}/{stamp}-settings"
        pushed = self._save(branch, commit, push=not blocked)
        self.git("reset", "-q")
        tracked = [path for xy, path in changed if xy != "??"]
        untracked = [path for xy, path in changed if xy == "??"]
        if tracked:
            self.git("checkout", "HEAD", "--pathspec-from-file=-", "--pathspec-file-nul", input=nul(tracked))
        if untracked:
            self.git("clean", "-fq", "--", *untracked)
        if blocked:
            where = f"{branch} (kept on this VM, not pushed because {'; '.join(blocked)})"
        else:
            where = branch if pushed else f"{branch} (local only: push failed)"
        self.say(f"  settings edited on this VM were saved to {where} and reset to git: {', '.join(sorted(paths))}")
        return branch

    def fetch(self) -> None:
        result = self.git("fetch", "-q", "origin", self.branch, check=False)
        if result.returncode:
            raise TeamError(f"fetch failed ({_last_line(result.stderr)}); local changes stay committed, sync again later")

    def rebase(self, stamp: str) -> None:
        """Rebase onto upstream. On conflict, save local commits to a rescue branch and let upstream win."""
        first = self.git("rebase", "-q", self.upstream, check=False)
        if first.returncode == 0:
            self._settle_autostash()
            return
        if not self._unmerged():
            self.git("rebase", "--abort", check=False)
            raise TeamError(f"rebase failed: {_last_line(first.stderr or first.stdout)}")
        self.git("rebase", "--abort")
        branch = f"rescue/{self.vm}/{stamp}"
        pushed = self._save(branch, "HEAD")
        resolved = []
        result = self.git("rebase", "-q", "-X", "ours", self.upstream, check=False)
        for _ in range(MAX_REBASE_STEPS):
            if result.returncode == 0:
                break
            unmerged = self._unmerged()
            if not unmerged:
                skipped = self.git("rebase", "--skip", check=False)
                if "no rebase in progress" in skipped.stderr.lower():
                    raise TeamError(f"rebase failed: {_last_line(result.stderr or result.stdout)}")
                result = skipped
                continue
            for path in unmerged:
                # During a rebase "ours" is upstream: keep its version, or its deletion.
                if self.git("cat-file", "-e", f"HEAD:{path}", check=False).returncode == 0:
                    self.git("checkout", "--ours", "--", path)
                    self.git("add", "--", path)
                else:
                    self.git("rm", "-q", "--", path)
                resolved.append(path)
            result = self.git("rebase", "--continue", check=False)
        if result.returncode != 0:
            self.git("rebase", "--abort", check=False)
            raise TeamError("rebase did not finish; local commits are kept in " + branch)
        self._settle_autostash()
        where = branch if pushed else f"{branch} (local only: push failed)"
        resolved_note = f"; upstream kept for {', '.join(sorted(set(resolved)))}" if resolved else ""
        self.say(f"  conflict with git: this VM's commits are saved in {where}, git wins conflicting lines{resolved_note}")

    def drop_removed_agents(self, stamp: str) -> None:
        """New VM files in agent folders git removed meanwhile: rescue them and keep them out of git."""
        listing = self.git("ls-tree", "-r", "-z", "--name-only", "HEAD", "workspaces/").stdout
        found: dict[str, set[str]] = {}
        for path in filter(None, listing.split("\0")):
            parts = path.split("/")
            if len(parts) >= 3:
                found.setdefault(parts[1], set()).add(parts[2])
        removed = sorted(f"workspaces/{folder}" for folder, names in found.items() if not set(REQUIRED_FILES) <= names)
        if not removed:
            return
        branch = f"rescue/{self.vm}/{stamp}-removed"
        pushed = self._save(branch, "HEAD")
        self.git("rm", "-r", "-q", "--cached", "--", *removed)
        self.git("commit", "-q", "--no-verify", "-m", f"sync: {self.vm} drop files in agent folders removed in git")
        where = branch if pushed else f"{branch} (local only: push failed)"
        self.say(f"  agent folders removed in git: {', '.join(removed)}; this VM's new files there are saved in {where} and stay on this VM")

    def push(self) -> bool:
        """Push local commits. False when the remote moved on (fetch and rebase again)."""
        ahead = int(self.git("rev-list", "--count", f"{self.upstream}..HEAD").stdout.strip())
        if not ahead:
            return True
        result = self.git("push", "-q", "origin", f"HEAD:refs/heads/{self.branch}", check=False)
        if result.returncode == 0:
            self.say(f"  pushed {ahead} commit(s)")
            return True
        if "[rejected]" in result.stderr or "fetch first" in result.stderr or "non-fast-forward" in result.stderr:
            return False
        raise TeamError(f"push failed ({_last_line(result.stderr)}); local commits are kept")

    def integrate(self, check: Callable[[Tree], object], stamp: str) -> None:
        """Fetch, check incoming settings, rebase and push, retrying when the push is rejected."""
        for _ in range(ATTEMPTS):
            self.fetch()
            behind = int(self.git("rev-list", "--count", f"HEAD..{self.upstream}").stdout.strip())
            if behind:
                check(from_rev(self.git, self.upstream))
            self.rebase(stamp)
            self.drop_removed_agents(stamp)
            if behind:
                self.say(f"  pulled {behind} commit(s)")
            if self.push():
                return
        raise TeamError(f"push was rejected {ATTEMPTS} times; local commits are kept, sync again later")

    def ahead_behind(self) -> tuple[int, int]:
        out = self.git("rev-list", "--left-right", "--count", f"HEAD...{self.upstream}").stdout.split()
        return int(out[0]), int(out[1])

    def unpushed(self) -> list[str]:
        """What deleting the clone would lose: local changes, unpushed commits, stashes, local-only rescue branches."""
        pending = [path for _, path in self.status(".")]
        ahead, _ = self.ahead_behind()
        if ahead:
            pending.append(f"{ahead} unpushed commit(s)")
        stashes = [line for line in self.git("stash", "list").stdout.splitlines() if line.strip()]
        if stashes:
            pending.append(f"{len(stashes)} stash entr{'y' if len(stashes) == 1 else 'ies'}")
        listing = self.git("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads/rescue", "refs/remotes/origin/rescue").stdout
        refs = dict(line.split() for line in listing.splitlines() if line.strip())
        for ref, oid in sorted(refs.items()):
            name = ref.removeprefix("refs/heads/")
            if ref.startswith("refs/heads/") and refs.get(f"refs/remotes/origin/{name}") != oid:
                pending.append(f"local-only branch {name}")
        return pending

    def _save(self, branch: str, rev: str, push: bool = True) -> bool:
        self.git("branch", "-f", branch, rev)
        return push and self.git("push", "-q", "origin", f"{rev}:refs/heads/{branch}", check=False).returncode == 0

    def _settle_autostash(self) -> None:
        """Uncommitted local edits that clash with git stay in `git stash`; git's version goes live."""
        unmerged = self._unmerged()
        if not unmerged:
            return
        self.git("checkout", "HEAD", "--", *unmerged)
        self.say(
            f"  uncommitted edits to {', '.join(unmerged)} clash with git; git's version is live, "
            f"the edits are kept in `git stash` in data/teams/{self.team}"
        )

    def _unmerged(self) -> list[str]:
        return [p for p in self.git("diff", "--name-only", "-z", "--diff-filter=U").stdout.split("\0") if p]


def _last_line(text: str) -> str:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1] if lines else "no output"
