# OpenClaw teams

A team is a group of OpenClaw agents whose personas, memory and settings live in one private git repo. Every team runs in this stack's single gateway, next to the default `main` agent. `teams.py` clones team repos, turns them into agents, and syncs them with git when you run `rune openclaw::team-sync`. Teams need OpenClaw 2026.10.3 or later (`OPENCLAW_IMAGE` in `stacks/openclaw/.env`); `teams.py` refuses older images.

## Try it

```
rune openclaw::team-add tech examples/teams/tech   # the example team, from this repo
rune openclaw::teams                               # agents tech-coordinator and tech-reviewer
rune openclaw::team-sync tech                      # commit what the agents wrote, pull changes
```

A local folder becomes a bare repo in `data/team-remotes/`, so sync works without GitHub. Its history lives only on this VM; use it to try things out.

For a real team, copy `examples/teams/tech/` into a new private repo, push it, and add it by its SSH URL:

```
rune openclaw::team-add tech git@github.com:org/openclaw-tech.git
```

`team-add` creates a deploy key for the team (`data/team-keys/tech`). If `gh` (GitHub) or `glab` (GitLab) is installed and logged in on the VM, it adds the key to the repo with write access; otherwise it prints the key and the repo's deploy-key page, and you rerun the command after adding it. A key added by `gh` belongs to that `gh` login: if the login is revoked, GitHub removes the key.

## Commands

| Command | Does |
|---------|------|
| `rune openclaw::team-add <team> <source>` | Clone `<source>` (SSH URL or folder), check it, add its agents, record it in `teams.toml` |
| `rune openclaw::team-sync [team]` | Two-way sync of one team, or all |
| `rune openclaw::teams` | Teams, agents, unpushed and unpulled commits, changed files, plugins no team lists |
| `rune openclaw::team-remove <team>` | Remove the agents and the clone from this VM. Refused while data exists only on this VM (changes, unpushed commits, stashes, local-only rescue branches); `python3 stacks/openclaw/teams.py remove <team> --force` deletes them too. The repo, sessions and deploy key stay |
| `rune openclaw::team-git <team> <git command>` | Run git on the VM's clone, for example `stash list`, `stash show -p`, `log --oneline rescue/<vm>/<time>-settings` |

Team names match `^[a-z0-9][a-z0-9-]*$` and must not start with another team's name plus `-` (`tech` and `tech-ops` cannot both exist), because agent ids are `<team>-<folder>`.

## Repo structure

```
openclaw-tech/
  team.json5           settings shared by all the team's agents (required; {} for none)
  agents/<id>.json5    optional per-agent overrides
  workspaces/<id>/     one folder = one agent, agent id <team>-<id>
    SOUL.md            persona (required)
    AGENTS.md          operating instructions (required)
    IDENTITY.md USER.md
    MEMORY.md DREAMS.md memory/   written by the agent
    skills/            skills for this agent only
  plugins.json         {"plugins": ["<package or ClawHub id>", ...]}
  .gitignore           runtime files, secrets, caches (copy the example's)
  .gitattributes       union merge for memory files (copy the example's)
```

Rules:

- `<id>` matches `^[a-z0-9][a-z0-9-]*$` and `<team>-<id>` has at most 64 characters.
- Settings files hold the keys of one OpenClaw agent entry (`agents.entries.<id>`): `name`, `description`, `model`, `skills`, `subagents`, `tools`, and so on. They must not set `workspace`, `cwd`, `agentDir` or `default`; `teams.py` sets the workspace and OpenClaw keeps sessions in `data/config/agents/<agent id>/`.
- `agents/<id>.json5` is merged over `team.json5`: objects merge key by key, anything else (lists included) is replaced. In the example, `team.json5` lets every agent delegate to `reviewer`, and `agents/reviewer.json5` replaces that list with `[]`.
- In `subagents.allowAgents`, a folder name of this team (`reviewer`) becomes its agent id (`tech-reviewer`); other names stay as written.
- No secrets in the repo. Reference keys by env name (`${ANTHROPIC_API_KEY}`); values live in `stacks/openclaw/.env`.
- Gateway-wide settings never go in a team repo: gateway, channels, bindings, model providers. Change those in the Control UI or with `rune openclaw::cli config set`.
- Files may use JSON5 (comments, trailing commas); `teams.py` parses them with OpenClaw's own parser.

## How sync works

`team-sync` runs these steps per team. Git runs in the `team-git` container, which holds the deploy key and the clone's git data (`data/team-gitdirs/<team>`), with git hooks off; the gateway sees neither, so an agent cannot change git's hooks or config.

