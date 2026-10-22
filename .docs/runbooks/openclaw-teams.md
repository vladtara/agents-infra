# OpenClaw Teams Runbook

As of 2026-10-22. Live copy: [OpenClaw Teams Runbook](https://claude.ai/artifact/1ea4vhyPfWTKT9mCVKcSAb). Repo contract: [stacks/openclaw/TEAMS.md](../../stacks/openclaw/TEAMS.md).

## Overview

A team is a group of OpenClaw agents whose personas, memory and settings live in one private git repo. The one gateway runs every team next to `main`, and you sync each team with git when you choose.

```
Team repos on GitHub (openclaw-tech, openclaw-fin)
  people edit SOUL.md, AGENTS.md and settings here
        ^
        |  fetch and push over SSH with the team's deploy key
        v
team-git container                                  teams.py on the host
  runs every git command: clone, commit,      <---  (rune team-add, team-sync,
  rebase, push                                        teams, team-remove)
  deploy keys: data/team-keys                               |
  git data:    data/team-gitdirs (hooks off)                |
        ^                                                   |
        |  checkout and staging                             |
        v                                                   |
data/teams/tech, data/teams/fin on the VM                   |
  the clone's files only: team.json5, agents/, workspaces/  |
  mounted into the gateway as /home/node/teams              |
        ^                                                   |
        |  agents read SOUL.md and write memory             |  applies agent settings
        v                                                   v  through openclaw-cli
OpenClaw gateway
  agents tech-coordinator, tech-reviewer, fin-... and main
  never mounts the deploy keys or the git data
```

`teams.py` on the host drives git inside the `team-git` container, which alone holds the deploy keys and the git data. The gateway mounts only the team files and gets each agent's settings through `data/config/agents.d/`.

## Prerequisites

Check these once per VM before adding the first team.

| Need | Why | How to check or set it |
| --- | --- | --- |
| OpenClaw 2026.10.3 or later | `teams.py` refuses older images | `OPENCLAW_IMAGE` in `stacks/openclaw/.env`, then `rune openclaw::update` |
| OpenClaw installed and onboarded | Teams are added to `data/config/openclaw.json` | `python3 init.py openclaw` |
| A private team repo with an SSH URL | Deploy keys work over SSH only | `git@github.com:org/openclaw-tech.git` |
| `gh` (GitHub) or `glab` (GitLab) logged in on the VM | Optional: `team-add` then adds the deploy key itself | `gh auth status` |
| rune 0.6.0 | Runs the team tasks | `rune --list \| grep team` |

The teams a VM runs are listed in `stacks/openclaw/teams.toml` (not in git). `team-add` creates it; copy `teams.toml.example` first to set the commit author and the VM name used in branch names.

## Create a team repo

Start by copying `examples/teams/tech/` from agents-infra into a new private repo and pushing it. Each folder under `workspaces/` becomes one agent named `<team>-<folder>`.

```
openclaw-tech/
  team.json5             settings shared by all agents of the team ({} for none)
  agents/reviewer.json5  optional overrides for one agent
  workspaces/
    coordinator/         agent tech-coordinator
      SOUL.md            persona (required, written by people)
      AGENTS.md          operating instructions (required)
      IDENTITY.md USER.md
      MEMORY.md memory/  written by the agent
      skills/            skills for this agent only
    reviewer/            agent tech-reviewer
  plugins.json           {"plugins": ["<package or ClawHub id>"]}
  .gitignore             copy the example's
  .gitattributes         union merge for memory files; copy the example's
```

Rules:

- Folder names match `^[a-z0-9][a-z0-9-]*$`; `<team>-<folder>` is at most 64 characters.
- Settings files hold the keys of one OpenClaw agent entry: `name`, `description`, `model`, `skills`, `subagents`, `tools`. They must not set `workspace`, `cwd`, `agentDir` or `default`.
- `agents/<id>.json5` is merged over `team.json5`: objects merge key by key, lists and plain values are replaced.
- In `subagents.allowAgents`, a folder name of this team (`reviewer`) becomes its agent id (`tech-reviewer`).
- No secrets in the repo. Refer to keys by name, for example `${ANTHROPIC_API_KEY}`; values live in `stacks/openclaw/.env`.
- Gateway-wide settings (gateway, channels, bindings, model providers) never go in a team repo.
- Team names must not start with another team's name plus `-`: `tech` and `tech-ops` cannot both exist.

## Add a team

One command clones the repo, checks it, and adds its agents to the running gateway without a restart.

```
rune openclaw::team-add tech git@github.com:org/openclaw-tech.git
rune openclaw::team-add tech examples/teams/tech        # try it with the example, no GitHub needed
```

1. Checks the team name and that the image is 2026.10.3 or later.
2. Creates a deploy key `stacks/openclaw/data/team-keys/tech` if there is none.
3. Tests access. If the repo refuses the key:
    - with `gh` or `glab` logged in, adds the key with write access (title = VM name) and retries;
    - otherwise prints the public key and the repo's deploy-key page; add it, then run the command again.
4. Clones the repo: files to `data/teams/tech/`, git data to `data/team-gitdirs/tech/`.
5. Checks the repo against the rules above, then asks OpenClaw to validate the settings. On any problem it stops, removes the clone and changes nothing.
6. Writes one file per agent to `data/config/agents.d/` and adds the agents to `openclaw.json`.
7. Installs plugins from `plugins.json` that need no confirmation; the rest are listed with the command to run.
8. Records the team in `teams.toml`.

A local folder (like the example) becomes a bare repo in `data/team-remotes/`, so sync works without GitHub; its history stays on that VM. A key added by `gh` belongs to that `gh` login: if the login is revoked, GitHub removes the key.

## Sync step by step

Sync is manual and two-way: the agents' memory goes to git, people's changes come to the agents. It takes about 20 seconds per team.

```
rune openclaw::team-sync tech     # one team
rune openclaw::team-sync          # all teams; a failing team does not stop the others
```

1. **Collect the agents' work.** Changes under `workspaces/` of agents that exist in git are staged. Left out and listed: files holding a secret (a value from `.env`, or something shaped like an API key or private key), files over 5 MB, and folders that are not agents in git. The check runs again on the staged content, so a file changed mid-sync is caught too.
2. **Commit** as `sync: <vm> <YYYY-MM-DD HH:MM>`.
3. **Rescue settings edited on the VM.** Changes outside `workspaces/` (`team.json5`, `plugins.json`) go to the branch `rescue/<vm>/<time>-settings` and are reset to git. If one holds a secret, that branch stays on the VM and is not pushed.
4. **Fetch and check.** New commits from git are checked against the repo rules and `openclaw config validate` before anything goes live. If they fail, sync stops; agents keep the last good settings and the local commit waits.
5. **Rebase** the VM's commits onto git's (see Conflicts and recovery). New VM files in an agent folder git removed meanwhile go to `rescue/<vm>/<time>-removed` and stay on the VM.
6. **Push.** If someone pushed in between: fetch, check and rebase again, up to 3 times.
7. **Apply.** Rewrite each agent's file in `data/config/agents.d/`, add or remove agents in `openclaw.json`, install missing plugins. The gateway picks it up without a restart.

Offline, sync keeps the local commit and reports the error; the next sync pushes it. Sync output names what happened: commits pulled and pushed, agents added or removed, files left out, rescue branches.

## Conflicts and recovery

Sync never stops on a conflict: git's version wins, and the VM's version is saved first.

| Case | Result | Where the VM's version is |
| --- | --- | --- |
| Same memory file (`MEMORY.md`, `DREAMS.md`, `memory/`) changed on both sides | Both versions kept (union merge) | In the file |
| Same lines of another file (for example `SOUL.md`) changed on both sides | Git's lines win; the VM's other edits stay | `rescue/<vm>/<time>`, pushed |
| File deleted in git, edited on the VM (or the reverse) | Git's side wins | `rescue/<vm>/<time>`, pushed |
| Settings edited on the VM | Reset to git | `rescue/<vm>/<time>-settings`, pushed unless it holds a secret |
| New VM files in an agent folder git removed | Kept out of git, agent leaves the gateway | `rescue/<vm>/<time>-removed`, pushed; files stay on the VM |
| A file left out (secret or over 5 MB) also changed, added or deleted in git | Git's version goes live | The clone's stash, on the VM only |
| Settings edited in the Control UI | Overwritten by the next sync | Not kept: change settings in git |
| A workspace with its own `.git` | Reported, not synced | Delete that `.git` |

Look at recovery data:

```
# in your own clone of the team repo
git fetch && git log -p origin/rescue/<vm>/<time>

# on the VM (git data lives outside the clone, so use team-git)
rune openclaw::team-git tech stash list
rune openclaw::team-git tech stash show -p --include-untracked
rune openclaw::team-git tech branch --list 'rescue/*'
rune openclaw::team-git tech show rescue/<vm>/<time>-settings
```

A stash or a local-only rescue branch may hold a secret: copy what you need, then drop it (`stash drop`, `branch -D <name>`).

## Command reference

Run rune tasks from the agents-infra repo root. Each rune task wraps `python3 stacks/openclaw/teams.py`, which also has `--help`.

| Task | Does |
| --- | --- |
| `rune openclaw::team-add <team> <source>` | Add a team from an SSH git URL or a local folder |
| `rune openclaw::team-sync [team]` | Two-way sync of one team, or of all teams |
| `rune openclaw::teams` | Teams, agents, commits to push or pull, changed files, plugins no team lists |
| `rune openclaw::team-remove <team>` | Remove a team's agents and clone from this VM (asks to confirm) |
| `rune openclaw::team-git <team> <git command>` | Run git on the VM's clone, for example `log --oneline -5` |
| `rune openclaw::cli plugins install <spec>` | Install a plugin `teams.py` reported as needing confirmation |

Only on `teams.py`:

- `python3 stacks/openclaw/teams.py add <team> <url> --branch <name>` for a repo whose branch is not `main`.
- `python3 stacks/openclaw/teams.py remove <team> --force` to remove even when data exists only on this VM.
- `python3 stacks/openclaw/teams.py apply` re-clones missing teams and refreshes agents; `init.py` runs it on every install.

## Restore on a new VM

The install restores every team listed in `teams.toml`; the team repos hold everything else.

1. Before retiring the old VM, run `rune openclaw::team-sync` so every local commit is pushed.
2. Copy `stacks/openclaw/teams.toml` to the new VM. Optionally copy `stacks/openclaw/data/team-keys/` too, so the existing deploy keys keep working.
3. Check `OPENCLAW_IMAGE` in `stacks/openclaw/.env` is 2026.10.3 or later.
4. Run `python3 init.py openclaw`. Its last setup step clones every team and adds its agents.
5. Read the **Action needed** list at the end. A team that cannot be reached shows the deploy key to add and where. Add it and run `python3 init.py openclaw` again; no `team-add` is needed, the team is already in `teams.toml`.
6. Check with `rune openclaw::teams`.

A team problem never fails the OpenClaw install; it only shows up under Action needed. Teams added from a local folder are rebuilt from that folder, so their sync history from the old VM is not restored.

## Remove a team

Removing a team takes it off this VM only; the team repo, the agents' sessions and the deploy key stay.

```
rune openclaw::team-sync tech
rune openclaw::team-remove tech
```

`team-remove` is refused while the clone holds data that exists only on this VM, and it lists what:

- changed files, including settings edited on the VM;
- commits not pushed yet;
- stash entries;
- rescue branches that were never pushed (for example one holding a secret).

Sync first, look at the rest with `rune openclaw::team-git tech ...`, then remove. To remove anyway and delete that data: `python3 stacks/openclaw/teams.py remove tech --force`.

What goes: the team's entries in `openclaw.json`, its files in `data/config/agents.d/`, the clone in `data/teams/tech/`, its git data in `data/team-gitdirs/tech/`, and its line in `teams.toml`. What stays: sessions in `data/config/agents/<agent id>/`, the deploy key in `data/team-keys/tech`, and for local-folder teams the bare repo in `data/team-remotes/tech.git`.

## Safety model

Teams are organized apart, not isolated: the gateway is one trust boundary, and the protections below keep credentials and secrets away from agents and git.

| Protection | How |
| --- | --- |
| Agents cannot push or read deploy keys | Keys (`data/team-keys/`) and the clones' git data (`data/team-gitdirs/`) are mounted only into the `team-git` container, never the gateway. Git hooks are off there, so a hook an agent writes into its folder never runs. |
| Secrets never reach git | Files are scanned for `.env` values and key-shaped strings before staging, then again on the staged content. Hits stay on the VM and are listed. |
| Broken settings never go live | Incoming settings are checked by the repo rules and `openclaw config validate` before the clone moves. |
| No silent overwrite of Control UI saves | `teams.py` refuses to write when `openclaw.json` or an agent file changed while it worked; run the command again. |
| Teams do not read each other's sessions | `tools.sessions.visibility` is set to `tree`: an agent sees its own sessions and the subagents it started. |
| Plugins need a person's review | `plugins.json` installs only what OpenClaw installs without asking; anything needing `--force` or extra capabilities is listed for a person. |

Limits to keep in mind:

- Until OpenClaw's sandbox is on, an agent can read another team's files by absolute path.
- Anyone who can push to a team repo changes that team's agents and can request gateway-wide plugins. Give push access accordingly.
- Deploy keys and `stacks/openclaw/data/` are credentials: treat them like `.env`.

## Troubleshooting

Every error names its cause; match the start of the message below.

| Message starts with | Cause | Fix |
| --- | --- | --- |
| `teams need OpenClaw 2026.10.3 or later` | Old image in `.env` | Set `OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.10.3`, run `rune openclaw::update` |
| `<url> refuses access ... Add this deploy key` | Key not on the repo, and no `gh`/`glab` login | Add the printed key with write access at the printed page, run the command again |
| `use an SSH URL` | An `https://` URL was given | Use `git@host:owner/repo.git` |
| `team <name> does not follow TEAMS.md` | Repo breaks a rule (listed below the line) | Fix the repo in git, push, add or sync again |
| `OpenClaw rejects the team's settings` | A setting is invalid, for example `agents.entries.tech-coordinator.model` | Fix `team.json5` or `agents/<id>.json5` in git, push, sync |
| `fetch failed` | Offline, or the key was removed | Local commits are kept; restore access, sync again |
| `push failed` | Key is read-only, or the branch is protected | Give the deploy key write access |
| `push was rejected 3 times` | Busy remote | Sync again |
| `... changed while teams.py was applying` | A Control UI save happened mid-run | Run the command again |
| `team name '...' overlaps team '...'` | Names like `tech` and `tech-ops` | Pick a name that is not a dash-prefix of another team |
| `plugin <spec> was not installed` | OpenClaw wants confirmation | Review the plugin, then `rune openclaw::cli plugins install <spec>` |
| `not committed: <file> (looks like it contains a key or token)` | A secret in an agent's file | Remove the secret from the file; it syncs next time |
| `warning: workspaces/<id> holds its own .git` | OpenClaw or an agent made a nested repo | `rune openclaw::shell`, then `rm -rf /home/node/teams/<team>/workspaces/<id>/.git` |
| `... is missing; move data/teams/<team> away and clone it again` | Clone or its git data was deleted | Move the folder away, run `python3 init.py openclaw` |
| `team <name> has data that exists only on this VM` | Unsynced work, stash or local rescue branch | See Remove a team |
