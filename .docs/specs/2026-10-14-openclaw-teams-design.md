# OpenClaw team workspaces managed by git

Date: 2026-10-14
Status: approved; revision re-approved 2026-10-16; spike done (see "Addendum")

## Context

agents-infra runs one OpenClaw gateway with one default agent (`main`, workspace `stacks/openclaw/data/workspace`). Nothing in the repo versions the workspace, and there is no way to run several groups of agents with their own personas, memory and settings.

This change adds **teams**: a team is a group of OpenClaw agents whose workspaces and settings live in one private git repo with a fixed structure. The gateway stays single; teams are added on top of the plain setup, cloned from git, and synced by hand.

Upstream facts this design relies on (OpenClaw v2026.10.3 docs and source):

- One agent = one workspace (`SOUL.md`, `AGENTS.md`, ...), one `agentDir` (auth profiles, sessions in `openclaw-agent.sqlite`). Agents are configured under `agents.entries.<id>`, the key is the agent id (`concepts/multi-agent.md`, `src/config/zod-schema.agents.ts`).
- A roster with more than one entry needs `agents.ownership: "explicit"` (or a legacy `default: true` marker). Without a sole agent there is no implicit default; `agents.defaults.systemAgent.agentId` names the default for operations that support it.
- OpenClaw has no "team" object; its own team preset is a group of agents with an id prefix and a workspace root (`agents team create --prefix --workspace-root`).
- A gateway is one trust boundary. Operator access sees every agent; a workspace is the default cwd, not a sandbox (`start/teams.md`, `gateway/security/trust-model.md`).
- `$include` inside one `agents.entries.<id>` is kept across OpenClaw writes only when the include is a single file inside the config dir. An include outside the config dir, or a nested include array, makes every write that touches the agent roster fail, including Control UI edits of other agents (`src/config/io.write-prepare.ts`, `include-write-boundary.ts`).
- Included files are watched, and `agents.entries` changes apply live without a restart (`src/config/io.read-helpers.ts`, `src/gateway/config-reload-plan.ts`).
- `openclaw config validate` reads the file named by `OPENCLAW_CONFIG_PATH` and exits 1 when invalid; schema errors name the logical path, for example `agents.entries.tech-coordinator.model`.
- OpenClaw runs `git init` inside a workspace it considers new (none of `AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`, `memory/` present), even when the folder is inside another repo (`src/agents/workspace-git.ts`). Existing workspace files are never overwritten.
- Session tools reach every agent by default (`tools.sessions.visibility: "all"`, `tools.agentToAgent.enabled: true`). `tools.agentToAgent.allow` is one list, not pairs, so it cannot express "only within a team". `visibility: "tree"` limits an agent to its own sessions plus subagents it spawned, across agent boundaries (`gateway/config-tools/sessions-and-subagents.md`).
- `openclaw backup create` includes every configured agent's workspace, wherever it lives, plus config include files (`src/commands/backup-shared.ts`).
- The image ships the `json5` npm package (`/app/node_modules/json5`).

## Decisions

| Topic | Decision |
|-------|----------|
| Gateways | One gateway for all teams. Teams are separated in persona, memory, sessions and settings, not secured from each other. |
| Team | A group of agents. Agent id in the gateway: `<team>-<folder>`, for example `tech-coordinator`. |
| Repo format | Own documented structure (`stacks/openclaw/TEAMS.md`). Claws (experimental upstream agent packages) are not used. |
| Applying settings | teams.py merges the team's files into one plain entry file per agent inside OpenClaw's config dir (`agents.d/<agent-id>.json5`); `openclaw.json` holds a one-line `$include` per agent. Edits to `main` and other config keep working in the Control UI. |
| Settings ownership | People own settings and persona files, agents own their workspace. Settings change through git; a Control UI edit to a team agent lands in its `agents.d` file and is overwritten by the next sync. |
| Sync | Manual: `rune openclaw::team-sync [team]`. No timer, no sidecar. |
| Conflicts | Auto-resolve: agent memory files merge with `merge=union`; other conflicts take git's version per hunk after the VM's state is pushed to `rescue/<vm>/<time>`. Sync never gets stuck on a conflict. |
| Git execution | In a one-off container from the OpenClaw image (has git and ssh), uid 1000, never on the host. |
| Credentials | One SSH deploy key per team and VM, mounted only into the git container. The agent never holds push rights. |
| Cross-team reading | `tools.sessions.visibility: "tree"` pinned by `init.py`. Delegation inside a team uses `subagents.allowAgents`. |
| Plugins | Installed from `plugins.json` only when OpenClaw needs no confirmation; anything that needs `--force` or `--accept-capabilities` is reported with the exact command for a person to run. |
| Example | `examples/teams/tech/` follows the contract; it is the template for real teams and the test fixture. |
| OpenClaw version | Bump to 2026.10.3 first, with `pid: "service:openclaw-gateway"` on `openclaw-cli`. |

