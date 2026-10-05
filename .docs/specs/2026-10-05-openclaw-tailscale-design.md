# OpenClaw operations and containerized Tailscale

Date: 2026-10-05
Status: approved in brainstorming, pending spec review

## Context

agents-infra installs Dockge and OpenClaw on one VM. Today `init.py` installs Tailscale on the host and runs `tailscale serve` for both UIs, and OpenClaw's gateway binds `lan` inside its container with port 18789 published on host loopback.

This change:

1. Moves Tailscale into containers: a VM-level node for SSH and Dockge, and a dedicated sidecar inside OpenClaw's compose file.
2. Defines how OpenClaw is operated: where data lives, memory, plugins and skills, shell and management access, config ownership, backups and logs, and documents it.

## Decisions

| Topic | Decision |
|-------|----------|
| VM access | A Tailscale container with host networking makes the VM a tailnet node. SSH goes to the VM's own sshd over the tailnet. Dockge is served on the VM node with Tailscale Serve. |
| VM node placement | Top-level `tailscale/`, next to `dockge/`, outside Dockge's stacks dir so it cannot be stopped from the Dockge UI. |
| OpenClaw access | Sidecar `tailscale` service in `stacks/openclaw/compose.yaml` owns the network namespace. Gateway binds loopback. No host ports. |
| Fallback when Tailscale is down | `rune openclaw::tui` / `openclaw::cli` on the VM. No SSH tunnel to the gateway. |
| openclaw.json ownership | OpenClaw owns it (not in git). The repo pins only infra keys on every `init.py` run. |
| Tailscale networking mode | Kernel mode (`TS_USERSPACE=false`) for both nodes. See "Why kernel mode". |

## Architecture

```
tailnet
├── agents-vm   tailscale/              host network, kernel mode
│   ├── ssh user@agents-vm               -> VM sshd :22
│   └── https://agents-vm.<tn>.ts.net    -> Serve -> http://127.0.0.1:5001 (Dockge)
└── openclaw    stacks/openclaw/ "tailscale" sidecar, kernel mode
    └── https://openclaw.<tn>.ts.net     -> Serve -> http://127.0.0.1:18789 (gateway, same netns)
```

Install order: `tailscale` (5), `dockge` (10), `openclaw` (20).

### Why kernel mode

- Userspace mode delivers every inbound tailnet TCP connection to `127.0.0.1:<port>` in the node's namespace.
- In the VM node (host network) that would expose every host service bound to loopback to the tailnet.
- In the OpenClaw sidecar, a peer connecting to `openclaw:18789` directly would reach the gateway as a loopback client without forwarded headers. OpenClaw treats that as a local direct request, which is trusted for device pairing.
- With kernel mode, traffic arrives on `tailscale0` with the peer's 100.x source address, so loopback-bound services are only reachable through Serve.

## Component: `tailscale/` (VM node)

Files: `compose.yaml`, `.env.example`, `component.toml`, `tailscale.rune`, `serve/serve.json`.

`compose.yaml`:
- image `${TS_IMAGE:-tailscale/tailscale:v1.102.5}`, `network_mode: host`, `restart: unless-stopped`
- env: `TS_AUTHKEY=${TS_AUTHKEY:-}`, `TS_HOSTNAME=${TS_HOSTNAME:-agents-vm}`, `TS_STATE_DIR=/var/lib/tailscale`, `TS_USERSPACE=false`, `TS_AUTH_ONCE=true`, `TS_SERVE_CONFIG=/config/serve.json`, `TS_ENABLE_HEALTH_CHECK=true`, `TS_LOCAL_ADDR_PORT=127.0.0.1:9002`
- volumes: `./data:/var/lib/tailscale`, `./serve:/config:ro` (directory mount so edits are detected)
- `devices: /dev/net/tun`, `cap_add: NET_ADMIN, NET_RAW`
- healthcheck: `wget --spider -q http://127.0.0.1:9002/healthz`

`serve/serve.json` (git-tracked):

```json
{
  "TCP": { "443": { "HTTPS": true } },
  "Web": { "${TS_CERT_DOMAIN}:443": { "Handlers": { "/": { "Proxy": "http://127.0.0.1:5001" } } } }
}
```

`component.toml`: `order = 5`, `dirs = ["data"]`, `[env] ask = ["TS_AUTHKEY"]`, `[tailscale] service = "tailscale"`.

`tailscale.rune`: `up`, `down` (confirm), `logs`, `status`.

Dockge keeps `127.0.0.1:5001` and loses its `[tailscale]` section.

## Component: `stacks/openclaw/` changes

