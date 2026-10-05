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
2. `rune openclaw::dashboard` prints a link that carries the gateway token. Open it on a device in your tailnet.
3. The browser shows up as a pending device: `rune openclaw::devices`, then `rune openclaw::approve <requestId>`.

If onboarding was skipped (`init.py --yes`), run `rune openclaw::onboard`.

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
| `rune openclaw::configure` | interactive wizard: models, channels, gateway |
| `rune openclaw::cli <args>` | any `openclaw` command, e.g. `channels list`, `agents list`, `logs` |
| `rune openclaw::dashboard` | Control UI link with token |
| `rune openclaw::devices`, `rune openclaw::approve <id>` | pair browsers and apps |
| `rune openclaw::audit` | security audit |
| `rune openclaw::backup` | verified archive into `backups/` |
| `rune openclaw::url` | tailnet URL |
| `rune openclaw::up`, `down`, `restart`, `logs`, `update` | lifecycle |

Without rune: `cd stacks/openclaw`, then `docker compose run --rm openclaw-cli <command>` or `docker compose exec openclaw-gateway bash`.

## Configuration

OpenClaw owns `data/config/openclaw.json` (JSON5 with a strict schema: unknown keys stop the gateway). Change it with:

- the Control UI **Config** tab (form or raw JSON),
- `rune openclaw::configure`,
- `rune openclaw::cli config get|set|unset <path> [value]` and `rune openclaw::cli config validate`.

Most changes apply live (agents, models, channels, tools, skills, plugins, logging); gateway network settings restart the gateway automatically. OpenClaw rewrites the file as plain JSON, so comments do not survive. Secrets belong in `.env` (passed to the gateway) and can be referenced from config as `${VAR}`.

`init.py` re-applies these keys on every run. Change them in `component.toml`, not by hand:

| Key | Value | Why |
|-----|-------|-----|
| `gateway.mode` | `local` | Docker setup |
| `gateway.bind` | `loopback` | only the sidecar's Serve can reach the gateway |
| `gateway.trustedProxies` | `["127.0.0.1"]` | accept Serve's forwarded requests |
| `gateway.auth.rateLimit` | 10 attempts per minute, 5 minute lockout | brute-force protection |
| `logging.file` | `/home/node/.openclaw/logs/openclaw.log` | logs survive container recreation (default is `/tmp`) |

## Troubleshooting

- **URL does not load:** `rune openclaw::url`. "no HTTPS name" means the node is not logged in (`docker compose logs tailscale`) or MagicDNS and HTTPS certificates are off in the admin console.
- **Gateway lost its network after the sidecar restarted on its own:** `rune openclaw::restart` recreates the gateway in the sidecar's current namespace.
- **`proxy_attribution_required`:** `gateway.trustedProxies` is missing; rerun `python3 init.py openclaw`.
- **`blocked plugin candidate: suspicious ownership`:** files not owned by uid 1000; rerun `python3 init.py openclaw`, which fixes ownership.
- **Logs:** `data/config/logs/openclaw.log`, or `rune openclaw::logs` for container output.