1. Stage the changes the agents made under `workspaces/`. Files holding a secret (a value from `.env`, or something shaped like an API key or private key) and files over 5 MB are left out and listed. The check runs again on the staged content itself, so a file an agent changes while sync runs is caught too. Only agent folders that exist in git are synced; anything else created under `workspaces/` stays on the VM and is listed.
2. Commit them as `sync: <vm> <date time>`.
3. Edits made on the VM outside `workspaces/` (settings, plugins.json) are saved to the branch `rescue/<vm>/<time>-settings` and reset to git. If one of those files holds a secret, the branch is kept on this VM and not pushed. Settings change through git only.
4. Fetch. When git has new commits, check them first: the contract above, then `openclaw config validate`. If they fail, sync stops and nothing goes live; the gateway keeps the last good settings.
5. Rebase this VM's commits onto git's (see Conflicts). New files the VM wrote into an agent folder that git removed meanwhile are saved to `rescue/<vm>/<time>-removed` and kept out of git; they stay on the VM.
6. Push; when someone pushed in between, fetch and rebase again, up to three times.
7. Apply: write each agent's merged settings to `data/config/agents.d/<agent id>.json5`, add or remove agents in `openclaw.json`, install missing plugins. The gateway picks this up without a restart.

Offline, sync keeps the local commit and stops that team; `team-sync` without a name carries on with the other teams and exits non-zero.

## Conflicts

| Case | Result |
|------|--------|
| Same memory file (`MEMORY.md`, `DREAMS.md`, `memory/`) changed on both sides | Both versions kept (`merge=union`), no conflict |
| Same lines of another file changed on both sides | This VM's commits are saved to `rescue/<vm>/<time>`, git's version wins those lines, the rest of the VM's edits stay |
| File deleted in git, edited on the VM (or the other way round) | Git's side wins; the VM's version is in the rescue branch |
| A file left out in step 1 also changed, added or deleted in git | Git's version goes live; the VM's copy stays in the clone's stash (`rune openclaw::team-git <team> stash show -p --include-untracked`) |
| Settings edited in the Control UI | Saved to the agent's `agents.d` file, then overwritten by the next sync |
| A workspace with its own `.git` | Reported and skipped until you delete that `.git` |
| A folder under `workspaces/` that is not an agent in git | Reported and left on the VM; add agents through git |

To look at a rescue branch: `git fetch` in your own clone, then `git log -p origin/rescue/<vm>/<time>`. A branch kept on the VM (it holds a secret): `rune openclaw::team-git <team> show rescue/<vm>/<time>-settings`.

## Plugins

`plugins.json` names plugins the team needs, by package name or ClawHub id (an entry starting with `-` is rejected). Plugins are gateway-wide: every agent of every team can use them. `teams.py` installs a missing plugin only when OpenClaw installs it without asking. Anything OpenClaw wants confirmed (a source outside ClawHub, extra capabilities) is reported with the command for a person to run after reviewing it, for example `rune openclaw::cli plugins install <spec>`. Plugins are never uninstalled automatically; `rune openclaw::teams` lists installed plugins no team names.

## A new VM

`teams.toml` (ignored by git, see `teams.toml.example`) lists the teams a VM runs. Copy it to the new VM, optionally with `data/team-keys/`, and run `python3 init.py openclaw`: the install clones every team and adds its agents. Teams it cannot reach show up under **Action needed** with the key to add.

## On the VM

| Path | Contents |
|------|----------|
| `stacks/openclaw/teams.toml` | Teams and the git identity of sync commits |
| `data/teams/<team>/` | The clone's files; the agents work in `workspaces/` (`/home/node/teams` in the containers) |
| `data/team-gitdirs/<team>/` | The clone's git data; mounted only into `team-git` |
| `data/config/agents.d/` | Generated agent entries, one file per agent |
| `data/team-keys/` | Deploy keys and `known_hosts`; mounted only into `team-git` |
| `data/team-remotes/` | Bare repos made from local folders |

## Security

- One gateway is one trust boundary. Teams are organized apart (persona, memory, sessions, settings), not isolated: an operator sees every agent, and an agent can read other teams' files by absolute path until the sandbox is on.
- `tools.sessions.visibility` is pinned to `tree`: an agent sees its own sessions and the subagents it spawned, not other teams' sessions.
- Anyone who can push to a team repo changes that team's agents and can ask for gateway-wide plugins. Give push access accordingly.
- Deploy keys and the clones' git data are mounted only into `team-git`, never into the gateway, and git hooks are off there, so an agent cannot plant a hook that reads the keys.
- Deploy keys are credentials. Treat `data/team-keys/` like `.env`.