## Layout

On the VM:

```
stacks/openclaw/
  teams.toml               teams this VM runs (gitignored; teams.toml.example in git)
  teams.py                 entry point: add, sync, remove, list, apply
  teamlib/                 team logic, Python stdlib
  TEAMS.md                 team repo contract
  data/teams/              mounted at /home/node/teams in openclaw-gateway and openclaw-cli
    tech/                  clone of the tech team repo
    fin/
  data/config/agents.d/    one generated entry file per team agent (inside OpenClaw's config dir)
    tech-coordinator.json5
  data/team-keys/          <team> and <team>.pub deploy keys, known_hosts; never mounted into the gateway
  data/team-remotes/       bare repos created from local folders (examples, tests)
examples/teams/tech/       example team, follows the contract
```

`teams.toml`:

```toml
[git]
name = "OpenClaw agents-vm"     # author of sync commits
email = "openclaw@agents-vm"
vm = "agents-vm"                # used in commit messages and rescue branch names; default: hostname

[teams.tech]
url = "git@github.com:org/openclaw-tech.git"   # or a local folder, see "Local sources"
branch = "main"
```

## Team repo contract (`stacks/openclaw/TEAMS.md`)

```
openclaw-tech/
  team.json5           settings shared by all the team's agents (agents.entries.<id> keys: model, tools, skills, subagents, ...)
  agents/<id>.json5    optional per-agent overrides, same keys
  workspaces/<id>/     one folder = one agent; the folder name is the agent id
    SOUL.md AGENTS.md IDENTITY.md USER.md    written by people (SOUL.md and AGENTS.md required)
    MEMORY.md DREAMS.md memory/              written by the agent
    skills/                                  skills for this agent only
  plugins.json         {"plugins": ["<package or ClawHub id>", ...]}, installed gateway-wide
  .gitignore           runtime files, secrets, caches, downloads
  .gitattributes       union merge for agent memory files
```

Rules:

- `<id>` matches `^[a-z0-9][a-z0-9-]*$`; `<team>-<id>` is at most 64 characters and must not collide with an existing agent.
- Every `workspaces/<id>/` contains `SOUL.md` and `AGENTS.md` (so OpenClaw never treats it as new and never runs `git init` inside it) and no `.git`.
- No secrets. Keys are referenced by env name (`${ANTHROPIC_API_KEY}`); values live in `stacks/openclaw/.env`.
- Team files must not set `workspace`, `cwd`, `agentDir` or `default`; teams.py sets `workspace`, and `agentDir` stays at OpenClaw's default (`data/config/agents/<agent-id>/agent`), outside the repo.
- Merge: `agents/<id>.json5` over `team.json5`, objects merged key by key, everything else (arrays included) replaced by the agent file's value.
- Gateway-wide settings never live in a team repo: gateway, channel accounts, bindings, model providers.
- `.gitattributes`:

  ```
  workspaces/*/MEMORY.md     merge=union
  workspaces/*/DREAMS.md     merge=union
  workspaces/*/memory/**     merge=union
  ```

- `.gitignore` (example):

  ```
  .openclaw/
  .openclaw-cli-images/
  .openclaw-repair/
  openclaw-workspace-state.json
  BOOTSTRAP.md
  **/memory/.dreams/
  .env
  *.key
  *.pem
  secrets*
  node_modules/
  downloads/
  .cache/
  ```

## Config wiring

Compose (`stacks/openclaw/compose.yaml`):

