"""A team repo as the contract sees it, read from a git revision."""

import io
import re
import tarfile
from dataclasses import dataclass

SETTINGS_RE = re.compile(r"^(team\.json5|plugins\.json|agents/[^/]+\.json5)$")


@dataclass(frozen=True)
class Tree:
    workspaces: dict[str, frozenset[str]]
    nested_git: frozenset[str]
    texts: dict[str, str]


def from_rev(git, rev: str) -> Tree:
    """Read a committed tree. git(*args, binary=False) runs git in the clone.

    workspaces maps each workspaces/<id>/ folder to the names directly inside it;
    nested_git holds folders committed as nested repos; texts holds the settings files.
    """
    listing = git("ls-tree", "-r", "-z", rev).stdout
    workspaces: dict[str, set[str]] = {}
    nested, settings = set(), []
    for item in filter(None, listing.split("\0")):
        meta, path = item.split("\t", 1)
        parts = path.split("/")
        if parts[0] == "workspaces" and len(parts) >= 2:
            names = workspaces.setdefault(parts[1], set())
            if len(parts) == 2 and meta.startswith("160000"):
                nested.add(parts[1])
            elif len(parts) >= 3:
                names.add(parts[2])
        elif SETTINGS_RE.match(path):
            settings.append(path)
    texts = {}
    if settings:
        data = git("archive", "--format=tar", rev, "--", *settings, binary=True).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    texts[member.name] = tar.extractfile(member).read().decode("utf-8", errors="replace")
    return Tree({name: frozenset(names) for name, names in workspaces.items()}, frozenset(nested), texts)