`compose.yaml`:
- New `tailscale` service: same image and env as the VM node except no host networking, `TS_HOSTNAME=${TS_HOSTNAME:-openclaw}`, state `./data/tailscale:/var/lib/tailscale`, Serve config `./tailscale:/config:ro`, `extra_hosts: host.docker.internal:host-gateway` (moved here; not allowed on namespace-sharing containers), `devices: /dev/net/tun`, `cap_add: NET_ADMIN`.
- `openclaw-gateway`: `network_mode: service:tailscale`, no `ports`, no `extra_hosts`, command `--bind loopback`, plus `./backups:/home/node/backups`. Hardening, healthcheck and other mounts unchanged.
- `openclaw-cli`: `network_mode: service:tailscale` (direct, not via the gateway, so it starts even when the gateway is down), plus `./backups:/home/node/backups`.
- Both OpenClaw services set `TS_AUTHKEY: ""` in `environment:`. They load the whole `.env` through `env_file`, and `environment:` wins, so the Tailscale auth key never reaches the gateway process or the agent's tools.
- `tailscale/serve.json` (git-tracked): 443 -> `http://127.0.0.1:18789`.

`.env.example`: add `TS_AUTHKEY=`, `TS_HOSTNAME=openclaw`; remove `BIND_IP`.

`component.toml`:
- `dirs` adds `data/tailscale` and `backups`
- `[env] ask` adds `TS_AUTHKEY`
- `[tailscale] service = "tailscale"`
- config-set step pins: `gateway.mode=local`, `gateway.bind=loopback`, `gateway.trustedProxies=["127.0.0.1"]`, `gateway.auth.rateLimit={maxAttempts:10, windowMs:60000, lockoutMs:300000}`, `logging.file` under `/home/node/.openclaw/logs/`. The localhost `allowedOrigins` list is dropped: `.ts.net` same-origin UI loads are accepted by default.
- onboarding step unchanged; the ownership step also covers `/home/node/backups`.

`openclaw.rune` adds: `restart` (force-recreate the gateway), `shell` (`exec openclaw-gateway bash`), `tui`, `status`, `doctor`, `configure`, `backup` (`backup create --output /home/node/backups --verify`), `url` (prints tailnet URL). Existing tasks stay. Setup steps and `onboard` drop `--no-deps`: one-off containers join the sidecar's namespace, so the sidecar must be running.

## OpenClaw operating model (content for `stacks/openclaw/README.md`)

**Data** (`stacks/openclaw/data/`, gitignored, uid 1000):

| Host | Container | Contents |
|------|-----------|----------|
| `data/config/` | `~/.openclaw` | `openclaw.json` (JSON5); `state/openclaw.sqlite` (devices, pairing, cron, plugin state, shared auth); `agents/<id>/agent/openclaw-agent.sqlite` (sessions, transcripts, memory index); `credentials/`; plugins in `npm/`, `git/`, `extensions/`; global skills in `skills/`; `logs/` |
| `data/workspace/` | `~/.openclaw/workspace` | `AGENTS.md`, `SOUL.md`, `USER.md`, `IDENTITY.md`, `MEMORY.md`, `memory/YYYY-MM-DD.md`, `DREAMS.md`, workspace `skills/` |
| `data/auth/` | `~/.config/openclaw` | key for legacy encrypted OAuth credentials |
| `data/tailscale/` | sidecar `/var/lib/tailscale` | tailnet node identity |
| `backups/` | gateway + cli `/home/node/backups` | `openclaw backup create` output |

Treat `data/config`, `data/auth` and `backups/` as credentials. Never copy live SQLite files; use `rune openclaw::backup` or stop the stack first.

**Memory**: Markdown files in the workspace are the memory. `MEMORY.md` loads every session; `memory/*.md` are indexed (FTS + vectors) into the per-agent SQLite DB. Vector search needs an embeddings provider (default `openai`); without one only keyword search works. Config lives under `memory.search` (not pinned by the repo). Commands: `rune openclaw::cli memory status|search|index|reset`. Never delete the SQLite files to reset memory.

**Plugins and skills**: `rune openclaw::cli plugins list|install|enable|disable|update`, `skills search|install|list`, or the Control UI. Changes apply live and persist under `data/config` (and workspace `skills/`). OS packages needed by a plugin do not persist; they require a custom image.

**Shell and management**: `rune openclaw::shell`, `tui`, `status`, `doctor`, `configure`, `cli <args>`, `devices`, `approve <id>`, `dashboard`, `audit`, `backup`, `url`.

