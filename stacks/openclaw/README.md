# OpenClaw

[OpenClaw](https://github.com/openclaw/openclaw) personal AI assistant gateway, pinned to `ghcr.io/openclaw/openclaw:2026.9.8`, on its own tailnet node.

## How it runs

| Service | Role |
|---------|------|
| `tailscale` | Tailnet node `openclaw`. Owns the network namespace and serves `https://openclaw.<tailnet>.ts.net` to `http://127.0.0.1:18789` (`tailscale/serve.json`). |
| `openclaw-gateway` | The gateway, `--bind loopback`, inside the sidecar's namespace. No host ports. |
| `openclaw-cli` | One-off CLI in the same namespace (`docker compose run --rm openclaw-cli <command>`). The `cli` profile keeps Dockge's Start from launching it. |

Requests from Serve arrive over loopback with forwarded headers. `init.py` sets `gateway.trustedProxies: ["127.0.0.1"]` so they are accepted; they still need the gateway token and a one-time device approval. The sidecar runs Tailscale in kernel mode: in userspace mode a tailnet peer could reach `127.0.0.1:18789` directly and look like a local client.

## First login

1. `python3 init.py openclaw` (or `rune install openclaw`) prints the URL.
2. Open `https://openclaw.<tailnet>.ts.net` on a device in your tailnet and paste the gateway token (`OPENCLAW_GATEWAY_TOKEN` in `stacks/openclaw/.env`).
3. The browser shows up as a pending device: `rune openclaw::devices`, then `rune openclaw::approve <requestId>`.

With `init.py --yes`, onboarding runs non-interactively from the model key in `.env` (`ANTHROPIC_API_KEY` first, then `OPENAI_API_KEY`), so fill one in before an unattended install. Run `rune openclaw::onboard` later for the interactive wizard (models, auth, channels).

## Where the data lives

Everything is under `stacks/openclaw/`, ignored by git, owned by uid 1000.

| Host | In the container | Contents |
|------|------------------|----------|
| `data/config/` | `~/.openclaw` | `openclaw.json` (config); `state/openclaw.sqlite` (devices, pairing, cron, plugin state, shared auth); `agents/<id>/agent/openclaw-agent.sqlite` (sessions, transcripts, memory index); `credentials/` (channel logins); plugins in `npm/`, `git/`, `extensions/`; global skills in `skills/`; logs in `logs/` |
| `data/workspace/` | `~/.openclaw/workspace` | The agent's files: `AGENTS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md`, `MEMORY.md`, `memory/YYYY-MM-DD.md`, `DREAMS.md`, workspace `skills/` |
| `data/auth/` | `~/.config/openclaw` | Key for legacy encrypted OAuth credentials |
| `data/tailscale/` | sidecar `/var/lib/tailscale` | Tailnet node identity |
| `backups/` | `/home/node/backups` | `rune openclaw::backup` archives |

Treat `data/config`, `data/auth` and `backups/` as credentials: OAuth tokens are stored as plain text in SQLite. Never copy the live `*.sqlite` files; use `rune openclaw::backup` or stop the stack first. The workspace is a good candidate for its own private git repo.

## Memory

- Memory is Markdown in the workspace. `MEMORY.md` is loaded at the start of every session; daily notes in `memory/YYYY-MM-DD.md` are searched on demand. Edit or delete these files to change what the agent remembers.
- The search index (full text plus vectors) lives in the per-agent SQLite database and reindexes when the files change.
- Vector search needs an embeddings provider. The default is OpenAI (`OPENAI_API_KEY`); with only an Anthropic key you get keyword search. Settings live under `memory.search` (`provider`, `model`, `sources`); this repo does not pin them.
- Commands: `rune openclaw::cli memory status`, `rune openclaw::cli memory search "query"`, `rune openclaw::cli memory index --force`, `rune openclaw::cli memory reset`. Never delete the SQLite files to reset memory.

## Plugins and skills

- Plugins: `rune openclaw::cli plugins list|search|install <pkg>|enable <id>|disable <id>|update --all`, or the Control UI. Changes apply without a restart and persist in `data/config`. Per-plugin settings live under `plugins.entries.<id>`.
- Skills: `rune openclaw::cli skills search|install @owner/<slug>|list`. Installs go to the workspace `skills/`; add `--global` for `data/config/skills/`.
- OS packages a plugin or skill needs (`apt install ...`) do not survive container recreation. They need a custom image set in `OPENCLAW_IMAGE`.

## Shell and management

| Task | Does |
|------|------|
| `rune openclaw::shell` | bash in the gateway container; `openclaw` is on PATH |
| `rune openclaw::tui` | terminal chat; works when Tailscale is down |
| `rune openclaw::status` | gateway, channels, sessions |
| `rune openclaw::doctor` | diagnose config and state |
| `rune openclaw::configure` | interactive wizard: workspace, web, channels, plugins, skills; do not enable Tailscale there |
| `rune openclaw::cli <args>` | any `openclaw` command, e.g. `channels list`, `agents list`, `logs` |
| `rune openclaw::dashboard` | the gateway's own Control UI link (loopback); remotely use `rune openclaw::url` |
| `rune openclaw::devices`, `rune openclaw::approve <id>` | pair browsers and apps |
| `rune openclaw::audit` | security audit |
| `rune openclaw::backup` | verified archive into `backups/` |
| `rune openclaw::url` | tailnet URL |
| `rune openclaw::onboard` | change model provider or auth, add channels (interactive) |
| `rune openclaw::up`, `down`, `restart`, `logs` | lifecycle |
| `rune openclaw::update` | verified backup, pull, recreate, `doctor --json` |

Without rune: `cd stacks/openclaw`, then `docker compose run --rm openclaw-cli <command>` or `docker compose exec openclaw-gateway bash`.

## Configuration

OpenClaw owns `data/config/openclaw.json` (JSON5 with a strict schema). An invalid direct edit stops the next start; hot reload skips invalid edits and keeps the last good config. Change it with:

- the Control UI **Config** tab (form or raw JSON),
- `rune openclaw::configure` (settings) or `rune openclaw::onboard` (model provider and auth),
- `rune openclaw::cli config get|set|unset <path> [value]` and `rune openclaw::cli config validate`.

Most changes apply live (agents, models, channels, tools, skills, plugins, logging); gateway network settings restart the gateway automatically. OpenClaw rewrites the file as plain JSON, so comments do not survive.

API keys live only in `.env`: onboarding runs with `--secret-input-mode ref`, so auth profiles store references such as `{source: "env", id: "ANTHROPIC_API_KEY"}`. To rotate a key, edit `.env` and run `rune openclaw::up` (`.env` changes need a recreate, not a restart). Other secrets can be referenced from config as `${VAR}`.

Do not turn on Tailscale in `configure`: the sidecar already serves the gateway, and the image has no `tailscale` CLI, so a managed Serve or Funnel would keep the gateway from starting. `init.py` pins it off.

`init.py` re-applies these keys on every run. Change them in `component.toml`, not by hand:

| Key | Value | Why |
|-----|-------|-----|
| `gateway.mode` | `local` | Docker setup |
| `gateway.bind` | `loopback` | only the sidecar's Serve can reach the gateway |
| `gateway.trustedProxies` | `["127.0.0.1"]` | accept Serve's forwarded requests |
| `gateway.tailscale.mode` | `off` | the sidecar owns Serve |
| `gateway.auth.rateLimit` | 10 attempts per minute, 5 minute lockout | brute-force protection |
| `logging.file` | `/home/node/.openclaw/logs/openclaw.log` | logs survive container recreation (default is `/tmp`) |

With `logging.file` set, logs rotate at 100 MB and keep five archives (about 600 MB). Lower it with `rune openclaw::cli config set logging.maxFileBytes <bytes>`.

## Updates

`rune openclaw::update` writes a verified backup, pulls the tags in `.env`, recreates the stack and runs `doctor --json`. On start the image migrates config and state itself (`doctor --fix`) and keeps `*.pre-startup-migration-*.bak` files; keep them with the backup, because a rollback needs the old image and the matching state.

Plain version tags such as `2026.9.8` never change, so they get no OS security refreshes. Bump `OPENCLAW_IMAGE` to a newer release (or a dated `-rYYYYMMDD` tag when one exists) and run `rune openclaw::update`.

A later OpenClaw release (already on upstream `main`) needs `pid: "service:openclaw-gateway"` on the `openclaw-cli` service; without it `tui`, `status` and `backup` refuse to run. Add it when you upgrade past 2026.9.8.

## Optional features

- **Browser control** (`openclaw browser`, the Control UI browser panel): Chromium must be inside the gateway image. Set `OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.9.8-browser` and run `rune openclaw::update`. Do not install Chromium at runtime; it does not survive recreation.
- **Sandbox** (agent tools in separate containers): needs a custom image with the Docker CLI, the sandbox image and the Docker socket; see the comment in `compose.yaml`.
- **Gmail webhooks** (`openclaw webhooks gmail`): Pub/Sub needs a public HTTPS endpoint (Tailscale Funnel on its own port in `tailscale/serve.json`, routed only to the watcher on `127.0.0.1:8788`), a custom image with `gog`, and a working sandbox. Plain `/hooks` endpoints already work for callers on the tailnet with a dedicated hook token.

## Troubleshooting

- **`init.py` fails after `up --wait`, `tailscale` unhealthy:** the node is not logged in. Set `TS_AUTHKEY` in `stacks/openclaw/.env` (or open the login URL from `docker compose logs tailscale`), then rerun `python3 init.py openclaw`.
- **URL does not load:** `rune openclaw::url`. "no HTTPS name" means the node is not logged in (`docker compose logs tailscale`) or MagicDNS and HTTPS certificates are off in the admin console.
- **Gateway lost its network after the sidecar restarted on its own:** `rune openclaw::restart` recreates the gateway in the sidecar's current namespace.
- **`proxy_attribution_required`:** `gateway.trustedProxies` is missing; rerun `python3 init.py openclaw`.
- **`blocked plugin candidate: suspicious ownership`:** files not owned by uid 1000; rerun `python3 init.py openclaw`, which fixes ownership.
- **Gateway exits with code 78 after an upgrade:** a migration needs repair. `docker compose stop openclaw-gateway`, then `docker compose run --rm --entrypoint node openclaw-gateway dist/index.js doctor --fix`, then `rune openclaw::up`.
- **Headless onboarding fails (`init.py --yes`):** set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` in `stacks/openclaw/.env` and rerun, or run `rune openclaw::onboard`.
- **Logs:** `data/config/logs/openclaw.log`, or `rune openclaw::logs` for container output.
