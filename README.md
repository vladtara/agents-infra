# agents-infra

Run and manage AI tools in Docker on a single Linux VM. The repo is cloned on the VM, every tool is a Docker Compose stack, and [Dockge](https://github.com/louislam/dockge) gives a web UI over all of them. Git stays the source of truth: edits made in Dockge show up in `git diff`.

| Component | What it is | Local URL | Tailnet URL |
|-----------|------------|-----------|-------------|
| `dockge` | Web UI for the stacks in `stacks/` | http://127.0.0.1:5001 | `https://<vm>.<tailnet>.ts.net:8443` |
| `openclaw` | [OpenClaw](https://github.com/openclaw/openclaw) personal AI assistant gateway | http://127.0.0.1:18789 | `https://<vm>.<tailnet>.ts.net` |

## Quick start

On a fresh Ubuntu 24.04+ or Debian 12+ VM (needs Python 3.11+ and sudo):

```sh
sudo git clone https://github.com/vladtara/agents-infra.git /opt/agents-infra
sudo chown -R "$USER": /opt/agents-infra
cd /opt/agents-infra
python3 init.py
```

`init.py` asks before installing anything on the host, then:

1. installs Docker Engine + compose plugin (Docker's apt repo), Tailscale and [rune](https://github.com/rune-task-runner/rune) if missing
2. writes each component's `.env` from `.env.example`, generating secrets and asking for API keys
3. pulls images, runs first-time setup (OpenClaw onboarding), starts the stacks
4. publishes the UIs on your tailnet with `tailscale serve`

It is safe to rerun: existing `.env` values and secrets are kept, one-time steps are skipped.

```sh
python3 init.py openclaw     # install or update one component
python3 init.py --list       # components and their state
python3 init.py --yes        # no questions; interactive steps are skipped
python3 init.py --skip-host  # components only, no host checks
```

If Docker was just installed, log out and back in (or run `newgrp docker`) and rerun `init.py`.

## Access

Every port is published on `127.0.0.1` only. Docker-published ports bypass UFW, so nothing is exposed even with the firewall off.

- **Tailscale:** `init.py` runs `tailscale serve`, which gives HTTPS on your tailnet. Serve needs MagicDNS and HTTPS certificates enabled in the Tailscale admin console; if they are off, `tailscale serve` prints a link to enable them. Rerun `python3 init.py` after the first `tailscale up` so OpenClaw learns its tailnet origin.
- **SSH tunnel** (always works): `ssh -N -L 5001:127.0.0.1:5001 -L 18789:127.0.0.1:18789 user@vm`, then open the local URLs.

First visits:

- Dockge asks you to create an admin account.
- OpenClaw: `rune openclaw::dashboard` prints the Control UI link with its token. A new browser shows up as a pending device: `rune openclaw::devices`, then `rune openclaw::approve <id>`.

## Daily tasks

`rune --list` shows everything. Main ones:

```sh
rune install [components...]   # same as python3 init.py
rune list                      # component state
rune ps                        # all compose projects on the host
rune validate                  # docker compose config for every component
rune update                    # git pull, then pull images and recreate installed components
rune test                      # installer unit tests

rune dockge::up | down | logs | update
rune openclaw::up | down | logs | update
rune openclaw::onboard         # rerun onboarding (model provider, channels)
rune openclaw::cli channels list
rune openclaw::audit           # openclaw security audit
```

Without rune, use `docker compose` inside a component folder; the project name is the folder name, the same one Dockge uses.

## Layout

```
init.py            entry point (Python stdlib only)
installer/         install logic: host prerequisites, manifests, .env rendering
Runefile           root rune tasks; each component adds a module
dockge/            Dockge itself, outside stacks/ so it never manages itself
stacks/            DOCKGE_STACKS_DIR: one folder per tool
  openclaw/
tests/             python3 -m unittest discover -s tests
```

Each component folder holds:

| File | Purpose | In git |
|------|---------|--------|
| `compose.yaml` | the stack | yes |
| `.env.example` | every setting, with defaults and comments | yes |
| `component.toml` | how `init.py` installs it | yes |
| `<name>.rune` | component tasks (optional) | yes |
| `.env` | real settings and secrets, mode 0600 | no |
| `data/` | persistent state | no |

## Adding a tool

1. Create `stacks/<name>/` (lowercase, digits, `-`, `_`) with `compose.yaml` and `.env.example`. Bind ports to `${BIND_IP:-127.0.0.1}` and keep state in relative `./data/...` mounts.
2. Add `component.toml`:

   ```toml
   description = "What it is"
   order = 30                      # install order; dockge = 10, openclaw = 20
   dirs = ["data"]                 # created before the stack starts

   [env]
   generate = ["APP_SECRET"]       # random 64-hex value when empty
   ask = ["SOME_API_KEY"]          # asked once; blank keeps the default
   set = { APP_URL = "https://{ts_hostname}" }  # rewritten on every run

   [[setup]]                       # `docker compose <run>`, in order, before `up -d`
   run = "run --rm app migrate"
   once = "data/.migrated"         # skip while this marker exists; created on success
   interactive = false             # true: skipped with --yes

   [tailscale]
   https_port = 8444
   target = "http://127.0.0.1:8080"
   ```

   Placeholders in `set` and `run`: `{repo_dir}`, `{stacks_dir}`, `{uid}`, `{gid}`, `{ts_hostname}` (`localhost` until Tailscale is up). Every key named in `[env]` must exist in `.env.example`. If a setup step ran while the stack was already running, `init.py` restarts it.
3. Optionally add `stacks/<name>/<name>.rune` (use `[working-directory("stacks/<name>")]` on each task) and a `mod <name> "stacks/<name>/<name>.rune"` line in `Runefile`.
4. `rune validate && python3 init.py <name>`

Stacks created from the Dockge UI work too. They just have no `component.toml`, so `init.py` ignores them.

## Security notes

- Dockge mounts the Docker socket, which is root-equivalent on the host. Keep it on loopback or the tailnet, and leave its console disabled (the default).
- OpenClaw's sandbox (agents in sibling containers) is off. Enabling it also needs the Docker socket; see the comments in `stacks/openclaw/compose.yaml`.
- OpenClaw keeps OAuth tokens in plain SQLite under `stacks/openclaw/data/`. Treat that folder and its backups as credentials.
- `init.py` pins OpenClaw's `gateway.mode`, `gateway.bind`, `gateway.controlUi.allowedOrigins` and `gateway.auth.rateLimit` on every run; change them in `stacks/openclaw/component.toml`, not by hand.
- Dockge 1.5.0 (`louislam/dockge:1`) ignores `PUID`/`PGID`, so stacks *created* in the UI are owned by root until a release ships that support. Editing existing files keeps their owner. `DOCKGE_IMAGE=louislam/dockge:nightly` in `dockge/.env` has it today.
