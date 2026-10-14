# OpenClaw team workspaces managed by git

Date: 2026-10-14
Status: approved in brainstorming, pending spec review

## Context

agents-infra runs one OpenClaw gateway with one default agent (`main`, workspace `stacks/openclaw/data/workspace`). Nothing in the repo versions the workspace, and there is no way to run several groups of agents with their own personas, memory and settings.

This change adds **teams**: a team is a group of OpenClaw agents whose workspaces and settings live in one private git repo with a fixed structure. The gateway stays single; teams are added on top of the plain setup, cloned from git, and synced by hand.

Upstream facts this design relies on (OpenClaw v2026.10.3 docs):

- One agent = one workspace (`SOUL.md`, `AGENTS.md`, ...), one `agentDir` (auth profiles, sessions in `openclaw-agent.sqlite`). Several agents per gateway are configured under `agents.entries.<id>` (`concepts/multi-agent.md`).
- OpenClaw has no "team" object; its own team preset is a group of agents with an id prefix and a workspace root (`agents team create --prefix --workspace-root`).
- A gateway is one trust boundary. Operator access sees every agent; a workspace is the default cwd, not a sandbox (`start/teams.md`, `gateway/security/trust-model.md`).
- `openclaw.json` supports `$include`; files admitted through `OPENCLAW_INCLUDE_ROOTS` are read-only for OpenClaw-owned writes, which fail closed (`gateway/config-secrets-env.md`).
- Session tools reach every agent by default (`tools.sessions.visibility: "all"`, `tools.agentToAgent.enabled: true`). `tools.agentToAgent.allow` is one list, not pairs, so it cannot express "only within a team". `visibility: "tree"` limits an agent to its own sessions plus subagents it spawned, across agent boundaries (`gateway/config-tools/sessions-and-subagents.md`).
- OpenClaw `git init`s a new workspace but never commits or pushes (`concepts/agent-workspace.md`).

## Decisions

| Topic | Decision |
|-------|----------|
| Gateways | One gateway for all teams. Teams are separated in persona, memory, sessions and settings, not secured from each other. |
| Team | A group of agents. Agent id in the gateway: `<team>-<folder>`, for example `tech-coordinator`. |
| Repo format | Own documented structure (`stacks/openclaw/TEAMS.md`), applied with `$include`. Claws (experimental upstream agent packages) are not used. |
| Settings ownership | People own settings and persona files, agents own their workspace. Settings change only through git; the Control UI cannot edit team agents. |
| Sync | Manual: `rune openclaw::team-sync [team]`. No timer, no sidecar. |
| Conflicts | Auto-resolve: agent memory files merge with `merge=union`; other conflicts take git's version per hunk after the VM's state is pushed to `rescue/<vm>/<time>`. Sync never gets stuck on a conflict. |
| Git execution | In a one-off container from the OpenClaw image (has git and ssh), uid 1000, never on the host. |
| Credentials | One SSH deploy key per team and VM, mounted only into the git container. The agent never holds push rights. |
| Cross-team reading | `tools.sessions.visibility: "tree"` pinned by `init.py`. Delegation inside a team uses `subagents.allowAgents`. |
| Example | `examples/teams/tech/` follows the contract; it is the template for real teams and the test fixture. |
| OpenClaw version | Bump to 2026.10.3 first, with `pid: "service:openclaw-gateway"` on `openclaw-cli`. |

## Layout

On the VM:

```
stacks/openclaw/
  teams.toml               teams this VM runs (gitignored; teams.toml.example in git)
  teams.py                 team logic, Python stdlib
  TEAMS.md                 team repo contract
  data/teams/              mounted at /home/node/teams in openclaw-gateway and openclaw-cli
    tech/                  clone of the tech team repo
    fin/
    .generated/            one include stub per team agent, written by teams.py
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
    SOUL.md AGENTS.md IDENTITY.md USER.md    written by people
    MEMORY.md DREAMS.md memory/              written by the agent
    skills/                                  skills for this agent only
  plugins.json         {"plugins": ["<npm package>", ...]}, installed gateway-wide
  .gitignore           .openclaw/, memory/.dreams/, secrets, caches, downloads
  .gitattributes       union merge for agent memory files
```

Rules:

- `<id>` matches `^[a-z0-9][a-z0-9-]*$`; `<team>-<id>` must not collide with an existing agent.
- No secrets. Keys are referenced by env name (`${ANTHROPIC_API_KEY}`); values live in `stacks/openclaw/.env`.
- Team files must not set `workspace` or `agentDir`; teams.py sets `workspace`, and `agentDir` stays at OpenClaw's default (`data/config/agents/<id>/agent`), outside the repo.
- Gateway-wide settings never live in a team repo: gateway, channel accounts, bindings, model providers.
- `.gitattributes`:

  ```
  workspaces/*/MEMORY.md     merge=union
  workspaces/*/DREAMS.md     merge=union
  workspaces/*/memory/**     merge=union
  ```

## Config wiring

Compose (`stacks/openclaw/compose.yaml`):

- `openclaw-gateway` and `openclaw-cli`: add `./data/teams:/home/node/teams` and `OPENCLAW_INCLUDE_ROOTS: /home/node/teams`.
- `openclaw-cli`: add `pid: "service:openclaw-gateway"` (required by 2026.10.1+ ownership checks).
- New `team-git` service: image `${OPENCLAW_IMAGE}`, `profiles: ["tools"]` (Dockge's Start ignores it), entrypoint `git`, own default network (not the sidecar namespace), no `env_file` (API keys stay out), `cap_drop: [NET_RAW, NET_ADMIN]`, `no-new-privileges`, volumes `./data/teams:/teams`, `./data/team-keys:/keys`, `./data/team-remotes:/remotes`. teams.py passes `GIT_SSH_COMMAND="ssh -i /keys/<team> -o IdentitiesOnly=yes -o UserKnownHostsFile=/keys/known_hosts -o StrictHostKeyChecking=accept-new"` per run.

`openclaw.json`, one stub per team agent, written by teams.py:

```json5
agents: { entries: {
  main: { ... },                                                        // untouched
  "tech-coordinator": { $include: "/home/node/teams/.generated/tech-coordinator.json5" },
}}
```

Generated stub:

```json5
{ $include: ["../tech/team.json5", "../tech/agents/coordinator.json5"],   // override listed only when present
  workspace: "/home/node/teams/tech/workspaces/coordinator" }
```

- Include arrays deep-merge in order (team defaults, then agent overrides); sibling keys merge last, so `workspace` cannot be overridden by team files.
- `openclaw.json` changes only when agents are added or removed. Content changes in team files apply through hot reload.
- Skills are files under `workspaces/<id>/skills/`; a ClawHub install into an agent's workspace is committed on the next sync.
- Plugins: missing entries of `plugins.json` are installed with `openclaw-cli plugins install`. Plugins are never uninstalled automatically (gateway-wide; another team may use them); installed plugins no team lists are reported.
- `component.toml` config step adds `{"path": "tools.sessions.visibility", "value": "tree"}` to the pinned keys. With only `main` this changes nothing visible: the main session still sees every same-agent session.

## Commands

All logic lives in `stacks/openclaw/teams.py` (stdlib). Rune tasks in `openclaw.rune` are thin wrappers.

| Command | Does |
|---------|------|
| `rune openclaw::team-add <team> <source>` | Validate name, set up access, clone, check contract, add to `teams.toml`, write stubs, install plugins |
| `rune openclaw::team-sync [team]` | Two-way sync of one team, or all teams in `teams.toml` |
| `rune openclaw::teams` | Teams, their agents, git state (ahead, behind, local changes) |
| `rune openclaw::team-remove <team>` | Refuse while unpushed changes exist; remove stubs, generated files, clone and `teams.toml` entry. Sessions in `data/config/agents/` are kept |

### team-add

1. Validate `<team>` (`^[a-z0-9][a-z0-9-]*$`, not in `teams.toml`).
2. Access: generate `data/team-keys/<team>` (ed25519, via `ssh-keygen` in `team-git`) if missing, then `git ls-remote`. On denial:
   - if `gh` (GitHub URL) or `glab` (GitLab URL) is installed and logged in on the host, add the key as a deploy key with write access, title `<vm>`, and retry;
   - otherwise print the public key and the repo's deploy-key settings URL, and stop. Rerunning continues.
3. Clone into `data/teams/<team>/`, set `pull.rebase=true`, `rebase.autoStash=true`, `user.name`, `user.email`.
4. Check the contract (`workspaces/` exists, valid ids, no collisions, no `workspace`/`agentDir` keys in team files). On failure remove the clone and stop before any config change.
5. Pre-validate the team's settings (see "Validation"), write `.generated/` stubs and `openclaw.json` stubs, install missing plugins, append to `teams.toml`.

### team-sync

1. **Stage** everything under `workspaces/`. Leave out, and report: files containing a secret (the value of any `.env` key named `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD` or listed in `[env] generate`/`ask`, when 8+ characters; or a match of `sk-ant-[A-Za-z0-9_-]{20,}`, `sk-[A-Za-z0-9]{20,}`, `ghp_[A-Za-z0-9]{36}`, `github_pat_[A-Za-z0-9_]{20,}`, `xox[abp]-[A-Za-z0-9-]{10,}`, `AKIA[0-9A-Z]{16}`, `-----BEGIN [A-Z ]*PRIVATE KEY-----`), and files over 5 MB.
2. **Commit** if anything is staged: `sync: <vm> <YYYY-MM-DD HH:MM>`.
3. **Settings edited on the VM** (`team.json5`, `agents/`, `plugins.json`, `.gitattributes`): commit them to `rescue/<vm>/<time>`, push that branch, reset those paths to `HEAD`. Reported.
4. **Fetch** and pre-validate the incoming settings. If invalid, stop: the live clone does not move, the local commit waits, sync names the broken file.
5. **Rebase** onto upstream (memory files merge with union). On conflict:
   1. abort the rebase;
   2. push the local commit to `rescue/<vm>/<time>`;
   3. rebase again with `-X ours` (during a rebase "ours" is upstream: git's version wins conflicting hunks, non-conflicting local edits stay);
   4. any path still unmerged (modify/delete, binary): take upstream's version, or upstream's deletion.
   Report every resolved path and the rescue branch.
6. **Push.** If rejected, fetch and rebase again, up to 3 attempts, then stop with the local commit kept.
7. **Apply**: regenerate stubs (agents added or removed in git), update `openclaw.json` stubs, install missing plugins, run `openclaw config validate`.
8. Offline or remote errors stop that team with a clear message; `team-sync` without a name continues with the other teams and exits non-zero if any failed.

### Validation

Incoming settings are checked before they go live. In team-add the fresh clone is the candidate; in team-sync it is a `git worktree add` of the fetched upstream under `data/teams/.check/<team>/`, a temporary copy of `openclaw.json` whose stubs for that team point into the check worktree, then `openclaw config validate` with `OPENCLAW_CONFIG_PATH` set to the temporary copy. The worktree and copy are removed afterwards.

### Local sources

When `<source>` is a local folder that is not a git repo (for example `examples/teams/tech`), teams.py copies it, commits it, creates `data/team-remotes/<team>.git` (bare) and uses `/remotes/<team>.git` as the remote. `teams.toml` keeps the folder as `url`; `teams.py apply` on a fresh VM recreates the bare repo from the folder if it is missing. The whole flow, sync included, then works without GitHub or keys. Local sources are for trying and testing: their sync history lives only on that VM.

### New VM

`component.toml` gets a step `script = "teams.py apply"`. `apply` clones every team in `teams.toml` that has no clone (same steps as team-add 2 to 5) and refreshes stubs for existing clones. Missing access becomes an **Action needed** entry with the key to add. Copying `data/team-keys/` from the old VM also works.

## Installer changes

- `components.py`: `[[setup]]` accepts either `run` (compose arguments, as today) or `script` (a file in the component folder plus arguments). Exactly one is required; `once`, `note` apply to both; `interactive`/`headless` only to `run`.
- `cli.py`: a `script` step runs `python3 <component>/<script> <args>` with cwd set to the component folder and env `REPO_DIR`, `STACKS_DIR`, `COMPONENT_DIR`. Lines on stdout starting with `action: ` are added to the Action needed list; a non-zero exit fails the component like a failed compose step.
- `component.toml` (openclaw): `dirs` adds `data/teams`, `data/team-keys`, `data/team-remotes`; the ownership step also chowns `/home/node/teams`; the config step pins `tools.sessions.visibility`; the new `teams.py apply` step runs after the config step.
- `.gitignore`: `stacks/openclaw/teams.toml`.
- `.env.example` and README: `OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.10.3`.

## Example team (`examples/teams/tech/`)

`team.json5` (model via env-referenced provider, `subagents.allowAgents` so `coordinator` delegates to `reviewer`), `agents/reviewer.json5` (one override), `workspaces/coordinator/` and `workspaces/reviewer/` with `SOUL.md`, `AGENTS.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md`, `memory/.gitkeep`, `skills/.gitkeep`, `plugins.json` (`{"plugins": []}`), `.gitignore`, `.gitattributes`. Try it with `rune openclaw::team-add tech examples/teams/tech`; start a real team by copying the folder into a new private repo.

## Errors

| Case | Behavior |
|------|----------|
| Repo unreachable on team-add | Key added through `gh`/`glab` when available, else key and settings URL printed; partial clone removed; `openclaw.json` untouched |
| Contract violation | Stop before any config change, name the problem |
| Same memory file changed on both sides | Union merge, no conflict |
| Same persona file changed on both sides | VM state to `rescue/<vm>/<time>`, git wins conflicting hunks, reported |
| Settings edited on the VM | Saved to a rescue branch, reset to git |
| Broken settings in git | Pre-validation fails, clone stays, gateway keeps the last good settings |
| Secret or file over 5 MB in a workspace | Not committed, reported |
| Push rejected | Up to 3 fetch, rebase, push attempts |
| Offline | Local commit kept; other teams continue |
| Agent folder removed in git | Stub removed, agent leaves the gateway, sessions kept |
| Plugin install fails | Others continue; retried next sync; non-zero exit |
| Gateway stopped | Git steps run; config applies on next start |

## Verify first (spike, OpenClaw 2026.10.3 container)

Results go into an addendum of this spec before the rest is built.

1. A `$include` stub in `agents.entries.<id>` is accepted, and survives OpenClaw rewriting `openclaw.json` (a `config set` of an infra key, a Control UI edit of `main`).
2. Writes to `main` and infra keys still succeed with stubs present; a write to a team agent fails closed with a clear error.
3. How teams.py can add a stub: `config set agents.entries.<id> '{"$include": "..."}'`, or a direct JSON edit with the gateway stopped.
4. `OPENCLAW_INCLUDE_ROOTS`, relative paths in nested includes, and the absolute `workspace` behave as documented.
5. Hot reload picks up changes to included files. If not, team-sync triggers a reload after applying.
6. `tools.sessions.visibility: "tree"` blocks cross-team session reads while `subagents.allowAgents` delegation still works.
7. `openclaw config validate` honors an overridden `OPENCLAW_CONFIG_PATH` (needed for pre-validation).
8. `openclaw backup create` includes workspaces under `/home/node/teams`. If not, `rune openclaw::backup` and `update` also archive `data/teams/` (without `.check/`) into `backups/`.
9. `pid: "service:openclaw-gateway"` works on `openclaw-cli`, and `plugins install` runs through it.

Fallbacks: if 1 to 3 fail, teams.py writes full agent entries with `openclaw config set` instead of stubs (git stays the source of truth because every sync reapplies them; UI edits are overwritten instead of refused). If 7 fails, validation runs after applying and, on failure, resets the team's settings paths to the previous commit.

## Testing

1. Spike above.
2. Unit tests first (TDD) in `tests/test_teams.py`: `teams.toml` read and write, name and contract validation (including the example folder), stub generation, git command building, secret and size filtering, settings-edit detection, plugin diff. Sync flows (union merge, conflict resolution with rescue branch, push retry, local sources) run against real git in temp directories through an injectable runner, so they need no Docker.
3. Installer: `script` step parsing in `tests/test_components.py`, execution and `action:` collection in `tests/test_cli.py`.
4. Local smoke: two teams from local folders, add, sync both directions, forced persona conflict, broken `team.json5`, remove; `openclaw agents list` shows the expected agents; visibility pinned.
5. `rune validate`, `rune test`.
6. On the VM, by the user, with real deploy keys.

## Docs

- `stacks/openclaw/TEAMS.md`: contract, example walkthrough, sync behavior and conflict rules.
- `stacks/openclaw/README.md` and root `README.md`: team tasks, data layout rows for `data/teams`, `data/team-keys`, `data/team-remotes`, version bump.
- Security notes: one gateway is one trust boundary; teams are organized, not isolated; agents can reach other teams' files by absolute path until the sandbox (separate sub-project) is on; deploy keys are credentials.

## Out of scope

- A gateway per team.
- Automatic or scheduled sync.
- Channel bindings in team repos.
- A skills folder shared by a whole team.
- Sandbox, custom image, local embeddings (separate sub-projects).
- Claws.

## Sources

OpenClaw v2026.10.3: `docs/concepts/{multi-agent,multi-user,agent-workspace}.md`, `docs/start/teams.md`, `docs/gateway/multi-tenant-hosting.md`, `docs/gateway/config-secrets-env.md`, `docs/gateway/config-tools/sessions-and-subagents.md`, `docs/gateway/security/trust-model.md`, `docs/tools/skills-config.md`, `docs/install/docker/compose-operations.md`, `docs/cli/{claws,backup}.md`, `CHANGELOG/2026.10.1.md`.
