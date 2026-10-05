# agents-infra

Run and manage AI tools in Docker on a single Linux VM. The repo is cloned on the VM, every tool is a Docker Compose stack, and [Dockge](https://github.com/louislam/dockge) gives a web UI over them. Git stays the source of truth: edits made in Dockge show up in `git diff`. Access goes through [Tailscale](https://tailscale.com) running in containers; the UIs listen on loopback only and are published on the tailnet.

| Component | What it is | Tailnet address |
|-----------|------------|-----------------|
| `tailscale` | The VM's own tailnet node: SSH to the VM, serves Dockge | `ssh <user>@agents-vm` |
| `dockge` | Web UI for the stacks in `stacks/` | `https://agents-vm.<tailnet>.ts.net` |
| `openclaw` | [OpenClaw](https://github.com/openclaw/openclaw) AI assistant gateway on its own tailnet node ([guide](stacks/openclaw/README.md)) | `https://openclaw.<tailnet>.ts.net` |

## Before you start

> [!IMPORTANT]
> **Turn on HTTPS certificates in your tailnet.** Every URL here (Dockge, OpenClaw) is published by Tailscale Serve over HTTPS. With HTTPS certificates off, both nodes still join the tailnet and show up in the admin console, but Serve is never applied and the URLs do not load (`tailscale serve status` says "No serve config").
>
> [Admin console > DNS](https://login.tailscale.com/admin/dns): enable **MagicDNS**, then **HTTPS Certificates > Enable HTTPS**. If you turn it on after installing, nothing needs to be reinstalled or restarted: the nodes pick it up within a minute.

In the [Tailscale admin console](https://login.tailscale.com/admin):

1. **DNS:** MagicDNS and HTTPS certificates on (see the box above).
2. **Settings > Keys:** generate an auth key that is **reusable** and **pre-approved**. `init.py` asks for it once and uses it for both nodes (`agents-vm`, `openclaw`). It is only needed for the first login.
3. After the first install, open **Machines** and choose **Disable key expiry** for both nodes, or use a tagged auth key (expiry is off for tagged nodes).

If the VM already runs Tailscale from a package, disable it first (`sudo systemctl disable --now tailscaled`): the containerized node uses the same `tailscale0` interface, and `init.py` refuses to continue while it runs. Do this over public SSH or the cloud console, not over that tailnet: it ends the session.

## Quick start

On a fresh Ubuntu 24.04+ or Debian 12+ VM (Python 3.11+ and sudo):

```sh
sudo git clone https://github.com/vladtara/agents-infra.git /opt/agents-infra
sudo chown -R "$USER": /opt/agents-infra
cd /opt/agents-infra
python3 init.py
```

`init.py` asks before installing anything on the host, then:

1. installs Docker Engine + compose plugin (Docker's apt repo) and [rune](https://github.com/rune-task-runner/rune) if missing
2. writes each component's `.env` from `.env.example`, generating secrets and asking for the Tailscale auth key and API keys (each asked once per run)
3. pulls images, runs first-time setup (OpenClaw onboarding), starts the stacks and waits for their health checks
4. prints each tailnet URL. If something blocks access, the run ends with an **Action needed** list, for example "HTTPS certificates are off in your tailnet" or "node is not logged in".

It is safe to rerun: existing `.env` values and secrets are kept, one-time steps are skipped.

```sh
python3 init.py openclaw     # install or update one component
python3 init.py --list       # components and their state
python3 init.py --yes        # no questions; OpenClaw onboards from the model key in its .env
python3 init.py --skip-host  # components only, no host checks
```

If Docker was just installed, log out and back in (or run `newgrp docker`) and rerun `init.py`.

If a `tailscale` container stays unhealthy, its node is not logged in: set `TS_AUTHKEY` in that component's `.env` (`tailscale/.env` or `stacks/openclaw/.env`) and rerun `init.py`.

## Access

Every UI listens on loopback only and is published on the tailnet with Tailscale Serve (HTTPS). Tailscale runs in kernel mode, so loopback-only services are reachable from the tailnet only through Serve. Anything listening on all interfaces is reachable from the tailnet too: the VM's sshd, a port a stack publishes on `0.0.0.0`, or a listener started inside the OpenClaw namespace. Limit inbound traffic with a tailnet ACL, for example only `tcp:443` to the `openclaw` node.

- **SSH:** `ssh <user>@agents-vm`, the VM's own sshd over the tailnet. Closing public SSH in your cloud firewall is optional and up to you.
- **Dockge:** `https://agents-vm.<tailnet>.ts.net`. It asks you to create an admin account on first visit. Without Tailscale: `ssh -L 5001:127.0.0.1:5001 <user>@<public-ip>`, then http://127.0.0.1:5001.
- **OpenClaw:** `https://openclaw.<tailnet>.ts.net`. Without Tailscale there is no network path by design; use `rune openclaw::tui` on the VM. First login and operations: [stacks/openclaw/README.md](stacks/openclaw/README.md).

`rune tailscale::status` shows the VM node; `rune openclaw::url` prints OpenClaw's address.

### If a URL does not load

| Symptom | Fix |
|---------|-----|
| `init.py` says "HTTPS certificates are off in your tailnet", or `docker compose exec tailscale tailscale serve status` (in `tailscale/` or `stacks/openclaw/`) says "No serve config" | Enable HTTPS certificates at [admin console > DNS](https://login.tailscale.com/admin/dns). No restart needed; give it a minute. |
| `init.py` says "node is not logged in", or a `tailscale` container is unhealthy | Set `TS_AUTHKEY` in that component's `.env` and rerun `python3 init.py`. |
| The name does not resolve on your laptop | The laptop must be on the same tailnet with Tailscale DNS on: `tailscale status` there lists `agents-vm` and `openclaw`. |
| The first load hangs for a while | The certificate is issued on the first HTTPS request; allow up to a minute. |

## Daily tasks

`rune --list` shows everything. Main ones:

```sh
rune install [components...]   # same as python3 init.py
rune list                      # component state
rune ps                        # all compose projects on the host
rune validate                  # docker compose config for every component
rune update                    # git pull, then each installed component's update (Tailscale last; use tmux over tailnet SSH)
rune test                      # installer unit tests

rune tailscale::up | down | logs | status | update
rune dockge::up | down | logs | update
rune openclaw::up | down | restart | logs | update
rune openclaw::shell | tui | status | doctor | configure | onboard | backup | url
rune openclaw::cli channels list
```

Without rune, use `docker compose` inside a component folder; the project name is the folder name, the same one Dockge uses.

## Layout

```
init.py            entry point (Python stdlib only)
installer/         install logic: host prerequisites, manifests, .env rendering
Runefile           root rune tasks; each component adds a module
tailscale/         the VM's tailnet node, outside stacks/ so Dockge cannot stop it
dockge/            Dockge itself, outside stacks/ so it never manages itself
stacks/            DOCKGE_STACKS_DIR: one folder per tool
  openclaw/
tests/             python3 -m unittest discover -s tests
.docs/             design specs and implementation plans
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

1. Create `stacks/<name>/` (lowercase, digits, `-`, `_`) with `compose.yaml` and `.env.example`. Keep state in relative `./data/...` mounts and do not publish ports on public interfaces.
2. Add `component.toml`:

   ```toml
   description = "What it is"
   order = 30                      # install order; tailscale = 5, dockge = 10, openclaw = 20
   dirs = ["data"]                 # created before the stack starts

   [env]
   generate = ["APP_SECRET"]       # random 64-hex value when empty
   ask = ["SOME_API_KEY"]          # asked once per run; blank keeps the default
   set = { DATA_ROOT = "{stacks_dir}/<name>/data" }  # rewritten on every run

   [[setup]]                       # `docker compose <run>`, in order, before `up -d --wait`
   run = "run --rm app migrate"
   once = "data/.migrated"         # skip while this marker exists; created on success
   interactive = false             # true: skipped with --yes, unless headless is set
   # headless = "run -T --rm app migrate --yes"  # with interactive = true: used instead under --yes

   [tailscale]                     # optional: print the URL of this stack's Tailscale sidecar
   service = "tailscale"
   ```

   Placeholders in `set` and `run`: `{repo_dir}`, `{stacks_dir}`, `{uid}`, `{gid}`. Every key named in `[env]` must exist in `.env.example`. If a setup step ran while the stack was already running, `init.py` restarts it (except the sidecar).
3. To give the tool its own tailnet name, copy the `tailscale` service and the `tailscale/serve.json` folder from `stacks/openclaw/`, point `Proxy` at the app's port, set `network_mode: service:tailscale` on the app, add `TS_IMAGE`, `TS_AUTHKEY`, `TS_HOSTNAME` to `.env.example` and `TS_AUTHKEY` to `[env] ask`. If the app loads `.env` with `env_file`, set `TS_AUTHKEY: ""` in its `environment:`.
4. Optionally add `stacks/<name>/<name>.rune` (use `[working-directory("stacks/<name>")]` on each task) and a `mod <name> "stacks/<name>/<name>.rune"` line in `Runefile`.
5. `rune validate && python3 init.py <name>`

Stacks created from the Dockge UI work too. They have no `component.toml`, so `init.py` ignores them.

## Security notes

- Dockge mounts the Docker socket, which is root-equivalent on the host. It listens on loopback only and is reachable through the tailnet. Its web console is enabled (`DOCKGE_ENABLE_CONSOLE: true` in `dockge/compose.yaml`), so anyone who can log in to Dockge gets a shell with Docker socket access, which means root on the VM. Use a strong Dockge password and limit who can reach `agents-vm` port 443 with a tailnet ACL, or set it to `false` and run `rune dockge::up` to turn the console off.
- The Tailscale containers have `NET_ADMIN` and `/dev/net/tun`; the VM node shares the host network. Treat `tailscale/data/` and `stacks/*/data/tailscale/` as credentials (node keys).
- OpenClaw's sandbox (agents in sibling containers) is off. Enabling it needs the Docker socket; see the comments in `stacks/openclaw/compose.yaml`.
- OpenClaw keeps OAuth tokens in plain SQLite under `stacks/openclaw/data/`. Treat that folder and `stacks/openclaw/backups/` as credentials.
- Dockge 1.5.0 (`louislam/dockge:1`) ignores `PUID`/`PGID`, so stacks *created* in the UI are owned by root until a release ships that support. Editing existing files keeps their owner. `DOCKGE_IMAGE=louislam/dockge:nightly` in `dockge/.env` has it today.
