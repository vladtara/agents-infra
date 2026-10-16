"""Manage git-backed OpenClaw teams: add, sync, remove, list, apply. See TEAMS.md."""

import argparse
import sys
from pathlib import Path

STACK = Path(__file__).resolve().parent
REPO = STACK.parent.parent
sys.path[:0] = [str(STACK), str(REPO)]

from teamlib import TeamError, commands, registry, scan
from teamlib.context import Context, Paths
from teamlib.runner import DockerRunner


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="teams.py", description="Manage git-backed OpenClaw teams (see TEAMS.md).")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="clone a team repo (SSH URL) or a local folder and add its agents")
    add.add_argument("team")
    add.add_argument("source")
    add.add_argument("--branch", default="main")
    sync = sub.add_parser("sync", help="two-way sync of one team, or all teams")
    sync.add_argument("team", nargs="?")
    remove = sub.add_parser("remove", help="remove a team's agents and clone from this VM")
    remove.add_argument("team")
    sub.add_parser("list", help="teams, agents and git state")
    sub.add_parser("apply", help="clone missing teams and refresh agents (run by init.py)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, runner=None, stack: Path = STACK, repo: Path = REPO, out=None) -> int:
    args = parse_args(argv)
    paths = Paths(stack)
    try:
        ctx = Context(
            repo=repo,
            paths=paths,
            runner=runner or DockerRunner(paths),
            registry=registry.load(paths.registry),
            secrets=scan.load_secrets(stack),
            out=out or sys.stdout,
        )
        if args.command == "add":
            problems = commands.add(ctx, args.team, args.source, args.branch)
            for problem in problems:
                ctx.say(f"  problem: {problem}")
            return 1 if problems else 0
        if args.command == "sync":
            return 0 if commands.sync(ctx, args.team) else 1
        if args.command == "remove":
            commands.remove(ctx, args.team)
        elif args.command == "list":
            commands.list_teams(ctx)
        else:
            commands.apply(ctx)
        return 0
    except TeamError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