- `openclaw-gateway` and `openclaw-cli`: add `./data/teams:/home/node/teams`.
- `openclaw-cli`: add `pid: "service:openclaw-gateway"` (required by 2026.10.1+ ownership checks).
- New `team-git` service: image `${OPENCLAW_IMAGE}`, `profiles: ["tools"]` (Dockge's Start ignores it), entrypoint `git`, own default network (not the sidecar namespace), no `env_file` (API keys stay out), `cap_drop: [NET_RAW, NET_ADMIN]`, `no-new-privileges`, volumes `./data/teams:/teams`, `./data/team-keys:/keys`, `./data/team-remotes:/remotes`. teams.py passes `GIT_SSH_COMMAND="ssh -i /keys/<team> -o IdentitiesOnly=yes -o UserKnownHostsFile=/keys/known_hosts -o StrictHostKeyChecking=accept-new"` per run.

`openclaw.json` after `team-add tech`, edited by teams.py:

```json5
agents: {
  ownership: "explicit",                                      // set once a team exists
  defaults: { systemAgent: { agentId: "main" } },             // only when unset
  entries: {
    main: { workspace: "/home/node/.openclaw/workspace" },    // added if missing, so main stays in the roster
    "tech-coordinator": { $include: "./agents.d/tech-coordinator.json5" },
    "tech-reviewer": { $include: "./agents.d/tech-reviewer.json5" },
  },
}
```

Generated `agents.d/tech-coordinator.json5` (plain JSON, written by teams.py):

```json
{"model": {"primary": "anthropic/claude-sonnet-5-5"}, "subagents": {"allowAgents": ["tech-reviewer"]}, "workspace": "/home/node/teams/tech/workspaces/coordinator"}
```

- teams.py reads JSON5 (team files, `openclaw.json`) with the image's `json5` package: one `node` call in `openclaw-cli` per run that parses every file and returns JSON. The merge itself is Python.
- teams.py edits `openclaw.json` directly (OpenClaw keeps it plain JSON; read through the same JSON5 parser, written atomically with a temp file and rename) because `config set`/`config unset` refuse to add or remove keyed include entries. The gateway applies roster changes live.
- `subagents.allowAgents` in team files names agents by folder (`reviewer`); teams.py prefixes them (`tech-reviewer`). Any other agent id is left as written.
- Skills are files under `workspaces/<id>/skills/`; a ClawHub install into an agent's workspace is committed on the next sync.
- Plugins: missing entries of `plugins.json` are installed with `openclaw-cli plugins install <spec>` (no `--force`, no `--accept-capabilities`). A refusal is reported with the command to run by hand. Plugins are never uninstalled automatically (gateway-wide; another team may use them); installed plugins no team lists are reported.
- `component.toml` config step adds `{"path": "tools.sessions.visibility", "value": "tree"}` to the pinned keys. With only `main` this changes nothing visible: the main session still sees every same-agent session.

## Commands

`stacks/openclaw/teams.py` is the entry point; the logic lives in `stacks/openclaw/teamlib/` (stdlib). Rune tasks in `openclaw.rune` are thin wrappers.

| Command | Does |
|---------|------|
| `rune openclaw::team-add <team> <source>` | Validate name, set up access, clone, check contract, validate settings, write entries, install plugins, add to `teams.toml` |
| `rune openclaw::team-sync [team]` | Two-way sync of one team, or all teams in `teams.toml` |
| `rune openclaw::teams` | Teams, their agents, git state (ahead, behind, local changes) |
| `rune openclaw::team-remove <team>` | Refuse while unpushed changes exist; remove entries, `agents.d` files, clone and `teams.toml` entry. Sessions in `data/config/agents/` are kept |

### team-add

1. Validate `<team>` (`^[a-z0-9][a-z0-9-]*$`, not in `teams.toml`).
2. Access: generate `data/team-keys/<team>` (ed25519, via `ssh-keygen` in `team-git`) if missing, then `git ls-remote`. On denial:
   - if `gh` (GitHub URL) or `glab` (GitLab URL) is installed and logged in on the host, add the key as a deploy key with write access, title `<vm>`, and retry;
   - otherwise print the public key and the repo's deploy-key settings URL, and stop. Rerunning continues.
3. Clone into `data/teams/<team>/`, set `pull.rebase=true`, `rebase.autoStash=true`, `user.name`, `user.email`.
4. Check the contract. On failure remove the clone and stop before any config change.
5. Validate the team's settings (see "Validation"), write `agents.d` files and `openclaw.json` entries, install missing plugins, append to `teams.toml`.

### team-sync

1. **Stage** everything under `workspaces/`. Leave out, and report: files containing a secret (the value of any `.env` key named `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD` or listed in `[env] generate`/`ask`, when 8+ characters; or a match of `sk-ant-[A-Za-z0-9_-]{20,}`, `sk-[A-Za-z0-9]{20,}`, `ghp_[A-Za-z0-9]{36}`, `github_pat_[A-Za-z0-9_]{20,}`, `xox[abp]-[A-Za-z0-9-]{10,}`, `AKIA[0-9A-Z]{16}`, `-----BEGIN [A-Z ]*PRIVATE KEY-----`), and files over 5 MB.
2. **Commit** if anything is staged: `sync: <vm> <YYYY-MM-DD HH:MM>`.
3. **Settings edited on the VM** (anything outside `workspaces/`): commit them to `rescue/<vm>/<time>`, push that branch, reset those paths to `HEAD`. Reported.
4. **Fetch** and validate the incoming settings. If invalid, stop: the live clone does not move, the local commit waits, sync names the agent and the files that feed it.
5. **Rebase** onto upstream (memory files merge with union). On conflict:
   1. abort the rebase;
   2. push the local commit to `rescue/<vm>/<time>`;
   3. rebase again with `-X ours` (during a rebase "ours" is upstream: git's version wins conflicting hunks, non-conflicting local edits stay);
   4. any path still unmerged (modify/delete, binary): take upstream's version, or upstream's deletion.
   Report every resolved path and the rescue branch.
6. **Push.** If rejected, fetch and rebase again, up to 3 attempts, then stop with the local commit kept.
7. **Apply**: regenerate `agents.d` files, add or remove `openclaw.json` entries for agents added or removed in git, install missing plugins, run `openclaw config validate`.
8. Offline or remote errors stop that team with a clear message; `team-sync` without a name continues with the other teams and exits non-zero if any failed.

### Validation

Incoming settings are checked before they go live. teams.py builds the team's entries from the candidate files (the fresh clone in team-add; `git show origin/<branch>:<path>` in team-sync), writes a copy of `openclaw.json` with those entries inline to `/home/node/.openclaw/openclaw.check.json` (next to the real file, so relative includes of other agents resolve the same), runs `openclaw config validate --json` with `OPENCLAW_CONFIG_PATH` set to that copy, and deletes it. The contract checks (ids, required files, forbidden keys) run on the same candidate files first.

### Local sources

When `<source>` is a local folder that is not a git repo (for example `examples/teams/tech`), teams.py copies it, commits it, creates `data/team-remotes/<team>.git` (bare) and uses `/remotes/<team>.git` as the remote. `teams.toml` keeps the folder as `url`; `teams.py apply` on a fresh VM recreates the bare repo from the folder if it is missing. The whole flow, sync included, then works without GitHub or keys. Local sources are for trying and testing: their sync history lives only on that VM.

### New VM

`component.toml` gets a step `script = "teams.py apply"`. `apply` clones every team in `teams.toml` that has no clone (same steps as team-add 2 to 5) and regenerates entries for existing clones. Missing access becomes an **Action needed** entry with the key to add. Copying `data/team-keys/` from the old VM also works.

## Installer changes

- `components.py`: `[[setup]]` accepts either `run` (compose arguments, as today) or `script` (a file in the component folder plus arguments). Exactly one is required; `once`, `note` apply to both; `interactive`/`headless` only to `run`.
- `cli.py`: a `script` step runs `python3 <component>/<script> <args>` with cwd set to the component folder and env `REPO_DIR`, `STACKS_DIR`, `COMPONENT_DIR`. Lines on stdout starting with `action: ` are added to the Action needed list; a non-zero exit fails the component like a failed compose step.
- `component.toml` (openclaw): `dirs` adds `data/teams`, `data/team-keys`, `data/team-remotes`, `data/config/agents.d`; the ownership step also chowns `/home/node/teams` (the mount point, not the clones' contents); the config step pins `tools.sessions.visibility`; the new `teams.py apply` step runs after the config step.
- `.gitignore`: `stacks/openclaw/teams.toml`.
- `.env.example` and README: `OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.10.3`.

## Example team (`examples/teams/tech/`)

`team.json5` (model, `subagents.allowAgents: ["reviewer"]` so `coordinator` delegates to `reviewer`), `agents/reviewer.json5` (one override), `workspaces/coordinator/` and `workspaces/reviewer/` with `SOUL.md`, `AGENTS.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md`, `memory/.gitkeep`, `skills/.gitkeep`, `plugins.json` (`{"plugins": []}`), `.gitignore`, `.gitattributes`. The example's JSON5 files use strict JSON syntax so tests can read them with the standard library; real teams may use comments and other JSON5 syntax. Try it with `rune openclaw::team-add tech examples/teams/tech`; start a real team by copying the folder into a new private repo.

## Errors

| Case | Behavior |
|------|----------|
| Repo unreachable on team-add | Key added through `gh`/`glab` when available, else key and settings URL printed; partial clone removed; `openclaw.json` untouched |
| Contract violation | Stop before any config change, name the problem |
| Same memory file changed on both sides | Union merge, no conflict |
| Same persona file changed on both sides | VM state to `rescue/<vm>/<time>`, git wins conflicting hunks, reported |
| Settings edited on the VM | Saved to a rescue branch, reset to git |
| Control UI edit to a team agent | Written to its `agents.d` file; overwritten by the next sync |
| Broken settings in git | Validation fails, clone stays, gateway keeps the last good settings |
| Secret or file over 5 MB in a workspace | Not committed, reported |
| Nested `.git` in a workspace | Reported; sync skips that folder until it is removed |
| Push rejected | Up to 3 fetch, rebase, push attempts |
| Offline | Local commit kept; other teams continue |
| Agent folder removed in git | Entry and `agents.d` file removed, agent leaves the gateway, sessions kept |
| Plugin needs confirmation or fails | Reported with the command to run; others continue; non-zero exit |
| Gateway stopped | `openclaw-cli` starts it (as every `rune openclaw::cli` task does) |

## Verify first (spike, OpenClaw 2026.10.3 container)

The source review settled backups, include watching, `config validate` with `OPENCLAW_CONFIG_PATH`, and the `config set`/`unset` refusal. The spike confirms the rest on a real container; results go into an addendum of this spec before the rest is built.

1. Editing `openclaw.json` directly to add and remove `agents.entries.<id> = {"$include": "./agents.d/<id>.json5"}` while the gateway runs: the roster changes live (`openclaw agents list --json`).
2. Those entries survive OpenClaw rewriting `openclaw.json` (`config set` of an infra key, a Control UI or `config set` edit of `main`), and an edit to a team agent writes through to its `agents.d` file.
3. After onboarding, whether `agents.entries.main` exists. With `main` listed explicitly, `agents.ownership: "explicit"` and `agents.defaults.systemAgent.agentId: "main"`: the main agent keeps its sessions, and `rune openclaw::tui` and `status` still work (or which `--agent` flag they need).
4. `node -e "require('json5')"` works through `docker compose run --rm -T --entrypoint node openclaw-cli`.
5. `OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.check.json openclaw config validate --json`: exit codes and output shape.
6. `tools.sessions.visibility: "tree"` blocks cross-team session reads while `subagents.allowAgents` delegation still works.
7. `pid: "service:openclaw-gateway"` works on `openclaw-cli`; `plugins install` and `plugins list --json` run through it.
8. A team workspace with `SOUL.md` and `AGENTS.md` gets no nested `.git` on first use.

Fallbacks: if 1 or 2 fail, teams.py writes the merged entries inline into `openclaw.json` instead of `agents.d` includes (every sync rewrites them). If 4 fails, team files must be strict JSON and teams.py parses them with the standard library. If 5 fails, validation runs after applying and, on failure, restores the team's previous `agents.d` files.

## Testing

1. Spike above.
2. Unit tests first (TDD) for `teamlib`: `teams.toml` read and write, name and contract validation (including the example folder), entry merge and id prefixing, `openclaw.json` roster edits, secret and size filtering, plugin diff, runner command building. Sync flows (union merge, conflict resolution with rescue branch, settings rescue, push retry, local sources) run against real git in temp directories through an injectable runner, so they need no Docker.
3. Installer: `script` step parsing in `tests/test_components.py`, execution and `action:` collection in `tests/test_cli.py`.
4. Local smoke: two teams from local folders, add, sync both directions, forced persona conflict, broken `team.json5`, remove; `openclaw agents list` shows the expected agents; visibility pinned.
5. `rune validate`, `rune test`.
6. On the VM, by the user, with real deploy keys.

## Docs

- `stacks/openclaw/TEAMS.md`: contract, example walkthrough, sync behavior and conflict rules.
- `stacks/openclaw/README.md` and root `README.md`: team tasks, data layout rows for `data/teams`, `data/team-keys`, `data/team-remotes`, `data/config/agents.d`, version bump.
- Security notes: one gateway is one trust boundary; teams are organized, not isolated; agents can reach other teams' files by absolute path until the sandbox (separate sub-project) is on; anyone who can push to a team repo changes that team's agents and can request gateway-wide plugins; deploy keys are credentials.

## Out of scope

- A gateway per team.
- Automatic or scheduled sync.
- Channel bindings in team repos.
- A skills folder shared by a whole team.
- Sandbox, custom image, local embeddings (separate sub-projects).
- Claws.

## Revision (2026-10-14, after reading the OpenClaw 2026.10.3 source)

| Was | Now | Why |
|-----|-----|-----|
| `openclaw.json` includes team files directly from `/home/node/teams` (`OPENCLAW_INCLUDE_ROOTS`, nested include arrays) | teams.py merges team files into `agents.d/<agent-id>.json5` inside the config dir; `openclaw.json` has one single-file include per agent | External or nested includes make every roster write fail, including Control UI edits of `main` |
| Control UI cannot edit team agents | It can; the edit lands in `agents.d` and the next sync overwrites it | Consequence of the above; git stays the source of truth |
| Include arrays merged by OpenClaw (arrays concatenated) | teams.py merges; the agent file replaces arrays | Predictable overrides, for example a shorter `skills` list |
| `.generated/` stubs in `data/teams/` | `data/config/agents.d/` | Includes must sit inside the config dir |
| Worktree-based validation | Candidate files via `git show`, inline entries in `openclaw.check.json` | `config validate` reads `OPENCLAW_CONFIG_PATH`; no worktree needed |
| (missing) | `agents.ownership: "explicit"`, explicit `main` entry, `systemAgent.agentId: "main"` | Required for a multi-agent roster; keeps `main` and its sessions |
| (missing) | `SOUL.md` and `AGENTS.md` required per workspace; nested `.git` detected | OpenClaw `git init`s workspaces it considers new |
| Plugins installed from `plugins.json` | Only when no confirmation is needed; otherwise reported | A team repo must not silently install gateway-wide code that needs `--force` |
| Backup coverage unknown | Covered by `openclaw backup create` | Source review |

## Sources

OpenClaw v2026.10.3 docs: `docs/concepts/{multi-agent,multi-user,agent-workspace}.md`, `docs/start/teams.md`, `docs/gateway/multi-tenant-hosting.md`, `docs/gateway/config-secrets-env.md`, `docs/gateway/config-tools/sessions-and-subagents.md`, `docs/gateway/security/trust-model.md`, `docs/tools/skills-config.md`, `docs/install/docker/compose-operations.md`, `docs/cli/{claws,backup}.md`, `CHANGELOG/2026.10.1.md`.

OpenClaw v2026.10.3 source: `src/config/{zod-schema.agents.ts,includes.ts,io.write-prepare.ts,include-write-boundary.ts,mutate.ts,io.read-helpers.ts,paths.ts}`, `src/gateway/{config-reload-plan.ts,config-reload.ts}`, `src/cli/{config-cli.ts,plugins-cli.ts,non-clawhub-install-acknowledgement.ts}`, `src/agents/{workspace.ts,workspace-git.ts,agent-scope-config.ts}`, `src/commands/{backup-shared.ts,agents.commands.list.ts}`, `package.json`.

## Addendum (2026-10-16): spike on 2026.10.3 and changes found while building

The spike ran in a scratch copy of the stack (compose project `oc-planwork`, no tailnet login) on `ghcr.io/openclaw/openclaw:2026.10.3`: the component's setup steps with headless onboarding and a placeholder key, then the teamlib code from the implementation plan. The teamlib tests also passed on the image's git 2.39 and Python 3.11.

| # | Check | Result |
|---|-------|--------|
| 1 | Direct `openclaw.json` edit adds `$include` entries | Hot reload applied (`agents.entries.tech-*`, `agents.ownership`, `agents.defaults.systemAgent`); `agents list --json` shows the team agents with their workspaces |
| 2 | Entries survive OpenClaw writes | `config set logging.maxFileBytes` and `config set agents.entries.main.description` keep the includes. `config set agents.entries.tech-reviewer.description` writes through to `agents.d/tech-reviewer.json5` (OpenClaw keeps `.bak` copies next to it); the next sync restores git's version |
| 3 | `main` and the default agent | Onboarding writes `agents.entries.main` with `workspace` and `agentDir`, and sets `agents.defaults.workspace`. With explicit ownership and `systemAgent.agentId: "main"`, `status` works and `tui` opens on agent `main` without `--agent` |
| 4 | `json5` in `openclaw-cli` | `require("json5")` works with `--entrypoint node` |
| 5 | `config validate` with `OPENCLAW_CONFIG_PATH` | Prints JSON with `valid` and `issues[].path`, for example `agents.entries.tech-coordinator.model: Invalid input`; exit 1 when invalid. Broken settings pushed to git stop the sync before anything goes live |
| 6 | `tools.sessions.visibility: "tree"` | Pinned by the config step and accepted. The live cross-team check needs a model key: on the VM (Testing 6) |
| 7 | `pid` on `openclaw-cli` | `status`, `tui`, `plugins list --json` and `backup create` work. `plugins list --json` returns `{workspaceDir, workspaceScope, registry, plugins, diagnostics}`; a non-ClawHub install without a TTY exits 1 with "Install cancelled; rerun with --force after reviewing the source." |
| 8 | No nested `.git` in team workspaces | None after gateway start and restart (no agent turn was run: no model key) |

Also confirmed: `backup create` archives `/home/node/teams/...` and `agents.d/`. `team-add` takes about 25 s and `team-sync` about 22 s per team, one container per git or OpenClaw call. No fallback is needed; `INLINE` stays off.

Changes to the design:

- **The previous default agent keeps its workspace.** On 2026.9.8 the gateway's startup doctor rewrites the roster to `agents.entries.main: {}`; switching to explicit ownership then moved `main` to `~/.openclaw/workspace-main`, away from its files. 2026.10.3 onboarding already pins the workspace, but teams.py now pins it too whenever it switches a roster to explicit ownership: the sole existing agent without a workspace gets `agents.defaults.workspace`, or `/home/node/.openclaw/workspace`. OpenClaw's own migration does the same.
- **Team names must not dash-prefix each other** (`tech` and `tech-ops`), so every agent id belongs to one team. teams.py owns an entry when its id starts with `<team>-` and it is that agent's include.
- **Uncommitted edits that clash with git.** A file left out of the commit (it holds a secret) and also changed in git made rebase's autostash leave conflict markers in the live workspace and blocked every later sync. Git's version now goes live and the edit stays in `git stash`; sync reports it.
- **Removing an agent also removes OpenClaw's `.bak` copies** of its `agents.d` file (`.bak`, `.bak.1` to `.bak.4`).
- **`teams.py apply` exits 0** and reports every per-team problem as an `action:` line, so a team problem never fails the OpenClaw install. It exits 1 only when nothing can run, for example an unreadable `teams.toml`.
- **Ownership of the team folders** is set from the `team-git` container (`chown node:node /teams /keys /remotes`), because `data/team-keys` and `data/team-remotes` are not mounted in the gateway.
- **The example team sets no model**, so it runs on the provider onboarding chose: `team.json5` has `description` and `subagents.allowAgents: ["reviewer"]`; `agents/reviewer.json5` sets `name`, `description` and `allowAgents: []`.
- **Rescue branch names:** `rescue/<vm>/<YYYYMMDD-HHMMSS>` for conflicts, with `-settings` appended for settings edited on the VM.
- **Sync output** names what happened: commits pushed and pulled, agents added or removed, files left out, rescue branches.