**Config**: change via Control UI Config tab, `rune openclaw::configure`, or `rune openclaw::cli config set ...`. OpenClaw rewrites `openclaw.json` as plain JSON (comments are dropped). The infra keys listed above are reset by every `init.py` run; change them in `component.toml`.

**First login**: open `https://openclaw.<tn>.ts.net`, paste the gateway token (`rune openclaw::dashboard` prints a link), then approve the browser with `rune openclaw::devices` and `rune openclaw::approve <id>`.

## Installer changes

`components.py`:
- discovery candidates: `tailscale/`, `dockge/`, `stacks/*/`
- `[tailscale]` schema becomes `{ service: str }` (required string); `https_port` / `target` removed

`cli.py`:
- context vars: `repo_dir`, `stacks_dir`, `uid`, `gid` (`ts_hostname` removed)
- secret prompt answers cached per run (same key asked by several components is entered once)
- `up -d --wait --wait-timeout 180`
- after `up`, if `[tailscale] service` is set: `docker compose exec -T <service> tailscale status --json`; print `https://<CertDomains[0]>/`; if empty, warn to enable MagicDNS and HTTPS certificates
- host `tailscale serve` call removed
- local URL printing removed (tailnet URL replaces it)

`host.py`:
- remove Tailscale install, `tailscale up`, status parsing
- `ensure(..., tailscale_component: bool)`: when true and `systemctl is-active --quiet tailscaled` succeeds, raise `HostError` with `sudo systemctl disable --now tailscaled`. Uses systemd state, not process lists, so the container's own `tailscaled` is not flagged.

`Runefile`: `mod tailscale "tailscale/tailscale.rune"`.

`.gitignore`: add `tailscale/data/`, `stacks/*/backups/`.

Root `README.md`: Tailscale prerequisites (reusable pre-approved auth key, MagicDNS + HTTPS enabled, disable key expiry or use a tagged key), access table, SSH over tailnet, closing public SSH is optional and left to the user, sidecar restart caveat.

## Caveats

- If the sidecar restarts outside Compose (crash), the gateway loses networking. Fix: `rune openclaw::restart` (recreates the gateway in the sidecar's current namespace). Documented.
- `TS_AUTH_ONCE=true` means `TS_EXTRA_ARGS` only apply on first login. None are used.
- Two VMs on one tailnet with default hostnames collide; Tailscale appends a suffix. `TS_HOSTNAME` is configurable in `.env`.
- Trusting `127.0.0.1` as a proxy lets any process in the OpenClaw namespace supply forwarded client IPs; token auth stays on.

## Verify during implementation

- `logging.file` key name and that a path under `/home/node/.openclaw/logs/` works.
- `openclaw backup create` flags (`--output`, `--verify`).
- `docker compose up --wait` ignores the profiled `openclaw-cli` service.
- Gateway healthcheck passes with `--bind loopback`.

## Testing

1. Unit tests first (TDD): `[tailscale] service` validation; discovery order with `tailscale/`; shared secret asked once; URL printed from mocked `status --json`; warning on empty `CertDomains`; `--wait` passed to `up`; no host Serve; host `tailscaled` conflict check.
2. `rune analyze`, `rune validate` (all three compose files).
3. Local smoke on a scratchpad copy without an auth key: sidecar waits for login; gateway healthy inside the namespace; no host listeners on 18789; pinned config accepted; logs in `data/config/logs/`.
4. Optional: with a throwaway auth key from the user, the OpenClaw sidecar end to end. The host-network VM node is not run on the developer laptop.
5. Real end to end on the VM by the user.

## Out of scope

- Building a custom OpenClaw image with extra OS packages.
- OpenClaw's managed Tailscale mode and `allowTailscale` identity auth (needs the tailscale CLI in the gateway image).
- Pinning memory, model or channel settings.
- Firewall changes on the VM.

## Sources

- OpenClaw docs at tag v2026.9.8: `docs/help/faq/where-things-live-on-disk.md`, `docs/concepts/memory*.md`, `docs/reference/memory-config.md`, `docs/cli/{plugins,skills,memory,config,tui,backup}.md`, `docs/gateway/{tailscale,configuration,pairing,config-gateway}.md`, `docs/gateway/configuration/hot-reload.md`, `docs/gateway/security/network-exposure.md`, `docs/install/docker/*.md`
- Tailscale: docs/features/containers/docker (docker-params, how-to), docs/features/tailscale-serve, docs/features/access-control/{auth-keys,key-expiry}, `cmd/containerboot` source, tailscale-dev/ScaleTail, issues #5215, #16987, #20728
- Docker: moby `daemon/oci_linux.go` (namespace chaining), compose-go `loader/normalize.go` (implicit depends_on)
