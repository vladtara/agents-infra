# OpenClaw Team Workspaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run several git-managed teams of OpenClaw agents in the one gateway: `rune openclaw::team-add | team-sync | teams | team-remove`, plus an installer step that restores teams on a new VM.

**Architecture:** `stacks/openclaw/teams.py` (entry point) drives `stacks/openclaw/teamlib/` (Python stdlib). Git runs in a one-off `team-git` container that holds the deploy keys; OpenClaw config is read and written through `openclaw-cli`, because `data/config` belongs to uid 1000 and `openclaw.json` is mode 0600. Each team agent's merged settings go to `data/config/agents.d/<agent id>.json5`, included from `openclaw.json`; the gateway hot-reloads them. The installer learns a `script` setup step so `init.py` can run `teams.py apply`.

**Tech Stack:** Python 3.11+ stdlib (`unittest`, `tomllib`, `tarfile`, `subprocess`), git (host for tests, 2.39 in the image), Docker Compose v2, `ghcr.io/openclaw/openclaw:2026.10.3`, rune 0.6.0, `gh`/`glab` (optional, for deploy keys).

**Spec:** `.docs/specs/2026-10-14-openclaw-teams-design.md` (read the "Addendum" at the end: spike results and design changes found while building).

## Global Constraints

- Python 3.11+ standard library only; tests use `unittest` (`python3 -m unittest discover -s tests`). teamlib tests run real git in temp dirs and never need Docker.
- Light comments: module docstring, docstrings on public functions, inline comments only for a non-obvious "why".
- No em dashes in any file.
- Image pin: `ghcr.io/openclaw/openclaw:2026.10.3` (compose defaults and `.env.example`).
- Team and agent ids: `^[a-z0-9][a-z0-9-]*$`; agent id `<team>-<folder>`, at most 64 characters; team names must not start with another team's name plus `-`.
- Container paths: gateway and CLI see `/home/node/.openclaw` (config) and `/home/node/teams` (clones); `team-git` sees `/teams`, `/keys`, `/remotes`.
- Team files must not set `workspace`, `cwd`, `agentDir` or `default`; every workspace needs `SOUL.md` and `AGENTS.md`.
- Clone config: `pull.rebase=true`, `rebase.autoStash=true`, `user.name`/`user.email` from `teams.toml`. Sync commit message `sync: <vm> <YYYY-MM-DD HH:MM>`; rescue branches `rescue/<vm>/<YYYYMMDD-HHMMSS>` and `rescue/<vm>/<YYYYMMDD-HHMMSS>-settings`.
- Secret scan: `.env` values of keys `*_KEY|*_TOKEN|*_SECRET|*_PASSWORD` or listed in `[env] generate/ask`, 8+ characters; patterns `sk-ant-[A-Za-z0-9_-]{20,}`, `sk-[A-Za-z0-9]{20,}`, `ghp_[A-Za-z0-9]{36}`, `github_pat_[A-Za-z0-9_]{20,}`, `xox[abp]-[A-Za-z0-9-]{10,}`, `AKIA[0-9A-Z]{16}`, `-----BEGIN [A-Z ]*PRIVATE KEY-----`; files over 5 MB are left out.
- Plugins: `plugins install <spec>` without `--force` or `--accept-capabilities`; never uninstall.
- rune 0.6.0: module task bodies run from the repo root; `[working-directory("x")]` is relative to the repo root.
- Commits: only after the user approves committing. Before the first commit run `git config user.name` (expect `vladtara`) and `gh auth status`. No `Co-Authored-By` trailers, no mention of Claude or agents in messages.
- The spike is done (spec Addendum): keep `gateway.INLINE = False`; no fallback applies.

## Review Focus

1. A VM edit git never got (a file left out for holding a secret) that git also changed: git's version must go live without conflict markers, the edit must stay in `git stash`, and the next sync must work (Task 9 `test_skipped_file_changed_in_git_too`).
2. The gateway's startup doctor rewrote `main` to `{}` before the first `team-add`: switching to explicit ownership must keep `main` on `/home/node/.openclaw/workspace` (Task 7 `test_sole_agent_keeps_the_default_workspace`).
3. Someone saves in the Control UI between teams.py reading and writing `openclaw.json`: refuse, write nothing, ask to rerun (Task 7 `test_config_changed_meanwhile`).
4. An agent folder deleted in git while gitignored runtime files (`.openclaw/`) keep it on disk: the agent must still leave the gateway (Task 9 `test_agent_removed_in_git_leaves_the_gateway`).
5. Teams named `tech` and `tech-ops`: refused at `team-add`, because `tech-ops-x` would look like an agent of `tech` (Task 3 `test_new_team_names`).

---

### Task 1: OpenClaw 2026.10.3 and compose wiring for teams

Bump the image, share the gateway's PID namespace with `openclaw-cli` (required since 2026.10.1), mount the team clones into the gateway and the CLI, and add the `team-git` service.

**Files:**
- Modify: `stacks/openclaw/compose.yaml`
- Modify: `stacks/openclaw/.env.example`
- Test: `tests/test_components.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: compose service `team-git` (entrypoint `git`, profile `tools`, volumes `./data/teams:/teams`, `./data/team-keys:/keys`, `./data/team-remotes:/remotes`, no `env_file`, own network); mount `./data/teams:/home/node/teams` on `openclaw-gateway` and `openclaw-cli`; `pid: "service:openclaw-gateway"` on `openclaw-cli`.

- [ ] **Step 1: Write the failing test**

Add to `class RepoManifestTest` in `tests/test_components.py`, after `test_openclaw_onboarding_keeps_keys_in_env_and_has_headless_variant`:

```python
    def test_openclaw_compose_wires_teams(self):
        compose = (REPO / "stacks/openclaw/compose.yaml").read_text()
        self.assertIn('pid: "service:openclaw-gateway"', compose)
        self.assertEqual(compose.count("- ./data/teams:/home/node/teams"), 2)
        team_git = compose[compose.index("  team-git:"):]
        for line in ('profiles: ["tools"]', 'entrypoint: ["git"]', "- ./data/team-keys:/keys", "- ./data/team-remotes:/remotes"):
            self.assertIn(line, team_git)
        self.assertNotIn("env_file", team_git)
        self.assertNotIn("network_mode", team_git)
        self.assertIn("OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.10.3", (REPO / "stacks/openclaw/.env.example").read_text())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_components.RepoManifestTest.test_openclaw_compose_wires_teams -v`
Expected: FAIL (`'pid: "service:openclaw-gateway"' not found`).

- [ ] **Step 3: Change compose and .env.example**

Apply to `stacks/openclaw/compose.yaml` (team-git goes last, after `openclaw-cli`):

```diff
--- a/stacks/openclaw/compose.yaml
+++ b/stacks/openclaw/compose.yaml
@@ -1,4 +1,4 @@
-# Based on openclaw/openclaw docker-compose.yml (v2026.9.8), adapted for Dockge:
+# Based on openclaw/openclaw docker-compose.yml (v2026.10.3), adapted for Dockge:
 # prebuilt image, relative bind mounts, CLI behind a profile, and a Tailscale
 # sidecar that owns the network namespace. The gateway binds loopback and is
 # reachable only through the sidecar's Tailscale Serve (HTTPS).
@@ -36,7 +36,7 @@ services:
       start_period: 20s
 
   openclaw-gateway:
-    image: ${OPENCLAW_IMAGE:-ghcr.io/openclaw/openclaw:2026.9.8}
+    image: ${OPENCLAW_IMAGE:-ghcr.io/openclaw/openclaw:2026.10.3}
     network_mode: service:tailscale
     env_file:
       - path: .env
@@ -60,6 +60,7 @@ services:
       - ./data/workspace:/home/node/.openclaw/workspace
       - ./data/auth:/home/node/.config/openclaw
       - ./backups:/home/node/backups
+      - ./data/teams:/home/node/teams
       ## Sandbox isolation (agents.defaults.sandbox) is off. It needs a custom image
       ## built with OPENCLAW_INSTALL_DOCKER_CLI=1 (published images have no Docker
       ## CLI), the sandbox image, and the host Docker socket, which is
@@ -85,7 +86,7 @@ services:
   # One-off CLI: docker compose run --rm openclaw-cli <command>
   # The profile keeps Dockge's "Start" (up -d) from launching it.
   openclaw-cli:
-    image: ${OPENCLAW_IMAGE:-ghcr.io/openclaw/openclaw:2026.9.8}
+    image: ${OPENCLAW_IMAGE:-ghcr.io/openclaw/openclaw:2026.10.3}
     profiles: ["cli"]
     network_mode: service:tailscale
     env_file:
@@ -109,14 +110,37 @@ services:
       - ./data/workspace:/home/node/.openclaw/workspace
       - ./data/auth:/home/node/.config/openclaw
       - ./backups:/home/node/backups
+      - ./data/teams:/home/node/teams
     cap_drop:
       - NET_RAW
       - NET_ADMIN
     security_opt:
       - no-new-privileges:true
+    # 2026.10.1+ checks gateway ownership through the shared PID namespace.
+    pid: "service:openclaw-gateway"
     stdin_open: true
     tty: true
     init: true
     entrypoint: ["node", "dist/index.js"]
     depends_on:
       - openclaw-gateway
+
+  # One-off git for team repos (stacks/openclaw/teams.py, TEAMS.md). It holds
+  # the deploy keys, gets no .env and stays off the tailnet namespace.
+  team-git:
+    image: ${OPENCLAW_IMAGE:-ghcr.io/openclaw/openclaw:2026.10.3}
+    profiles: ["tools"]
+    entrypoint: ["git"]
+    working_dir: /teams
+    environment:
+      HOME: /home/node
+      GIT_TERMINAL_PROMPT: "0"
+    volumes:
+      - ./data/teams:/teams
+      - ./data/team-keys:/keys
+      - ./data/team-remotes:/remotes
+    cap_drop:
+      - NET_RAW
+      - NET_ADMIN
+    security_opt:
+      - no-new-privileges:true
```

Apply to `stacks/openclaw/.env.example`:

```diff
--- a/stacks/openclaw/.env.example
+++ b/stacks/openclaw/.env.example
@@ -1,5 +1,5 @@
 # Pin a version tag; moving tags (latest, main) are rebuilt weekly.
-OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.9.8
+OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.10.3
 
 # Gateway auth token. Generated by init.py; keep it secret.
 OPENCLAW_GATEWAY_TOKEN=
```

- [ ] **Step 4: Run tests and compose validation**

Run: `python3 -m unittest tests.test_components -v && rune validate`
Expected: all PASS; `ok  stacks/openclaw/`.

Run: `cd stacks/openclaw && docker compose --profile cli --profile tools config --format json | python3 -c 'import json,sys; s=json.load(sys.stdin)["services"]; print(s["openclaw-cli"]["pid"], s["team-git"].get("env_file"), s["team-git"].get("network_mode"))'`
Expected: `service:openclaw-gateway None None`

- [ ] **Step 5: Commit (only if the user approved commits)**

```bash
git add stacks/openclaw/compose.yaml stacks/openclaw/.env.example tests/test_components.py
git commit -m "feat: openclaw 2026.10.3 with team mounts, shared pid namespace and team-git service"
```

---

### Task 2: Installer `script` setup step

A `[[setup]]` step takes either `run` (compose arguments) or `script` (a Python file in the component folder plus arguments). Script steps run with `python3`, cwd set to the component folder and env `REPO_DIR`, `STACKS_DIR`, `COMPONENT_DIR`; stdout lines starting with `action: ` join the Action needed list; a non-zero exit fails the component.

**Files:**
- Modify: `installer/components.py`
- Modify: `installer/cli.py`
- Test: `tests/test_components.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `components.expand`, `components._check_relative`, `cli.install`, `shell.CommandError`.
- Produces:
  - `components.Step(run: str | None = None, script: str | None = None, once=None, interactive=False, headless=None, note=None)`
  - `components.STEP_KEYS` gains `"script"`
  - `cli.ACTION_PREFIX = "action: "`
  - `cli.run_script(component: Component, script: str, context: dict[str, str]) -> list[str]` (returns action texts without the prefix)
  - `cli.install(...)` now returns script actions followed by tailnet problems.

- [ ] **Step 1: Write the failing component tests**

Add to `class LoadTest` in `tests/test_components.py`, after `test_step_note`:

```python
    def test_script_step(self):
        path = self.add("stacks/sc", 'description = "x"\n[[setup]]\nscript = "tool.py apply --all"\nonce = "data/.done"\n', env_example=None)
        (path / "tool.py").write_text("")
        step = components.load(path).setup[0]
        self.assertEqual((step.run, step.script, step.once), (None, "tool.py apply --all", "data/.done"))

    def test_rejects_bad_script_steps(self):
        self.assert_invalid('description = "x"\n[[setup]]\nonce = "x"\n', "exactly one of 'run' or 'script'")
        self.assert_invalid('description = "x"\n[[setup]]\nrun = "a"\nscript = "b.py"\n', "exactly one of 'run' or 'script'")
        self.assert_invalid('description = "x"\n[[setup]]\nscript = ""\n', "setup.script")
        self.assert_invalid('description = "x"\n[[setup]]\nscript = "missing.py"\n', "not found: missing.py")
        self.assert_invalid('description = "x"\n[[setup]]\nscript = "../x.py"\n', "setup.script")
        self.assert_invalid('description = "x"\n[[setup]]\nscript = "a.py"\ninteractive = true\n', "only apply to 'run'")
```

- [ ] **Step 2: Write the failing CLI tests**

Apply to `tests/test_cli.py` (adds `SCRIPT_TOML`, `TOOL_PY`, moves `calls_for` from `InstallTest` up to `CliCase`, adds `ScriptStepTest`):

```diff
--- a/tests/test_cli.py
+++ b/tests/test_cli.py
@@ -55,6 +55,27 @@ interactive = true
 headless = "run -T --rm hl onboard --non-interactive"
 """
 
+SCRIPT_TOML = """\
+description = "Scripted"
+order = 50
+
+[[setup]]
+run = "run --rm sc chown"
+
+[[setup]]
+script = "tool.py apply {uid}"
+"""
+
+TOOL_PY = """\
+import os, sys
+from pathlib import Path
+print("args", sys.argv[1:])
+print("cwd", Path.cwd() == Path(os.environ["COMPONENT_DIR"]))
+print("repo", os.environ["REPO_DIR"], "stacks", os.environ["STACKS_DIR"])
+print("action: team tech: add the deploy key")
+sys.exit(int(os.environ.get("TOOL_EXIT", "0")))
+"""
+
 UP = ("up", "-d", "--wait", "--wait-timeout", "180")
 
 
@@ -87,6 +108,9 @@ class CliCase(unittest.TestCase):
     def compose_calls(self):
         return [call.args[1:] for call in self.compose.call_args_list if not call.kwargs.get("capture")]
 
+    def calls_for(self, path):
+        return [c.args[1:] for c in self.compose.call_args_list if c.args[0] == path and not c.kwargs.get("capture")]
+
     def add_component(self, rel, toml, env_example="API_KEY=\n"):
         path = self.repo / rel
         path.mkdir(parents=True)
@@ -292,9 +316,6 @@ class InstallTest(CliCase):
         self.assertEqual(code, 1)
         self.assertNotIn("TS_AUTHKEY", err)
 
-    def calls_for(self, path):
-        return [c.args[1:] for c in self.compose.call_args_list if c.args[0] == path and not c.kwargs.get("capture")]
-
     def test_yes_runs_headless_variant_of_interactive_step(self):
         hl = self.add_component("stacks/hl", HEADLESS_TOML)
         _, out, _ = self.main("--skip-host", "--yes", "hl")
@@ -312,5 +333,35 @@ class InstallTest(CliCase):
         self.assertNotIn(("run", "-T", "--rm", "hl", "onboard", "--non-interactive"), calls)
 
 
+
+class ScriptStepTest(CliCase):
+    def setUp(self):
+        super().setUp()
+        self.sc = self.add_component("stacks/sc", SCRIPT_TOML, env_example="")
+        (self.sc / "tool.py").write_text(TOOL_PY)
+
+    def test_runs_script_in_component_folder_with_env(self):
+        code, out, _ = self.main("--skip-host", "sc")
+        self.assertEqual(code, 0)
+        self.assertIn(f"$ python3 tool.py apply {os.getuid()}", out)
+        self.assertIn(f"args ['apply', '{os.getuid()}']", out)
+        self.assertIn("cwd True", out)
+        self.assertIn(f"repo {self.repo} stacks {self.repo / 'stacks'}", out)
+        self.assertEqual(self.calls_for(self.sc), [("pull",), ("run", "--rm", "sc", "chown"), UP])
+
+    def test_action_lines_reach_action_needed(self):
+        _, out, _ = self.main("--skip-host", "sc")
+        action = out[out.index("Action needed"):]
+        self.assertIn("sc: team tech: add the deploy key", action)
+
+    def test_failing_script_fails_the_component(self):
+        with mock.patch.dict(os.environ, {"TOOL_EXIT": "3"}):
+            code, _, err = self.main("--skip-host", "sc")
+        self.assertEqual(code, 1)
+        self.assertIn("sc: `python3 tool.py apply", err)
+        self.assertIn("exited with 3", err)
+        self.assertNotIn(UP, self.calls_for(self.sc))
+
+
 if __name__ == "__main__":
     unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_components tests.test_cli -v`
Expected: FAIL/ERROR in `test_script_step`, `test_rejects_bad_script_steps` (`unknown key(s) script`) and `ScriptStepTest` (`unknown key(s) script`).

- [ ] **Step 4: Implement the step in components.py**

```diff
--- a/installer/components.py
+++ b/installer/components.py
@@ -5,6 +5,7 @@ an optional .env.example and a component.toml describing how to install it.
 """
 
 import re
+import shlex
 import tomllib
 from collections.abc import Iterable, Mapping
 from dataclasses import dataclass, field
@@ -15,7 +16,7 @@ from installer import envfile
 NAME_RE = re.compile(r"^[a-z0-9_-]+$")
 TOP_KEYS = {"description", "order", "dirs", "env", "setup", "tailscale"}
 ENV_KEYS = {"set", "generate", "ask"}
-STEP_KEYS = {"run", "once", "interactive", "headless", "note"}
+STEP_KEYS = {"run", "script", "once", "interactive", "headless", "note"}
 TAILSCALE_KEYS = {"service"}
 INFRA_DIRS = ("tailscale", "dockge")
 
@@ -30,15 +31,17 @@ class SelectionError(ValueError):
 
 @dataclass(frozen=True)
 class Step:
-    """A `docker compose` invocation run during install.
+    """A setup step run during install: `docker compose <run>` or `python3 <script>`.
 
+    script: a file in the component folder plus arguments.
     once: marker path; the step is skipped while it exists and the
     installer creates it after the step succeeds.
     headless: run string used instead of an interactive step under --yes.
     note: printed right before the step runs.
     """
 
-    run: str
+    run: str | None = None
+    script: str | None = None
     once: str | None = None
     interactive: bool = False
     headless: str | None = None
@@ -151,16 +154,27 @@ def load(path: Path) -> Component:
         env_set=dict(env_set),
         env_generate=tuple(generate),
         env_ask=tuple(ask),
-        setup=tuple(_step(raw, where) for raw in data.get("setup", [])),
+        setup=tuple(_step(raw, where, path) for raw in data.get("setup", [])),
         tailscale_service=service,
     )
 
 
-def _step(raw: dict, where: str) -> Step:
+def _step(raw: dict, where: str, path: Path) -> Step:
     _check_keys(raw, STEP_KEYS, f"{where} [[setup]]")
-    run, once, interactive = raw.get("run"), raw.get("once"), raw.get("interactive", False)
-    if not isinstance(run, str) or not run.strip():
-        raise ManifestError(f"{where}: every [[setup]] needs a 'run' string")
+    run, script = raw.get("run"), raw.get("script")
+    once, interactive = raw.get("once"), raw.get("interactive", False)
+    if (run is None) == (script is None):
+        raise ManifestError(f"{where}: every [[setup]] needs exactly one of 'run' or 'script'")
+    for key, value in (("run", run), ("script", script)):
+        if value is not None and (not isinstance(value, str) or not value.strip()):
+            raise ManifestError(f"{where}: 'setup.{key}' must be a non-empty string")
+    if script is not None:
+        if interactive or "headless" in raw:
+            raise ManifestError(f"{where}: 'setup.interactive' and 'setup.headless' only apply to 'run' steps")
+        target = shlex.split(script)[0]
+        _check_relative(target, "setup.script", where)
+        if not (path / target).is_file():
+            raise ManifestError(f"{where}: setup.script file not found: {target}")
     if once is not None:
         if not isinstance(once, str):
             raise ManifestError(f"{where}: 'setup.once' must be a path string")
@@ -176,7 +190,7 @@ def _step(raw: dict, where: str) -> Step:
     note = raw.get("note")
     if note is not None and not isinstance(note, str):
         raise ManifestError(f"{where}: 'setup.note' must be a string")
-    return Step(run=run, once=once, interactive=interactive, headless=headless, note=note)
+    return Step(run=run, script=script, once=once, interactive=interactive, headless=headless, note=note)
 
 
 def _check_keys(table, allowed: set[str], where: str) -> None:
```

- [ ] **Step 5: Run script steps in cli.py**

```diff
--- a/installer/cli.py
+++ b/installer/cli.py
@@ -5,6 +5,7 @@ import getpass
 import json
 import os
 import shlex
+import subprocess
 import sys
 from collections.abc import Callable
 from pathlib import Path
@@ -21,6 +22,7 @@ LOGIN_FIX = (
 )
 LOGIN_HINT = "If the {service} container is unhealthy, its node is not logged in: " + LOGIN_FIX
 NOT_LOGGED_IN = "the {service} node is not logged in: " + LOGIN_FIX
+ACTION_PREFIX = "action: "
 HTTPS_OFF = (
     "HTTPS certificates are off in your tailnet, so Serve is not applied and the URL does not load. "
     "Enable them at https://login.tailscale.com/admin/dns (HTTPS Certificates > Enable HTTPS); "
@@ -124,6 +126,7 @@ def install(component: Component, context: dict[str, str], *, prompt: Callable[[
     was_running = is_running(path)
     compose(path, "pull")
     ran_setup = False
+    actions = []
     for step in component.setup:
         marker = path / step.once if step.once else None
         if marker and marker.exists():
@@ -136,7 +139,10 @@ def install(component: Component, context: dict[str, str], *, prompt: Callable[[
             run = step.headless
         if step.note:
             print(f"  note: {step.note}")
-        compose(path, *shlex.split(components.expand(run, context)))
+        if step.script:
+            actions += run_script(component, step.script, context)
+        else:
+            compose(path, *shlex.split(components.expand(run, context)))
         ran_setup = True
         if marker:
             marker.parent.mkdir(parents=True, exist_ok=True)
@@ -155,10 +161,43 @@ def install(component: Component, context: dict[str, str], *, prompt: Callable[[
         compose(path, "restart", *[s for s in services if s != component.tailscale_service])
 
     if not component.tailscale_service:
-        return []
+        return actions
     url, problem = tailnet_check(component)
     print(f"  tailnet: {url or problem}")
-    return [problem] if problem else []
+    return actions + ([problem] if problem else [])
+
+
+def run_script(component: Component, script: str, context: dict[str, str]) -> list[str]:
+    """Run `python3 <component>/<script> <args>` in the component folder.
+
+    Output is streamed; lines starting with "action: " are also returned
+    for the Action needed list. A non-zero exit raises CommandError.
+    """
+    argv = shlex.split(components.expand(script, context))
+    label = shlex.join(["python3", *argv])
+    env = {
+        **os.environ,
+        "REPO_DIR": context["repo_dir"],
+        "STACKS_DIR": context["stacks_dir"],
+        "COMPONENT_DIR": str(component.path),
+    }
+    print(f"  $ {label}", flush=True)
+    actions = []
+    try:
+        proc = subprocess.Popen(
+            [sys.executable, str(component.path / argv[0]), *argv[1:]],
+            cwd=component.path, env=env, stdout=subprocess.PIPE, text=True,
+        )
+    except OSError as exc:
+        raise CommandError(f"`{label}`: {exc}") from exc
+    with proc:
+        for line in proc.stdout:
+            print(line, end="", flush=True)
+            if line.startswith(ACTION_PREFIX):
+                actions.append(line[len(ACTION_PREFIX):].strip())
+    if proc.returncode:
+        raise CommandError(f"`{label}` exited with {proc.returncode}")
+    return actions
 
 
 def tailnet_check(component: Component) -> tuple[str, str]:
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 7: Commit (only if the user approved commits)**

```bash
git add installer/components.py installer/cli.py tests/test_components.py tests/test_cli.py
git commit -m "feat: script setup steps with action lines for Action needed"
```

---

### Task 3: teamlib foundation: registry, paths, test fixtures

`teams.toml` handling, team name rules, host paths, the run context, and the shared test fixtures (`LocalRunner` implements the runner interface with real git and a faked OpenClaw).

**Files:**
- Create: `stacks/openclaw/teamlib/__init__.py`
- Create: `stacks/openclaw/teamlib/registry.py`
- Create: `stacks/openclaw/teamlib/context.py`
- Create: `tests/teamfixtures.py`
- Test: `tests/test_teams_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `teamlib.TeamError(Exception)`: every user-facing failure.
  - `registry.NAME_RE`, `VM_RE`, `BRANCH_RE`; `Team(name: str, url: str, branch: str = "main")` (frozen); `Registry(vm: str, git_name: str, git_email: str, teams: dict[str, Team])`; `default_vm() -> str`; `load(path: Path) -> Registry` (missing file: no teams, hostname identity); `save(path: Path, registry: Registry) -> None`; `check_new_name(name: str, existing: Iterable[str]) -> None`; `check_branch(branch: str) -> None`.
  - `context.GATEWAY_CONFIG = "/home/node/.openclaw"`, `context.GATEWAY_TEAMS = "/home/node/teams"`; `Paths(stack: Path)` with properties `data`, `teams`, `config`, `agents_d`, `openclaw_json`, `keys`, `remotes`, `registry` and methods `clone(team) -> Path`, `remote(team) -> Path`; `Context(repo: Path, paths: Paths, runner, registry: Registry, secrets: list[str] = [], out=sys.stdout)` with `say(message: str) -> None`.
  - Runner interface (implemented by `LocalRunner` here and `DockerRunner` in Task 6), all paths are host paths:
    - `git(cwd: Path, *args, team=None, check=True, binary=False, input=None) -> CompletedProcess` (raises `TeamError` when `check` and exit != 0)
    - `git_path(host: Path) -> str` (how git in the runner sees a host path; used for local remotes)
    - `keygen(team: str, comment: str) -> None`
    - `copy_in(src: Path, dest: Path) -> None` (copy a host folder without `.git` into `dest` under `data/teams`)
    - `remove_tree(path: Path) -> None`
    - `parse_json5(texts: dict[str, str]) -> tuple[dict, dict[str, str]]` (parsed, errors)
    - `read_files(paths: list[Path]) -> dict[Path, str | None]`
    - `write_files(ops: list[tuple[Path, str | None]], expect: dict[Path, str | None]) -> None` (None text deletes; raises `TeamError` if an `expect` sha256 no longer matches, before writing anything)
    - `validate(config_text: str) -> dict` (parsed `openclaw config validate --json`)
    - `openclaw(*args) -> CompletedProcess` (never raises on exit code)
  - `tests/teamfixtures.py`: `REPO`, `EXAMPLE` (`examples/teams/tech`, created in Task 4), `GIT_ENV`, `done(stdout="", stderr="", code=0)`, `run_git(cwd, *args, check=True, binary=False, input=None)`, `git_in(cwd)`, `commit_dir(src, dest) -> Path`, `LocalRunner` (attributes `validation`, `validated`, `installed`, `refuse`, `openclaw_calls`, `before_git`), `StackCase` (attributes `root`, `paths`, `runner`, `out`, `ctx`; methods `write_config`, `config`, `roster`, `output`).

- [ ] **Step 1: Write the test fixtures**

Create `tests/teamfixtures.py`:

```python
"""Shared helpers for the teamlib tests: real git in temp dirs, OpenClaw faked."""

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "stacks" / "openclaw"))

from teamlib import TeamError  # noqa: E402
from teamlib.context import Context, Paths  # noqa: E402
from teamlib.registry import Registry  # noqa: E402

EXAMPLE = REPO / "examples" / "teams" / "tech"
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_EDITOR": "true",
    "GIT_TERMINAL_PROMPT": "0",
}


def done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


def run_git(cwd, *args, check=True, binary=False, input=None):
    if input is None:
        input = b"" if binary else ""
    result = subprocess.run(["git", *args], cwd=cwd, env=GIT_ENV, input=input, capture_output=True, text=not binary)
    if check and result.returncode:
        stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode()
        raise TeamError(f"git {' '.join(args)} failed: {stderr.strip()}")
    return result


def git_in(cwd):
    return lambda *args, **kwargs: run_git(cwd, *args, **kwargs)


def commit_dir(src: Path, dest: Path) -> Path:
    """Copy src into a new git repo at dest and commit it."""
    shutil.copytree(src, dest)
    run_git(dest, "init", "-q", "-b", "main")
    run_git(dest, "add", "-A")
    run_git(dest, "-c", "user.name=Person", "-c", "user.email=person@example.com", "commit", "-q", "-m", "init")
    return dest


class LocalRunner:
    """The runner interface of teamlib.runner.DockerRunner, without Docker."""

    def __init__(self):
        self.validation = {"valid": True, "warnings": []}
        self.validated = []
        self.installed = []
        self.refuse = set()
        self.openclaw_calls = []
        self.before_git = None

    def git(self, cwd, *args, team=None, check=True, binary=False, input=None):
        if self.before_git:
            self.before_git(cwd, args)
        return run_git(cwd, *args, check=check, binary=binary, input=input)

    def git_path(self, host):
        return str(host)

    def keygen(self, team, comment):
        raise AssertionError("tests use local sources or pre-made keys")

    def copy_in(self, src, dest):
        shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))

    def remove_tree(self, path):
        shutil.rmtree(path, ignore_errors=True)

    def parse_json5(self, texts):
        parsed, errors = {}, {}
        for name, text in texts.items():
            try:
                parsed[name] = json.loads(text)
            except json.JSONDecodeError as exc:
                errors[name] = str(exc)
        return parsed, errors

    def read_files(self, paths):
        return {p: p.read_bytes().decode() if p.is_file() else None for p in paths}

    def write_files(self, ops, expect):
        for path, sha in expect.items():
            current = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            if current != sha:
                raise TeamError(f"{path} changed while teams.py was applying; run the command again")
        for path, text in ops:
            if text is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(text)

    def validate(self, config_text):
        self.validated.append(json.loads(config_text))
        return self.validation

    def openclaw(self, *args):
        self.openclaw_calls.append(args)
        if args[:2] == ("plugins", "list"):
            return done(json.dumps({"plugins": self.installed}))
        if args[:2] == ("plugins", "install"):
            if args[2] in self.refuse:
                return done(stderr="plugin requests capabilities: rerun with --accept-capabilities", code=1)
            self.installed.append({"id": args[2], "packageName": args[2], "origin": "global"})
            return done()
        raise AssertionError(f"unexpected openclaw call {args}")


class StackCase(unittest.TestCase):
    """A temp stacks/openclaw with data dirs, an openclaw.json and a Context on LocalRunner."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.paths = Paths(self.root / "stack")
        for folder in (self.paths.teams, self.paths.agents_d, self.paths.keys, self.paths.remotes):
            folder.mkdir(parents=True)
        self.write_config({"gateway": {"mode": "local"}})
        self.runner = LocalRunner()
        self.out = io.StringIO()
        self.ctx = Context(
            repo=REPO,
            paths=self.paths,
            runner=self.runner,
            registry=Registry(vm="test-vm", git_name="OpenClaw test-vm", git_email="openclaw@test-vm"),
            out=self.out,
        )

    def write_config(self, config):
        self.paths.openclaw_json.write_text(json.dumps(config, indent=2) + "\n")

    def config(self):
        return json.loads(self.paths.openclaw_json.read_text())

    def roster(self):
        return self.config().get("agents", {}).get("entries", {})

    def output(self):
        return self.out.getvalue()
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_teams_registry.py`:

```python
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import teamfixtures  # noqa: F401  (puts teamlib on sys.path)
from teamlib import TeamError, registry
from teamlib.context import Paths
from teamlib.registry import Registry, Team


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "teams.toml"

    def test_missing_file_means_no_teams_and_hostname_identity(self):
        with mock.patch.object(registry.socket, "gethostname", return_value="Agents_VM.example.com"):
            reg = registry.load(self.path)
        self.assertEqual((reg.vm, reg.git_name, reg.git_email), ("agents-vm", "OpenClaw agents-vm", "openclaw@agents-vm"))
        self.assertEqual(reg.teams, {})

    def test_round_trip(self):
        reg = Registry(vm="agents-vm", git_name='OpenClaw "vm"', git_email="openclaw@agents-vm")
        reg.teams["tech"] = Team("tech", "git@github.com:org/openclaw-tech.git")
        reg.teams["fin"] = Team("fin", "examples/teams/tech", "trunk")
        registry.save(self.path, reg)
        self.assertEqual(registry.load(self.path), reg)
        self.assertIn("[teams.fin]\nurl = \"examples/teams/tech\"\nbranch = \"trunk\"", self.path.read_text())

    def test_rejects_bad_files(self):
        for text, message in (
            ("[git\n", "teams.toml"),
            ('[git]\nvm = "a b"\n', "git.vm"),
            ('[teams.Tech]\nurl = "x"\n', "team name"),
            ("[teams.tech]\n", "needs a url"),
            ('[teams.tech]\nurl = "x"\nbranch = "-x"\n', "branch"),
        ):
            self.path.write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(TeamError, message):
                registry.load(self.path)

    def test_new_team_names(self):
        registry.check_new_name("fin", ["tech"])
        for name, existing, message in (
            ("Tech", [], "must match"),
            ("-x", [], "must match"),
            ("tech", ["tech"], "already in teams.toml"),
            ("tech-ops", ["tech"], "overlaps team 'tech'"),
            ("tech", ["tech-ops"], "overlaps team 'tech-ops'"),
        ):
            with self.subTest(name=name), self.assertRaisesRegex(TeamError, message):
                registry.check_new_name(name, existing)


class PathsTest(unittest.TestCase):
    def test_layout(self):
        paths = Paths(Path("/s"))
        self.assertEqual(paths.clone("tech"), Path("/s/data/teams/tech"))
        self.assertEqual(paths.agents_d, Path("/s/data/config/agents.d"))
        self.assertEqual(paths.openclaw_json, Path("/s/data/config/openclaw.json"))
        self.assertEqual(paths.keys, Path("/s/data/team-keys"))
        self.assertEqual(paths.remote("tech"), Path("/s/data/team-remotes/tech.git"))
        self.assertEqual(paths.registry, Path("/s/teams.toml"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_teams_registry -v`
Expected: ERROR `ModuleNotFoundError: No module named 'teamlib'`.

- [ ] **Step 4: Implement**

Create `stacks/openclaw/teamlib/__init__.py`:

```python
"""Git-managed OpenClaw teams: clone team repos, check them, sync them, apply their agents."""


class TeamError(Exception):
    """A team operation failed; the message is meant for the user."""
```

Create `stacks/openclaw/teamlib/registry.py`:

```python
"""teams.toml: the teams this VM runs and the git identity of its sync commits."""

import json
import re
import socket
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from teamlib import TeamError

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
VM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


@dataclass(frozen=True)
class Team:
    name: str
    url: str
    branch: str = "main"


@dataclass
class Registry:
    vm: str
    git_name: str
    git_email: str
    teams: dict[str, Team] = field(default_factory=dict)


def default_vm() -> str:
    host = socket.gethostname().split(".")[0].lower()
    return re.sub(r"[^a-z0-9-]", "-", host).strip("-") or "vm"


def load(path: Path) -> Registry:
    """Read teams.toml; a missing file means no teams and default identity."""
    data = {}
    if path.is_file():
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise TeamError(f"{path}: {exc}") from exc
    git = data.get("git", {})
    vm = git.get("vm") or default_vm()
    if not isinstance(vm, str) or not VM_RE.match(vm):
        raise TeamError(f"{path}: git.vm must match {VM_RE.pattern}")
    registry = Registry(vm=vm, git_name=git.get("name") or f"OpenClaw {vm}", git_email=git.get("email") or f"openclaw@{vm}")
    for name, raw in data.get("teams", {}).items():
        if not NAME_RE.match(name):
            raise TeamError(f"{path}: team name {name!r} must match {NAME_RE.pattern}")
        if not isinstance(raw, dict) or not isinstance(raw.get("url"), str) or not raw["url"]:
            raise TeamError(f"{path}: [teams.{name}] needs a url")
        branch = raw.get("branch", "main")
        if not isinstance(branch, str) or not BRANCH_RE.match(branch):
            raise TeamError(f"{path}: [teams.{name}] branch must match {BRANCH_RE.pattern}")
        registry.teams[name] = Team(name, raw["url"], branch)
    return registry


def save(path: Path, registry: Registry) -> None:
    lines = ["[git]", f"name = {_quote(registry.git_name)}", f"email = {_quote(registry.git_email)}", f"vm = {_quote(registry.vm)}"]
    for team in registry.teams.values():
        lines += ["", f"[teams.{team.name}]", f"url = {_quote(team.url)}", f"branch = {_quote(team.branch)}"]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(path)


def check_new_name(name: str, existing: Iterable[str]) -> None:
    """A new team name must be valid, unused, and not a dash-prefix of another team (agent ids would clash)."""
    if not NAME_RE.match(name):
        raise TeamError(f"team name {name!r} must match {NAME_RE.pattern}")
    for other in existing:
        if other == name:
            raise TeamError(f"team {name} is already in teams.toml")
        if name.startswith(other + "-") or other.startswith(name + "-"):
            raise TeamError(f"team name {name!r} overlaps team {other!r}: their agent ids would be ambiguous")


def check_branch(branch: str) -> None:
    if not BRANCH_RE.match(branch):
        raise TeamError(f"branch {branch!r} must match {BRANCH_RE.pattern}")


def _quote(value: str) -> str:
    # JSON string escapes are valid TOML basic-string escapes.
    return json.dumps(value, ensure_ascii=False)
```

Create `stacks/openclaw/teamlib/context.py`:

```python
"""Where team data lives, and the state shared by one teams.py run."""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from teamlib.registry import Registry

GATEWAY_CONFIG = "/home/node/.openclaw"
GATEWAY_TEAMS = "/home/node/teams"


@dataclass(frozen=True)
class Paths:
    """Host paths under stacks/openclaw/."""

    stack: Path

    @property
    def data(self) -> Path:
        return self.stack / "data"

    @property
    def teams(self) -> Path:
        return self.data / "teams"

    @property
    def config(self) -> Path:
        return self.data / "config"

    @property
    def agents_d(self) -> Path:
        return self.config / "agents.d"

    @property
    def openclaw_json(self) -> Path:
        return self.config / "openclaw.json"

    @property
    def keys(self) -> Path:
        return self.data / "team-keys"

    @property
    def remotes(self) -> Path:
        return self.data / "team-remotes"

    @property
    def registry(self) -> Path:
        return self.stack / "teams.toml"

    def clone(self, team: str) -> Path:
        return self.teams / team

    def remote(self, team: str) -> Path:
        return self.remotes / f"{team}.git"


@dataclass
class Context:
    repo: Path
    paths: Paths
    runner: object
    registry: Registry
    secrets: list[str] = field(default_factory=list)
    out: TextIO = sys.stdout

    def say(self, message: str) -> None:
        print(message, file=self.out, flush=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_teams_registry -v`
Expected: all PASS.

- [ ] **Step 6: Commit (only if the user approved commits)**

```bash
git add stacks/openclaw/teamlib/__init__.py stacks/openclaw/teamlib/registry.py stacks/openclaw/teamlib/context.py tests/teamfixtures.py tests/test_teams_registry.py
git commit -m "feat: teams.toml registry, team paths and test fixtures"
```

---

### Task 4: Team repo contract and the example team

Read a team repo from a git revision, check it against the contract, merge `team.json5` with `agents/<id>.json5`, prefix `subagents.allowAgents`, and add `examples/teams/tech/`.

**Files:**
- Create: `stacks/openclaw/teamlib/tree.py`
- Create: `stacks/openclaw/teamlib/contract.py`
- Create: `examples/teams/tech/` (files below)
- Test: `tests/test_teams_contract.py`

**Interfaces:**
- Consumes: `TeamError`, `registry.NAME_RE`, `context.GATEWAY_TEAMS`; fixtures `EXAMPLE`, `commit_dir`, `git_in`, `run_git`.
- Produces:
  - `tree.SETTINGS_RE`; `Tree(workspaces: dict[str, frozenset[str]], nested_git: frozenset[str], texts: dict[str, str])`; `from_rev(git, rev: str) -> Tree` where `git(*args, binary=False)` runs git in the clone. Only committed files count, so untracked and ignored leftovers never become agents.
  - `contract.MAX_ID = 64`, `REQUIRED_FILES`, `FORBIDDEN_KEYS`; `Resolved(entries: dict[str, dict], plugins: list[str], warnings: list[str])`; `agent_id(team, folder) -> str`; `workspace(team, folder) -> str`; `merge(base: dict, override: dict) -> dict`; `plugin_specs(value) -> list[str] | None`; `resolve(team, tree, parsed: dict, errors: dict[str, str]) -> Resolved` (raises `TeamError` listing every problem).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_teams_contract.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from tests.teamfixtures import EXAMPLE, commit_dir, git_in, run_git
from teamlib import TeamError, contract
from teamlib.tree import Tree, from_rev

GOOD = frozenset({"SOUL.md", "AGENTS.md"})


def tree(workspaces=None, texts=None, nested=()):
    workspaces = {"a": GOOD} if workspaces is None else workspaces
    texts = {"team.json5": "{}"} if texts is None else texts
    return Tree(workspaces, frozenset(nested), texts)


def resolve(t, team="tech"):
    parsed, errors = {}, {}
    for name, text in t.texts.items():
        try:
            parsed[name] = json.loads(text)
        except json.JSONDecodeError as exc:
            errors[name] = str(exc)
    return contract.resolve(team, t, parsed, errors)


class MergeTest(unittest.TestCase):
    def test_objects_merge_and_other_values_replace(self):
        base = {"model": {"primary": "a", "fallbacks": ["b"]}, "skills": ["x", "y"], "name": "base"}
        override = {"model": {"primary": "c"}, "skills": ["z"]}
        self.assertEqual(
            contract.merge(base, override),
            {"model": {"primary": "c", "fallbacks": ["b"]}, "skills": ["z"], "name": "base"},
        )
        self.assertEqual(base["model"], {"primary": "a", "fallbacks": ["b"]})


class ResolveTest(unittest.TestCase):
    def test_example_team(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = commit_dir(EXAMPLE, Path(tmp) / "tech")
            resolved = resolve(from_rev(git_in(repo), "HEAD"))
        self.assertEqual(resolved.entries, {
            "tech-coordinator": {
                "description": "Tech team agent",
                "subagents": {"allowAgents": ["tech-reviewer"]},
                "workspace": "/home/node/teams/tech/workspaces/coordinator",
            },
            "tech-reviewer": {
                "description": "Reviews code and designs for the tech team",
                "subagents": {"allowAgents": []},
                "name": "Reviewer",
                "workspace": "/home/node/teams/tech/workspaces/reviewer",
            },
        })
        self.assertEqual((resolved.plugins, resolved.warnings), ([], []))

    def test_allow_agents_prefix_only_team_folders(self):
        t = tree({"a": GOOD, "b": GOOD}, {"team.json5": '{"subagents": {"allowAgents": ["b", "main", "*"]}}'})
        self.assertEqual(resolve(t).entries["tech-a"]["subagents"]["allowAgents"], ["tech-b", "main", "*"])

    def test_plugins(self):
        t = tree(texts={"team.json5": "{}", "plugins.json": '{"plugins": ["@openclaw/voice-call", "foo@1.2.0"]}'})
        self.assertEqual(resolve(t).plugins, ["@openclaw/voice-call", "foo@1.2.0"])

    def test_nested_git_is_a_warning(self):
        self.assertEqual(len(resolve(tree(nested=["a"])).warnings), 1)

    def assert_problem(self, t, message, team="tech"):
        with self.assertRaisesRegex(TeamError, message):
            resolve(t, team)

    def test_problems(self):
        self.assert_problem(tree({}), "no agents")
        self.assert_problem(tree({"Bad_Name": GOOD}), "workspaces/Bad_Name: folder names must match")
        self.assert_problem(tree({"a": frozenset({"SOUL.md"})}), "workspaces/a: missing AGENTS.md")
        self.assert_problem(tree({"a" * 60: GOOD}), "longer than 64")
        self.assert_problem(tree(texts={}), "team.json5 is missing")
        self.assert_problem(tree(texts={"team.json5": "[]"}), "team.json5: must be an object")
        self.assert_problem(tree(texts={"team.json5": "{oops"}), "team.json5: ")
        self.assert_problem(tree(texts={"team.json5": '{"workspace": "/x", "default": true}'}), "must not set workspace, default")
        self.assert_problem(tree(texts={"team.json5": "{}", "agents/ghost.json5": "{}"}), "no matching workspaces/ghost/")
        self.assert_problem(tree(texts={"team.json5": "{}", "plugins.json": '["x"]'}), "plugins.json: expected")

    def test_lists_every_problem_at_once(self):
        with self.assertRaises(TeamError) as caught:
            resolve(tree({"a": frozenset()}, {}))
        self.assertIn("missing SOUL.md, AGENTS.md", str(caught.exception))
        self.assertIn("team.json5 is missing", str(caught.exception))


class FromRevTest(unittest.TestCase):
    def test_reads_committed_state_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = commit_dir(EXAMPLE, Path(tmp) / "tech")
            (repo / "workspaces" / "untracked").mkdir()
            (repo / "workspaces" / "untracked" / "SOUL.md").write_text("x")
            t = from_rev(git_in(repo), "HEAD")
        self.assertEqual(sorted(t.workspaces), ["coordinator", "reviewer"])
        self.assertIn("memory", t.workspaces["coordinator"])
        self.assertEqual(sorted(t.texts), ["agents/reviewer.json5", "plugins.json", "team.json5"])

    def test_committed_nested_repo_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = commit_dir(EXAMPLE, Path(tmp) / "tech")
            inner = repo / "workspaces" / "inner"
            inner.mkdir()
            run_git(inner, "init", "-q")
            (inner / "SOUL.md").write_text("x")
            run_git(inner, "add", "-A")
            run_git(inner, "-c", "user.name=P", "-c", "user.email=p@x", "commit", "-q", "-m", "x")
            run_git(repo, "add", "workspaces/inner")
            run_git(repo, "-c", "user.name=P", "-c", "user.email=p@x", "commit", "-q", "-m", "nested")
            t = from_rev(git_in(repo), "HEAD")
        self.assertEqual(t.nested_git, frozenset({"inner"}))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_teams_contract -v`
Expected: ERROR `ImportError: cannot import name 'contract' from 'teamlib'`.

- [ ] **Step 3: Create the example team**

The settings files use strict JSON so the tests can read them with the standard library. No `model`: the agents use the provider chosen at onboarding.

`examples/teams/tech/team.json5`:

```json
{
  "description": "Tech team agent",
  "subagents": {"allowAgents": ["reviewer"]}
}
```

`examples/teams/tech/agents/reviewer.json5`:

```json
{
  "name": "Reviewer",
  "description": "Reviews code and designs for the tech team",
  "subagents": {"allowAgents": []}
}
```

`examples/teams/tech/plugins.json`:

```json
{"plugins": []}
```

`examples/teams/tech/.gitignore`:

```text
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

`examples/teams/tech/.gitattributes`:

```text
workspaces/*/MEMORY.md     merge=union
workspaces/*/DREAMS.md     merge=union
workspaces/*/memory/**     merge=union
```

`examples/teams/tech/workspaces/coordinator/SOUL.md`:

```markdown
# Soul

You coordinate the tech team. You are calm, direct and precise. You break work
into clear steps, hand reviews to the reviewer, and report back in plain words.
```

`examples/teams/tech/workspaces/coordinator/AGENTS.md`:

```markdown
# Operating notes

- Plan before acting; write the plan down in a short list.
- Ask the reviewer (agent `tech-reviewer`) to review designs and code before you call them done.
- Keep long-term facts in MEMORY.md and daily notes in memory/YYYY-MM-DD.md.
- Never put keys, tokens or passwords in any file in this workspace.
```

`examples/teams/tech/workspaces/coordinator/IDENTITY.md`:

```markdown
# Identity

- Name: Coordinator
- Team: tech
```

`examples/teams/tech/workspaces/reviewer/SOUL.md`:

```markdown
# Soul

You review code and designs for the tech team. You are thorough and kind. You
name the problem, why it matters, and the smallest fix.
```

`examples/teams/tech/workspaces/reviewer/AGENTS.md`:

```markdown
# Operating notes

- Review what you are given; do not rewrite it.
- List findings from most to least severe, each with a concrete fix.
- Say plainly when something is fine.
- Never put keys, tokens or passwords in any file in this workspace.
```

`examples/teams/tech/workspaces/reviewer/IDENTITY.md`:

```markdown
# Identity

- Name: Reviewer
- Team: tech
```

`examples/teams/tech/workspaces/coordinator/USER.md` and `examples/teams/tech/workspaces/reviewer/USER.md` (same content):

```markdown
# The people you work for

The tech team. Describe their roles, preferences and working hours here.
```

`examples/teams/tech/workspaces/coordinator/MEMORY.md` and `examples/teams/tech/workspaces/reviewer/MEMORY.md` (same content):

```markdown
# Memory
```

Empty placeholder files:

```bash
touch examples/teams/tech/workspaces/{coordinator,reviewer}/{memory,skills}/.gitkeep
```

- [ ] **Step 4: Implement tree.py**

Create `stacks/openclaw/teamlib/tree.py`:

```python
"""A team repo as the contract sees it, read from a git revision."""

import io
import re
import tarfile
from dataclasses import dataclass

SETTINGS_RE = re.compile(r"^(team\.json5|plugins\.json|agents/[^/]+\.json5)$")


@dataclass(frozen=True)
class Tree:
    workspaces: dict[str, frozenset[str]]
    nested_git: frozenset[str]
    texts: dict[str, str]


def from_rev(git, rev: str) -> Tree:
    """Read a committed tree. git(*args, binary=False) runs git in the clone.

    workspaces maps each workspaces/<id>/ folder to the names directly inside it;
    nested_git holds folders committed as nested repos; texts holds the settings files.
    """
    listing = git("ls-tree", "-r", "-z", rev).stdout
    workspaces: dict[str, set[str]] = {}
    nested, settings = set(), []
    for item in filter(None, listing.split("\0")):
        meta, path = item.split("\t", 1)
        parts = path.split("/")
        if parts[0] == "workspaces" and len(parts) >= 2:
            names = workspaces.setdefault(parts[1], set())
            if len(parts) == 2 and meta.startswith("160000"):
                nested.add(parts[1])
            elif len(parts) >= 3:
                names.add(parts[2])
        elif SETTINGS_RE.match(path):
            settings.append(path)
    texts = {}
    if settings:
        data = git("archive", "--format=tar", rev, "--", *settings, binary=True).stdout
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    texts[member.name] = tar.extractfile(member).read().decode("utf-8", errors="replace")
    return Tree({name: frozenset(names) for name, names in workspaces.items()}, frozenset(nested), texts)
```

- [ ] **Step 5: Implement contract.py**

Create `stacks/openclaw/teamlib/contract.py`:

```python
"""The team repo contract (TEAMS.md): check a team's files and build its agent entries."""

import copy
from dataclasses import dataclass

from teamlib import TeamError
from teamlib.context import GATEWAY_TEAMS
from teamlib.registry import NAME_RE
from teamlib.tree import Tree

MAX_ID = 64
REQUIRED_FILES = ("SOUL.md", "AGENTS.md")
FORBIDDEN_KEYS = ("workspace", "cwd", "agentDir", "default")


@dataclass(frozen=True)
class Resolved:
    entries: dict[str, dict]
    plugins: list[str]
    warnings: list[str]


def agent_id(team: str, folder: str) -> str:
    return f"{team}-{folder}"


def workspace(team: str, folder: str) -> str:
    return f"{GATEWAY_TEAMS}/{team}/workspaces/{folder}"


def merge(base: dict, override: dict) -> dict:
    """Objects merge key by key; any other value in override (arrays included) replaces base."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(out.get(key), dict) and isinstance(value, dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def plugin_specs(value) -> list[str] | None:
    """The list in a parsed plugins.json, or None when it has the wrong shape."""
    if not isinstance(value, dict) or set(value) - {"plugins"}:
        return None
    specs = value.get("plugins", [])
    if not isinstance(specs, list) or not all(isinstance(s, str) and s.strip() for s in specs):
        return None
    return list(specs)


def resolve(team: str, tree: Tree, parsed: dict, errors: dict[str, str]) -> Resolved:
    """Check the contract and build {agent id: entry}. Raises TeamError listing every problem.

    parsed and errors come from parsing tree.texts (JSON5).
    """
    problems = [f"{name}: {message}" for name, message in sorted(errors.items())]
    folders = sorted(tree.workspaces)
    if not folders:
        problems.append("no agents: add workspaces/<id>/ with SOUL.md and AGENTS.md")
    for folder in folders:
        if not NAME_RE.match(folder):
            problems.append(f"workspaces/{folder}: folder names must match {NAME_RE.pattern}")
        elif len(agent_id(team, folder)) > MAX_ID:
            problems.append(f"workspaces/{folder}: agent id {agent_id(team, folder)} is longer than {MAX_ID} characters")
        missing = [name for name in REQUIRED_FILES if name not in tree.workspaces[folder]]
        if missing:
            problems.append(f"workspaces/{folder}: missing {', '.join(missing)}")

    if "team.json5" not in tree.texts:
        problems.append("team.json5 is missing (use {} when the agents share no settings)")
    settings = {}
    for name in sorted(tree.texts):
        if name == "plugins.json" or name in errors:
            continue
        value = parsed.get(name)
        if not isinstance(value, dict):
            problems.append(f"{name}: must be an object")
            continue
        forbidden = [key for key in FORBIDDEN_KEYS if key in value]
        if forbidden:
            problems.append(f"{name}: must not set {', '.join(forbidden)} (teams.py manages them)")
        folder = name.removeprefix("agents/").removesuffix(".json5")
        if name.startswith("agents/") and folder not in tree.workspaces:
            problems.append(f"{name}: no matching workspaces/{folder}/ folder")
        settings[name] = value

    plugins = []
    if "plugins.json" in tree.texts and "plugins.json" not in errors:
        specs = plugin_specs(parsed.get("plugins.json"))
        if specs is None:
            problems.append('plugins.json: expected {"plugins": ["<package or ClawHub id>", ...]}')
        else:
            plugins = specs

    if problems:
        raise TeamError(f"team {team} does not follow TEAMS.md:\n  - " + "\n  - ".join(problems))

    entries = {}
    for folder in folders:
        entry = merge(settings.get("team.json5", {}), settings.get(f"agents/{folder}.json5", {}))
        _prefix_allow_agents(team, entry, set(folders))
        entry["workspace"] = workspace(team, folder)
        entries[agent_id(team, folder)] = entry
    warnings = [f"workspaces/{f} holds its own .git; remove it so the folder syncs" for f in sorted(tree.nested_git)]
    return Resolved(entries, plugins, warnings)


def _prefix_allow_agents(team: str, entry: dict, folders: set[str]) -> None:
    subagents = entry.get("subagents")
    if isinstance(subagents, dict) and isinstance(subagents.get("allowAgents"), list):
        subagents["allowAgents"] = [agent_id(team, a) if a in folders else a for a in subagents["allowAgents"]]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_teams_contract -v`
Expected: all PASS.

- [ ] **Step 7: Commit (only if the user approved commits)**

```bash
git add stacks/openclaw/teamlib/tree.py stacks/openclaw/teamlib/contract.py examples/teams/tech tests/test_teams_contract.py
git commit -m "feat: team repo contract, entry merge and example tech team"
```

---

### Task 5: Secret and size scan

**Files:**
- Create: `stacks/openclaw/teamlib/scan.py`
- Test: `tests/test_teams_scan.py`

**Interfaces:**
- Consumes: `installer.envfile.parse` (the repo root is on `sys.path`: tests run from it, `teams.py` adds it).
- Produces: `scan.MAX_BYTES`, `MIN_SECRET`, `SECRET_KEY_RE`, `PATTERNS`; `load_secrets(stack: Path) -> list[str]`; `problem(path: Path, secrets: list[str]) -> str | None` (None for clean, deleted or symlinked files).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_teams_scan.py`:

```python
import os
import tempfile
import unittest
from pathlib import Path

from tests import teamfixtures  # noqa: F401
from teamlib import scan

KEY = "sk-ant-" + "a" * 30


class ScanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def file(self, name, content):
        path = self.dir / name
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return path

    def test_load_secrets_from_env_and_manifest(self):
        self.file(".env", "OPENCLAW_GATEWAY_TOKEN=abcdef0123\nTS_AUTHKEY=tskey-1234567\nSHORT_KEY=abc\nTZ=Europe/Berlin\nOPENCLAW_TZ=Europe/Berlin\n")
        self.file("component.toml", '[env]\nask = ["TS_AUTHKEY"]\n')
        self.assertEqual(scan.load_secrets(self.dir), ["abcdef0123", "tskey-1234567"])

    def test_no_env_means_no_secrets(self):
        self.assertEqual(scan.load_secrets(self.dir), [])

    def test_problems(self):
        self.assertIsNone(scan.problem(self.file("ok.md", "notes"), ["abcdef0123"]))
        self.assertEqual(scan.problem(self.file("env.md", "token abcdef0123"), ["abcdef0123"]), "contains a value from .env")
        self.assertEqual(scan.problem(self.file("key.md", f"key: {KEY}"), []), "looks like it contains a key or token")
        self.assertEqual(scan.problem(self.file("pem.md", "-----BEGIN OPENSSH PRIVATE KEY-----"), []), "looks like it contains a key or token")
        self.assertEqual(scan.problem(self.file("big.bin", b"\0" * (scan.MAX_BYTES + 1)), []), "larger than 5 MB")

    def test_deleted_files_and_symlinks_are_fine(self):
        self.assertIsNone(scan.problem(self.dir / "gone.md", []))
        os.symlink("/nonexistent", self.dir / "link")
        self.assertIsNone(scan.problem(self.dir / "link", []))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_teams_scan -v`
Expected: ERROR `ImportError: cannot import name 'scan' from 'teamlib'`.

- [ ] **Step 3: Implement**

Create `stacks/openclaw/teamlib/scan.py`:

```python
"""Keep secrets and large files out of sync commits."""

import re
import tomllib
from pathlib import Path

from installer import envfile

MAX_BYTES = 5 * 1024 * 1024
MIN_SECRET = 8
SECRET_KEY_RE = re.compile(r"_(KEY|TOKEN|SECRET|PASSWORD)$")
PATTERNS = [
    re.compile(pattern)
    for pattern in (
        r"sk-ant-[A-Za-z0-9_-]{20,}",
        r"sk-[A-Za-z0-9]{20,}",
        r"ghp_[A-Za-z0-9]{36}",
        r"github_pat_[A-Za-z0-9_]{20,}",
        r"xox[abp]-[A-Za-z0-9-]{10,}",
        r"AKIA[0-9A-Z]{16}",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    )
]


def load_secrets(stack: Path) -> list[str]:
    """Values from stack/.env that must never be committed.

    Keys named *_KEY, *_TOKEN, *_SECRET, *_PASSWORD or listed in [env] generate/ask
    of component.toml, when the value has at least 8 characters.
    """
    env_path = stack / ".env"
    if not env_path.is_file():
        return []
    listed = set()
    manifest = stack / "component.toml"
    if manifest.is_file():
        env_table = tomllib.loads(manifest.read_text()).get("env", {})
        listed = {*env_table.get("generate", []), *env_table.get("ask", [])}
    env = envfile.parse(env_path.read_text())
    return sorted({
        value for key, value in env.items()
        if (SECRET_KEY_RE.search(key) or key in listed) and len(value) >= MIN_SECRET
    })


def problem(path: Path, secrets: list[str]) -> str | None:
    """Why path must not be committed, or None. Deleted files and symlinks are fine."""
    if path.is_symlink() or not path.exists():
        return None
    try:
        if path.stat().st_size > MAX_BYTES:
            return "larger than 5 MB"
        text = path.read_bytes().decode("utf-8", errors="ignore")
    except OSError as exc:
        return f"unreadable ({exc.strerror})"
    if any(secret in text for secret in secrets):
        return "contains a value from .env"
    if any(pattern.search(text) for pattern in PATTERNS):
        return "looks like it contains a key or token"
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_teams_scan -v`
Expected: all PASS.

- [ ] **Step 5: Commit (only if the user approved commits)**

```bash
git add stacks/openclaw/teamlib/scan.py tests/test_teams_scan.py
git commit -m "feat: keep secrets and large files out of team sync commits"
```

---

### Task 6: Docker runner

The production runner: git in `team-git`, config files through a small node helper in `openclaw-cli` (JSON5 parsing with OpenClaw's own `json5`, reads, atomic 0600 writes guarded by sha256), validation on a copy next to `openclaw.json`. The node helper was exercised against the real image during the spike.

**Files:**
- Create: `stacks/openclaw/teamlib/runner.py`
- Test: `tests/test_teams_runner.py`

**Interfaces:**
- Consumes: `TeamError`, `context.Paths`, `GATEWAY_CONFIG`, `GATEWAY_TEAMS`.
- Produces: `runner.CHECK_PATH`, `VALIDATE_SH`, `NODE_HELPER`; `ssh_command(team: str) -> str`; `json_out(text: str) -> dict` (skips log lines before the JSON); `DockerRunner(paths: Paths)` implementing the runner interface from Task 3, running `docker compose ...` with cwd `paths.stack`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_teams_runner.py`:

```python
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.teamfixtures import done
from teamlib import TeamError, runner
from teamlib.context import Paths
from teamlib.runner import DockerRunner


class DockerRunnerTest(unittest.TestCase):
    def setUp(self):
        self.paths = Paths(Path("/srv/stacks/openclaw"))
        self.runner = DockerRunner(self.paths)
        self.run = mock.patch.object(runner.subprocess, "run", return_value=done("{}")).start()
        self.addCleanup(mock.patch.stopall)

    def cmd(self):
        return self.run.call_args.args[0]

    def test_git_runs_in_team_git_with_the_team_key(self):
        self.runner.git(self.paths.clone("tech"), "fetch", "origin", "main", team="tech")
        self.assertEqual(self.cmd(), [
            "docker", "compose", "run", "--rm", "-T",
            "-e", "GIT_TERMINAL_PROMPT=0", "-e", "GIT_EDITOR=true",
            "-e", "GIT_SSH_COMMAND=" + runner.ssh_command("tech"),
            "-w", "/teams/tech", "team-git", "fetch", "origin", "main",
        ])
        self.assertEqual(self.run.call_args.kwargs["cwd"], self.paths.stack)
        self.assertIn("-i /keys/tech -o IdentitiesOnly=yes", runner.ssh_command("tech"))
        self.assertIn("UserKnownHostsFile=/keys/known_hosts", runner.ssh_command("tech"))

    def test_path_mapping(self):
        self.assertEqual(self.runner.git_path(self.paths.remote("tech")), "/remotes/tech.git")
        self.assertEqual(self.runner.git_path(self.paths.teams), "/teams")
        with self.assertRaisesRegex(TeamError, "outside the folders"):
            self.runner.git_path(Path("/etc"))

    def test_failed_command_raises_with_stderr(self):
        self.run.return_value = done(stderr="fatal: boom", code=128)
        with self.assertRaisesRegex(TeamError, "`git status` failed: fatal: boom"):
            self.runner.git(self.paths.clone("tech"), "status")
        self.assertEqual(self.runner.git(self.paths.clone("tech"), "status", check=False).returncode, 128)

    def test_keygen(self):
        self.runner.keygen("tech", "openclaw-vm-tech")
        self.assertEqual(self.cmd()[-12:], [
            "--entrypoint", "ssh-keygen", "team-git", "-q", "-t", "ed25519", "-N", "", "-C", "openclaw-vm-tech", "-f", "/keys/tech",
        ])

    def test_copy_in_streams_a_tar_without_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            (src / ".git").mkdir()
            (src / ".git" / "HEAD").write_text("x")
            (src / "team.json5").write_text("{}")
            self.runner.copy_in(src, self.paths.clone("tech"))
        self.assertEqual(self.cmd()[-6:], ["--entrypoint", "tar", "team-git", "-x", "-C", "/teams/tech"])
        with tarfile.open(fileobj=io.BytesIO(self.run.call_args.kwargs["input"])) as tar:
            names = tar.getnames()
        self.assertIn("./team.json5", names)
        self.assertFalse(any(".git" in Path(n).parts for n in names))

    def test_remove_tree_refuses_mount_roots(self):
        with self.assertRaisesRegex(TeamError, "refusing"):
            self.runner.remove_tree(self.paths.teams)
        self.runner.remove_tree(self.paths.clone("tech"))
        self.assertEqual(self.cmd()[-4:], ["team-git", "-rf", "--", "/teams/tech"])

    def test_files_go_through_the_cli_container(self):
        self.run.return_value = done(json.dumps({"parsed": {}, "errors": {}, "files": {"/home/node/.openclaw/openclaw.json": "{}"}}))
        found = self.runner.read_files([self.paths.openclaw_json])
        self.assertEqual(found, {self.paths.openclaw_json: "{}"})
        self.assertEqual(self.cmd()[5:9], ["--entrypoint", "node", "openclaw-cli", "-e"])
        self.assertEqual(json.loads(self.run.call_args.kwargs["input"]), {"read": ["/home/node/.openclaw/openclaw.json"]})

    def test_write_files_maps_paths_and_reports_conflicts(self):
        self.run.return_value = done(json.dumps({"conflict": "/home/node/.openclaw/openclaw.json"}))
        agent = self.paths.agents_d / "tech-a.json5"
        with self.assertRaisesRegex(TeamError, "changed while teams.py was applying"):
            self.runner.write_files([(agent, "{}\n"), (self.paths.openclaw_json, None)], expect={self.paths.openclaw_json: "abc"})
        self.assertEqual(json.loads(self.run.call_args.kwargs["input"]), {
            "expect": {"/home/node/.openclaw/openclaw.json": "abc"},
            "ops": [
                {"path": "/home/node/.openclaw/agents.d/tech-a.json5", "text": "{}\n"},
                {"path": "/home/node/.openclaw/openclaw.json", "text": None},
            ],
        })

    def test_validate_uses_a_copy_next_to_the_config(self):
        self.run.return_value = done('[plugins] loading\n{\n  "valid": false,\n  "issues": []\n}\n', code=1)
        self.assertEqual(self.runner.validate('{"a": 1}'), {"valid": False, "issues": []})
        cmd = self.cmd()
        self.assertIn("OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.check.json", cmd)
        self.assertEqual(cmd[-3:-1], ["openclaw-cli", "-c"])
        self.assertEqual(self.run.call_args.kwargs["input"], '{"a": 1}')

    def test_json_out_rejects_non_json(self):
        with self.assertRaisesRegex(TeamError, "expected JSON"):
            runner.json_out("Error: gateway not running")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_teams_runner -v`
Expected: ERROR `ImportError: cannot import name 'runner' from 'teamlib'`.

- [ ] **Step 3: Implement**

Create `stacks/openclaw/teamlib/runner.py`:

```python
"""Run git and OpenClaw in one-off containers of the openclaw stack.

git runs in team-git (uid 1000, deploy keys, no .env); config files are read
and written through openclaw-cli, because data/config belongs to uid 1000
and openclaw.json is mode 0600.
"""

import io
import json
import subprocess
import tarfile
from pathlib import Path

from teamlib import TeamError
from teamlib.context import GATEWAY_CONFIG, GATEWAY_TEAMS, Paths

CHECK_PATH = f"{GATEWAY_CONFIG}/openclaw.check.json"
VALIDATE_SH = (
    'umask 077; cat > "$OPENCLAW_CONFIG_PATH" && node dist/index.js config validate --json; '
    'status=$?; rm -f "$OPENCLAW_CONFIG_PATH"; exit $status'
)
NODE_HELPER = r"""
const fs = require("fs");
const crypto = require("crypto");
const req = JSON.parse(fs.readFileSync(0, "utf8"));
const out = { parsed: {}, errors: {}, files: {} };
if (req.parse) {
  const JSON5 = require("json5");
  for (const [name, text] of Object.entries(req.parse)) {
    try { out.parsed[name] = JSON5.parse(text); } catch (e) { out.errors[name] = e.message; }
  }
}
for (const p of req.read || []) {
  try { out.files[p] = fs.readFileSync(p, "utf8"); } catch (e) { if (e.code !== "ENOENT") throw e; out.files[p] = null; }
}
const sha = (p) => fs.existsSync(p) ? crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex") : null;
for (const [p, expected] of Object.entries(req.expect || {})) {
  if (sha(p) !== expected) { out.conflict = p; break; }
}
if (!out.conflict) {
  for (const op of req.ops || []) {
    if (op.text === null) { fs.rmSync(op.path, { force: true }); continue; }
    const tmp = `${op.path}.tmp-${process.pid}`;
    fs.writeFileSync(tmp, op.text, { mode: 0o600 });
    fs.renameSync(tmp, op.path);
  }
}
process.stdout.write(JSON.stringify(out));
"""


def ssh_command(team: str) -> str:
    return (
        f"ssh -i /keys/{team} -o IdentitiesOnly=yes -o UserKnownHostsFile=/keys/known_hosts "
        "-o StrictHostKeyChecking=accept-new"
    )


def json_out(text: str) -> dict:
    """Parse JSON printed by the OpenClaw CLI, skipping any log lines before it."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("{"):
            try:
                return json.loads("\n".join(lines[index:]))
            except json.JSONDecodeError:
                continue
    raise TeamError(f"expected JSON from OpenClaw, got: {text.strip()[:300]}")


class DockerRunner:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.git_mounts = {paths.teams: "/teams", paths.keys: "/keys", paths.remotes: "/remotes"}
        self.gateway_mounts = {paths.config: GATEWAY_CONFIG, paths.teams: GATEWAY_TEAMS}

    def git(self, cwd: Path, *args: str, team: str | None = None, check: bool = True,
            binary: bool = False, input: str | None = None) -> subprocess.CompletedProcess:
        env = ["-e", "GIT_TERMINAL_PROMPT=0", "-e", "GIT_EDITOR=true"]
        if team:
            env += ["-e", f"GIT_SSH_COMMAND={ssh_command(team)}"]
        return self._compose(
            "run", "--rm", "-T", *env, "-w", self.git_path(cwd), "team-git", *args,
            label=f"git {' '.join(args)}", check=check, binary=binary, input=input,
        )

    def git_path(self, host: Path) -> str:
        return _map(host, self.git_mounts)

    def keygen(self, team: str, comment: str) -> None:
        self._compose(
            "run", "--rm", "-T", "--entrypoint", "ssh-keygen", "team-git",
            "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", f"/keys/{team}",
            label="ssh-keygen",
        )

    def copy_in(self, src: Path, dest: Path) -> None:
        """Copy a host folder (without .git) into dest under data/teams."""
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            tar.add(src, arcname=".", filter=lambda info: None if ".git" in Path(info.name).parts else info)
        self._compose(
            "run", "--rm", "-T", "--entrypoint", "tar", "team-git", "-x", "-C", self.git_path(dest),
            label=f"copy {src}", binary=True, input=buffer.getvalue(),
        )

    def remove_tree(self, path: Path) -> None:
        if path in self.git_mounts:
            raise TeamError(f"refusing to remove {path}")
        self._compose("run", "--rm", "-T", "--entrypoint", "rm", "team-git", "-rf", "--", self.git_path(path), label=f"rm -rf {path}")

    def parse_json5(self, texts: dict[str, str]) -> tuple[dict, dict[str, str]]:
        out = self._node({"parse": texts})
        return out["parsed"], out["errors"]

    def read_files(self, paths: list[Path]) -> dict[Path, str | None]:
        mapped = {_map(p, self.gateway_mounts): p for p in paths}
        out = self._node({"read": list(mapped)})
        return {host: out["files"][inner] for inner, host in mapped.items()}

    def write_files(self, ops: list[tuple[Path, str | None]], expect: dict[Path, str | None]) -> None:
        """Apply ops in order, each file written atomically, unless an expected file changed."""
        out = self._node({
            "expect": {_map(p, self.gateway_mounts): sha for p, sha in expect.items()},
            "ops": [{"path": _map(p, self.gateway_mounts), "text": text} for p, text in ops],
        })
        if out.get("conflict"):
            raise TeamError(f"{out['conflict']} changed while teams.py was applying; run the command again")

    def validate(self, config_text: str) -> dict:
        """Run `openclaw config validate --json` on config_text placed next to openclaw.json."""
        result = self._compose(
            "run", "--rm", "-T", "-e", f"OPENCLAW_CONFIG_PATH={CHECK_PATH}", "--entrypoint", "sh",
            "openclaw-cli", "-c", VALIDATE_SH, label="openclaw config validate", check=False, input=config_text,
        )
        return json_out(result.stdout)

    def openclaw(self, *args: str) -> subprocess.CompletedProcess:
        return self._compose("run", "--rm", "-T", "openclaw-cli", *args, label=f"openclaw {' '.join(args)}", check=False)

    def _node(self, request: dict) -> dict:
        result = self._compose(
            "run", "--rm", "-T", "--entrypoint", "node", "openclaw-cli", "-e", NODE_HELPER,
            label="openclaw-cli node helper", input=json.dumps(request),
        )
        return json_out(result.stdout)

    def _compose(self, *args: str, label: str, check: bool = True, binary: bool = False,
                 input: str | bytes | None = None) -> subprocess.CompletedProcess:
        if input is None:
            input = b"" if binary else ""
        try:
            result = subprocess.run(
                ["docker", "compose", *args], cwd=self.paths.stack, input=input,
                capture_output=True, text=not binary, check=False,
            )
        except FileNotFoundError as exc:
            raise TeamError("docker: command not found") from exc
        if check and result.returncode != 0:
            stderr = result.stderr if isinstance(result.stderr, str) else result.stderr.decode(errors="replace")
            raise TeamError(f"`{label}` failed: {stderr.strip()}")
        return result


def _map(host: Path, mounts: dict[Path, str]) -> str:
    for root, inner in mounts.items():
        if host == root:
            return inner
        if root in host.parents:
            return f"{inner}/{host.relative_to(root).as_posix()}"
    raise TeamError(f"{host} is outside the folders mounted into the containers")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_teams_runner -v`
Expected: all PASS.

- [ ] **Step 5: Commit (only if the user approved commits)**

```bash
git add stacks/openclaw/teamlib/runner.py tests/test_teams_runner.py
git commit -m "feat: run team git and openclaw config access in stack containers"
```

---

### Task 7: Gateway wiring

Plan roster changes in `openclaw.json` (explicit ownership, `main` kept on its workspace, `systemAgent`), write `agents.d` files, validate before writing, refuse when the file changed meanwhile, remove teams, and install plugins that need no confirmation.

**Files:**
- Create: `stacks/openclaw/teamlib/gateway.py`
- Test: `tests/test_teams_gateway.py`

**Interfaces:**
- Consumes: `contract.resolve`, `contract.Resolved`, `tree.Tree`, `runner.json_out`, the runner interface, `Context`.
- Produces: `gateway.MAIN_WORKSPACE`, `INLINE = False`; `include(agent_id) -> dict`; `owned(team, roster: dict) -> set[str]`; `plan_roster(config: dict, team: str, entries: dict | None, *, inline=INLINE) -> dict`; `parse_texts(ctx, texts) -> tuple[dict, dict[str, str]]`; `resolve_tree(ctx, team, tree) -> Resolved`; `check_team(ctx, team, tree) -> Resolved` (contract plus `config validate`, writes nothing); `apply_team(ctx, team, tree) -> list[str]` (non-fatal problems; raises `TeamError` before writing when invalid); `remove_team(ctx, team) -> list[str]` (removed ids); `installed_plugins(ctx) -> list[dict]`; `missing_plugins(specs, installed) -> list[str]`; `unlisted_plugins(installed, specs) -> list[str]`; `install_plugins(ctx, specs) -> list[str]` (never raises).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_teams_gateway.py`:

```python
import json
import unittest
from pathlib import Path

from tests.teamfixtures import EXAMPLE, StackCase, commit_dir, git_in
from teamlib import TeamError, gateway
from teamlib.tree import from_rev

INC = gateway.include


class PlanRosterTest(unittest.TestCase):
    ENTRIES = {"tech-a": {"workspace": "/home/node/teams/tech/workspaces/a"}}

    def test_first_team_lists_implicit_main_and_sets_ownership(self):
        new = gateway.plan_roster({"gateway": {}}, "tech", self.ENTRIES)
        self.assertEqual(new["agents"], {
            "entries": {"main": {"workspace": "/home/node/.openclaw/workspace"}, "tech-a": INC("tech-a")},
            "ownership": "explicit",
            "defaults": {"systemAgent": {"agentId": "main"}},
        })

    def test_sole_agent_keeps_the_default_workspace(self):
        # The gateway's startup doctor writes main: {} before teams.py runs.
        new = gateway.plan_roster({"agents": {"entries": {"main": {"model": "m"}}}}, "tech", self.ENTRIES)
        self.assertEqual(new["agents"]["entries"]["main"], {"model": "m", "workspace": "/home/node/.openclaw/workspace"})
        custom = {"agents": {"entries": {"main": {}}, "defaults": {"workspace": "/data/ws"}}}
        self.assertEqual(gateway.plan_roster(custom, "tech", self.ENTRIES)["agents"]["entries"]["main"], {"workspace": "/data/ws"})

    def test_no_pinning_once_ownership_is_explicit(self):
        config = {"agents": {"ownership": "explicit", "entries": {"main": {}, "ops": {}}}}
        new = gateway.plan_roster(config, "tech", self.ENTRIES)
        self.assertEqual((new["agents"]["entries"]["main"], new["agents"]["entries"]["ops"]), ({}, {}))

    def test_inline_entries_for_validation(self):
        new = gateway.plan_roster({}, "tech", self.ENTRIES, inline=True)
        self.assertEqual(new["agents"]["entries"]["tech-a"], self.ENTRIES["tech-a"])

    def test_keeps_an_existing_roster_and_system_agent(self):
        config = {"agents": {"entries": {"ops": {"model": "x"}}, "defaults": {"systemAgent": {"agentId": "ops"}, "model": "y"}}}
        new = gateway.plan_roster(config, "tech", self.ENTRIES)
        self.assertEqual(list(new["agents"]["entries"]), ["ops", "tech-a"])
        self.assertEqual(new["agents"]["entries"]["ops"]["workspace"], "/home/node/.openclaw/workspace")
        self.assertEqual(new["agents"]["defaults"], {"systemAgent": {"agentId": "ops"}, "model": "y"})
        self.assertEqual(config["agents"]["entries"], {"ops": {"model": "x"}})

    def test_legacy_default_marker_keeps_ownership_unset(self):
        config = {"agents": {"entries": {"main": {"default": True}}}}
        self.assertNotIn("ownership", gateway.plan_roster(config, "tech", self.ENTRIES)["agents"])

    def test_refuses_ids_owned_by_someone_else(self):
        config = {"agents": {"entries": {"tech-a": {"model": "mine"}}}}
        with self.assertRaisesRegex(TeamError, "already used outside team tech: tech-a"):
            gateway.plan_roster(config, "tech", self.ENTRIES)

    def test_drops_agents_gone_from_the_team_and_leaves_others(self):
        config = {"agents": {"ownership": "explicit", "entries": {"main": {}, "tech-old": INC("tech-old"), "fin-a": INC("fin-a"), "tech-x": {"model": "m"}}}}
        new = gateway.plan_roster(config, "tech", self.ENTRIES)
        self.assertEqual(list(new["agents"]["entries"]), ["main", "fin-a", "tech-x", "tech-a"])
        removed = gateway.plan_roster(new, "tech", None)
        self.assertEqual(list(removed["agents"]["entries"]), ["main", "fin-a", "tech-x"])


class ApplyTest(StackCase):
    def setUp(self):
        super().setUp()
        repo = commit_dir(EXAMPLE, self.root / "example")
        self.tree = from_rev(git_in(repo), "HEAD")

    def test_writes_entries_and_includes(self):
        self.assertEqual(gateway.apply_team(self.ctx, "tech", self.tree), [])
        self.assertEqual(self.roster()["tech-coordinator"], INC("tech-coordinator"))
        written = json.loads((self.paths.agents_d / "tech-reviewer.json5").read_text())
        self.assertEqual(written["workspace"], "/home/node/teams/tech/workspaces/reviewer")
        self.assertEqual(self.config()["gateway"], {"mode": "local"})
        self.assertIn("agents: tech-coordinator, tech-reviewer (added tech-coordinator, tech-reviewer)", self.output())
        candidate, after = self.runner.validated
        self.assertEqual(candidate["agents"]["entries"]["tech-reviewer"]["name"], "Reviewer")
        self.assertEqual(after["agents"]["entries"]["tech-reviewer"], INC("tech-reviewer"))

    def test_second_apply_writes_nothing(self):
        gateway.apply_team(self.ctx, "tech", self.tree)
        before = self.paths.openclaw_json.stat().st_mtime_ns
        self.runner.validated.clear()
        gateway.apply_team(self.ctx, "tech", self.tree)
        self.assertEqual(self.paths.openclaw_json.stat().st_mtime_ns, before)
        self.assertEqual(self.runner.validated, [])

    def test_control_ui_edit_is_overwritten(self):
        gateway.apply_team(self.ctx, "tech", self.tree)
        (self.paths.agents_d / "tech-reviewer.json5").write_text('{"name": "edited in the UI"}\n')
        gateway.apply_team(self.ctx, "tech", self.tree)
        self.assertEqual(json.loads((self.paths.agents_d / "tech-reviewer.json5").read_text())["name"], "Reviewer")

    def test_rejected_settings_write_nothing(self):
        self.runner.validation = {"valid": False, "issues": [{"path": "agents.entries.tech-reviewer.name", "message": "Expected string"}]}
        with self.assertRaisesRegex(TeamError, "agents.entries.tech-reviewer.name: Expected string"):
            gateway.apply_team(self.ctx, "tech", self.tree)
        self.assertEqual(self.config(), {"gateway": {"mode": "local"}})
        self.assertEqual(list(self.paths.agents_d.iterdir()), [])

    def test_missing_openclaw_json(self):
        self.paths.openclaw_json.unlink()
        with self.assertRaisesRegex(TeamError, "install OpenClaw first"):
            gateway.apply_team(self.ctx, "tech", self.tree)

    def test_config_changed_meanwhile(self):
        original = self.runner.read_files

        def racing_read(paths):
            found = original(paths)
            self.write_config({"gateway": {"mode": "local"}, "edited": True})
            return found

        self.runner.read_files = racing_read
        with self.assertRaisesRegex(TeamError, "changed while teams.py was applying"):
            gateway.apply_team(self.ctx, "tech", self.tree)
        self.assertTrue(self.config()["edited"])

    def test_remove_team(self):
        gateway.apply_team(self.ctx, "tech", self.tree)
        (self.paths.agents_d / "tech-reviewer.json5.bak").write_text("{}")
        (self.paths.agents_d / "tech-reviewer.json5.bak.1").write_text("{}")
        self.assertEqual(gateway.remove_team(self.ctx, "tech"), ["tech-coordinator", "tech-reviewer"])
        self.assertEqual(list(self.roster()), ["main"])
        self.assertEqual(list(self.paths.agents_d.iterdir()), [])


class PluginTest(StackCase):
    def test_installs_missing_and_reports_refused(self):
        self.runner.installed = [{"id": "voice-call", "packageName": "@openclaw/voice-call", "origin": "global"}]
        self.runner.refuse = {"risky"}
        problems = gateway.install_plugins(self.ctx, ["@openclaw/voice-call@1.0.0", "calendar", "risky"])
        self.assertEqual(self.runner.openclaw_calls[1:], [("plugins", "install", "calendar"), ("plugins", "install", "risky")])
        self.assertEqual(len(problems), 1)
        self.assertIn("plugin risky was not installed (plugin requests capabilities", problems[0])
        self.assertIn("rune openclaw::cli plugins install risky", problems[0])

    def test_unlisted_plugins(self):
        installed = [
            {"id": "a", "packageName": "pkg-a", "origin": "global"},
            {"id": "b", "packageName": "pkg-b", "origin": "global"},
            {"id": "c", "origin": "bundled"},
        ]
        self.assertEqual(gateway.unlisted_plugins(installed, ["pkg-a@2"]), ["b"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_teams_gateway -v`
Expected: ERROR `ImportError: cannot import name 'gateway' from 'teamlib'`.

- [ ] **Step 3: Implement**

Create `stacks/openclaw/teamlib/gateway.py`:

```python
"""Apply a team's agents to openclaw.json and agents.d, and install its plugins."""

import copy
import hashlib
import json

from teamlib import TeamError, contract
from teamlib.context import GATEWAY_CONFIG, GATEWAY_TEAMS
from teamlib.runner import json_out
from teamlib.tree import Tree

MAIN_WORKSPACE = f"{GATEWAY_CONFIG}/workspace"
# Spike fallback: True writes merged entries straight into openclaw.json instead of agents.d includes.
INLINE = False


def include(agent_id: str) -> dict:
    return {"$include": f"./agents.d/{agent_id}.json5"}


def owned(team: str, roster: dict) -> set[str]:
    """Agent ids in roster that teams.py manages for team."""
    prefix = f"{GATEWAY_TEAMS}/{team}/workspaces/"
    return {
        key for key, value in roster.items()
        if key.startswith(f"{team}-") and isinstance(value, dict)
        and (value == include(key) or str(value.get("workspace", "")).startswith(prefix))
    }


def plan_roster(config: dict, team: str, entries: dict[str, dict] | None, *, inline: bool = INLINE) -> dict:
    """A copy of config with team's agents set to entries; None or {} removes the team."""
    config = copy.deepcopy(config)
    agents = config.setdefault("agents", {})
    roster = agents.setdefault("entries", {})
    mine = owned(team, roster)
    wanted = entries or {}
    taken = sorted(key for key in wanted if key in roster and key not in mine)
    if taken:
        raise TeamError(f"agent id(s) already used outside team {team}: {', '.join(taken)}")
    implicit_main = not roster
    for key in mine - wanted.keys():
        del roster[key]
    if not wanted:
        return config
    if implicit_main:
        # OpenClaw's implicit single agent; list it so it stays in the roster.
        roster["main"] = {"workspace": MAIN_WORKSPACE}
    for key, entry in wanted.items():
        roster[key] = copy.deepcopy(entry) if inline else include(key)
    legacy_default = any(isinstance(v, dict) and v.get("default") is True for v in roster.values())
    if agents.get("ownership") != "explicit" and not legacy_default:
        # Explicit ownership ends the sole agent's claim on the default workspace; pin it, as OpenClaw's own migration does.
        previous = [key for key in roster if key not in wanted]
        if len(previous) == 1 and _pinnable(roster[previous[0]]):
            pinned = (agents.get("defaults") or {}).get("workspace") or MAIN_WORKSPACE
            roster[previous[0]] = {**roster[previous[0]], "workspace": pinned}
        agents["ownership"] = "explicit"
    defaults = agents.get("defaults") or {}
    system = defaults.get("systemAgent") or {}
    if "main" in roster and not system.get("agentId"):
        agents["defaults"] = {**defaults, "systemAgent": {**system, "agentId": "main"}}
    return config


def _pinnable(entry) -> bool:
    return isinstance(entry, dict) and "$include" not in entry and not entry.get("workspace")


def parse_texts(ctx, texts: dict[str, str]) -> tuple[dict, dict[str, str]]:
    """Parse JSON5 texts: strict JSON in Python, anything else with OpenClaw's json5 package."""
    parsed, rest = {}, {}
    for name, text in texts.items():
        try:
            parsed[name] = json.loads(text)
        except json.JSONDecodeError:
            rest[name] = text
    errors = {}
    if rest:
        more, errors = ctx.runner.parse_json5(rest)
        parsed.update(more)
    return parsed, errors


def resolve_tree(ctx, team: str, tree: Tree) -> contract.Resolved:
    parsed, errors = parse_texts(ctx, tree.texts)
    return contract.resolve(team, tree, parsed, errors)


def check_team(ctx, team: str, tree: Tree) -> contract.Resolved:
    """Raise TeamError unless tree follows the contract and OpenClaw accepts its agents."""
    resolved = resolve_tree(ctx, team, tree)
    _, config, _ = _read_state(ctx, [])
    _validate(ctx, plan_roster(config, team, resolved.entries, inline=True))
    return resolved


def apply_team(ctx, team: str, tree: Tree) -> list[str]:
    """Write team's agents and install its plugins. Returns problems for the user (non-fatal).

    Raises TeamError before anything is written when the team is invalid.
    """
    resolved = resolve_tree(ctx, team, tree)
    for warning in resolved.warnings:
        ctx.say(f"  warning: {warning}")
    files = {} if INLINE else {ctx.paths.agents_d / f"{key}.json5": _dump(e) for key, e in sorted(resolved.entries.items())}
    text, config, current = _read_state(ctx, list(files))
    new = plan_roster(config, team, resolved.entries)
    before = owned(team, config.get("agents", {}).get("entries", {}))
    stale = sorted(before - resolved.entries.keys())
    ops = [(path, body) for path, body in files.items() if current.get(path) != body]
    if new != config:
        ops.append((ctx.paths.openclaw_json, _dump(new)))
    if not INLINE:
        ops += [(path, None) for key in stale for path in _agent_files(ctx, key)]
    problems = []
    if ops:
        _validate(ctx, plan_roster(config, team, resolved.entries, inline=True))
        ctx.runner.write_files(ops, expect={ctx.paths.openclaw_json: _sha(text)})
        added = sorted(resolved.entries.keys() - before)
        note = "".join(f" ({word} {', '.join(ids)})" for word, ids in (("added", added), ("removed", stale)) if ids)
        ctx.say(f"  agents: {', '.join(sorted(resolved.entries))}{note}")
        result = ctx.runner.validate(_dump(new))
        if not result.get("valid"):
            problems.append("openclaw.json does not validate after applying: " + "; ".join(_issues(result)))
    problems += install_plugins(ctx, resolved.plugins)
    return problems


def remove_team(ctx, team: str) -> list[str]:
    """Remove team's agents from openclaw.json and agents.d. Returns the removed ids."""
    text, config, _ = _read_state(ctx, [], required=False)
    if text is None:
        return []
    removed = sorted(owned(team, config.get("agents", {}).get("entries", {})))
    if not removed:
        return []
    ops = [(ctx.paths.openclaw_json, _dump(plan_roster(config, team, None)))]
    ops += [(path, None) for key in removed for path in _agent_files(ctx, key)]
    ctx.runner.write_files(ops, expect={ctx.paths.openclaw_json: _sha(text)})
    return removed


def installed_plugins(ctx) -> list[dict]:
    result = ctx.runner.openclaw("plugins", "list", "--json")
    if result.returncode:
        raise TeamError(f"`openclaw plugins list` failed: {result.stderr.strip()}")
    return [p for p in json_out(result.stdout).get("plugins", []) if isinstance(p, dict)]


def missing_plugins(specs: list[str], installed: list[dict]) -> list[str]:
    have = {name for p in installed for name in (p.get("id"), p.get("packageName")) if name}
    return [spec for spec in specs if not {spec, _bare(spec)} & have]


def unlisted_plugins(installed: list[dict], specs: list[str]) -> list[str]:
    """Installed (origin global) plugins that no team lists."""
    wanted = {spec for s in specs for spec in (s, _bare(s))}
    return sorted(
        p.get("id") or p.get("packageName") for p in installed
        if p.get("origin") == "global" and not {p.get("id"), p.get("packageName")} & wanted
    )


def install_plugins(ctx, specs: list[str]) -> list[str]:
    """Install missing plugins that need no confirmation; report the rest. Never raises."""
    if not specs:
        return []
    try:
        missing = missing_plugins(specs, installed_plugins(ctx))
    except TeamError as exc:
        return [str(exc)]
    problems = []
    for spec in missing:
        result = ctx.runner.openclaw("plugins", "install", spec)
        if result.returncode == 0:
            ctx.say(f"  installed plugin {spec}")
            continue
        detail = (result.stderr or result.stdout).strip().splitlines()
        reason = detail[-1] if detail else f"exit {result.returncode}"
        problems.append(f"plugin {spec} was not installed ({reason}); review it and run: rune openclaw::cli plugins install {spec}")
    return problems


def _read_state(ctx, extra: list, *, required: bool = True) -> tuple[str | None, dict, dict]:
    found = ctx.runner.read_files([ctx.paths.openclaw_json, *extra])
    text = found.pop(ctx.paths.openclaw_json)
    if text is None:
        if required:
            raise TeamError(f"{ctx.paths.openclaw_json} is missing; install OpenClaw first: python3 init.py openclaw")
        return None, {}, found
    try:
        config = json.loads(text)
    except json.JSONDecodeError:
        parsed, errors = ctx.runner.parse_json5({"openclaw.json": text})
        if errors:
            raise TeamError(f"openclaw.json: {errors['openclaw.json']}")
        config = parsed["openclaw.json"]
    return text, config, found


def _validate(ctx, config: dict) -> None:
    result = ctx.runner.validate(_dump(config))
    if not result.get("valid"):
        raise TeamError("OpenClaw rejects the team's settings:\n  - " + "\n  - ".join(_issues(result)))


def _issues(result: dict) -> list[str]:
    issues = [f"{i.get('path', '<root>')}: {i.get('message', '')}" for i in result.get("issues") or [] if isinstance(i, dict)]
    return issues or [str((result.get("error") or {}).get("message") or "config validate failed")]


def _agent_files(ctx, agent_id: str) -> list:
    """An agent's include file and the backups OpenClaw keeps when it writes through to it."""
    path = ctx.paths.agents_d / f"{agent_id}.json5"
    return [path, path.with_name(path.name + ".bak"), *(path.with_name(f"{path.name}.bak.{n}") for n in range(1, 5))]


def _bare(spec: str) -> str:
    """Package name without a version: name@1.2.3 -> name, @scope/name@1 -> @scope/name."""
    at = spec.rfind("@")
    return spec[:at] if at > 0 else spec


def _dump(value: dict) -> str:
    return json.dumps(value, indent=2) + "\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_teams_gateway -v`
Expected: all PASS.

- [ ] **Step 5: Commit (only if the user approved commits)**

```bash
git add stacks/openclaw/teamlib/gateway.py tests/test_teams_gateway.py
git commit -m "feat: apply team agents to openclaw.json and agents.d, install team plugins"
```

---

### Task 8: Deploy-key access

**Files:**
- Create: `stacks/openclaw/teamlib/access.py`
- Test: `tests/test_teams_access.py`

**Interfaces:**
- Consumes: `TeamError`, `registry.Team`, the runner interface (`git`, `keygen`), `Context`.
- Produces: `access.SCP_RE`, `SSH_RE`, `RETRY_DELAYS = (2, 5, 10)`; `NeedsKey(TeamError)` (one-line message with the key and the settings URL); `is_local(url) -> bool`; `repo_slug(url) -> tuple[str, str] | None`; `settings_url(host, path) -> str | None`; `ensure_access(ctx, team: Team, sleep=time.sleep) -> None`; `add_deploy_key(host, path, pub: Path, title: str) -> str | None` (`"gh"`, `"glab"` or None).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_teams_access.py`:

```python
import unittest
from unittest import mock

from tests.teamfixtures import StackCase, done
from teamlib import TeamError, access
from teamlib.registry import Team

TEAM = Team("tech", "git@github.com:org/openclaw-tech.git")


class UrlTest(unittest.TestCase):
    def test_kinds(self):
        self.assertTrue(access.is_local("examples/teams/tech"))
        self.assertTrue(access.is_local("/srv/teams/tech"))
        self.assertFalse(access.is_local("git@github.com:org/repo.git"))
        self.assertFalse(access.is_local("ssh://git@gitlab.example.com:2222/group/sub/repo.git"))
        self.assertFalse(access.is_local("https://github.com/org/repo.git"))

    def test_slug_and_settings_url(self):
        self.assertEqual(access.repo_slug("git@github.com:org/repo.git"), ("github.com", "org/repo"))
        self.assertEqual(access.repo_slug("ssh://git@gitlab.example.com:2222/group/sub/repo.git"), ("gitlab.example.com", "group/sub/repo"))
        self.assertIsNone(access.repo_slug("https://github.com/org/repo.git"))
        self.assertEqual(access.settings_url("github.com", "org/repo"), "https://github.com/org/repo/settings/keys/new")
        self.assertEqual(access.settings_url("gitlab.example.com", "g/r"), "https://gitlab.example.com/g/r/-/settings/repository#js-deploy-keys-settings")
        self.assertIsNone(access.settings_url("git.example.com", "g/r"))


class EnsureAccessTest(StackCase):
    def setUp(self):
        super().setUp()
        (self.paths.keys / "tech.pub").write_text("ssh-ed25519 AAAAkey openclaw-test-vm-tech\n")
        self.answers = []
        self.runner.git = self.fake_git
        self.which = mock.patch.object(access.shutil, "which", return_value="/usr/bin/gh").start()
        self.run = mock.patch.object(access.subprocess, "run", return_value=done()).start()
        self.addCleanup(mock.patch.stopall)

    def fake_git(self, cwd, *args, team=None, check=True, binary=False, input=None):
        self.assertEqual((args[0], team), ("ls-remote", "tech"))
        return self.answers.pop(0)

    def test_reachable_repo_needs_nothing(self):
        self.answers = [done()]
        access.ensure_access(self.ctx, TEAM, sleep=lambda s: None)
        self.run.assert_not_called()

    def test_adds_key_with_gh_and_retries(self):
        self.answers = [done(stderr="Permission denied (publickey).", code=128), done(stderr="denied", code=128), done()]
        access.ensure_access(self.ctx, TEAM, sleep=lambda s: None)
        add = self.run.call_args_list[-1].args[0]
        self.assertEqual(add, [
            "gh", "repo", "deploy-key", "add", str(self.paths.keys / "tech.pub"),
            "--repo", "org/openclaw-tech", "--title", "test-vm", "--allow-write",
        ])
        self.assertIn("added deploy key tech.pub to org/openclaw-tech with gh", self.output())

    def test_without_gh_prints_key_and_settings_url(self):
        self.which.return_value = None
        self.answers = [done(stderr="Permission denied (publickey).", code=128)]
        with self.assertRaises(access.NeedsKey) as caught:
            access.ensure_access(self.ctx, TEAM, sleep=lambda s: None)
        message = str(caught.exception)
        self.assertIn("Permission denied (publickey).", message)
        self.assertIn("https://github.com/org/openclaw-tech/settings/keys/new", message)
        self.assertIn("ssh-ed25519 AAAAkey openclaw-test-vm-tech", message)

    def test_glab_for_gitlab_hosts(self):
        self.which.return_value = "/usr/bin/glab"
        self.answers = [done(code=128), done()]
        access.ensure_access(self.ctx, Team("tech", "git@gitlab.example.com:g/tech.git"), sleep=lambda s: None)
        self.assertEqual(self.run.call_args_list[0].args[0], ["glab", "auth", "status", "--hostname", "gitlab.example.com"])
        self.assertEqual(self.run.call_args_list[-1].args[0][:3], ["glab", "deploy-key", "add"])
        self.assertIn("--can-push", self.run.call_args_list[-1].args[0])

    def test_gives_up_when_the_key_still_fails(self):
        self.answers = [done(code=128)] * 4
        with self.assertRaisesRegex(TeamError, "still refuses the deploy key"):
            access.ensure_access(self.ctx, TEAM, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_teams_access -v`
Expected: ERROR `ImportError: cannot import name 'access' from 'teamlib'`.

- [ ] **Step 3: Implement**

Create `stacks/openclaw/teamlib/access.py`:

```python
"""Deploy-key access to team repos: key generation, access checks, adding the key with gh or glab."""

import re
import shutil
import subprocess
import time
from pathlib import Path

from teamlib import TeamError
from teamlib.registry import Team

SCP_RE = re.compile(r"^(?:[\w.-]+@)?(?P<host>[\w.-]+):(?P<path>[^/].*)$")
SSH_RE = re.compile(r"^ssh://(?:[\w.-]+@)?(?P<host>[\w.-]+)(?::\d+)?/(?P<path>.+)$")
RETRY_DELAYS = (2, 5, 10)


class NeedsKey(TeamError):
    """The repo refuses the deploy key and it could not be added automatically."""


def is_local(url: str) -> bool:
    return "://" not in url and not SCP_RE.match(url)


def repo_slug(url: str) -> tuple[str, str] | None:
    """(host, owner/repo) of an SSH URL, or None."""
    match = SCP_RE.match(url) or SSH_RE.match(url)
    if not match:
        return None
    return match["host"], match["path"].removesuffix(".git").strip("/")


def settings_url(host: str, path: str) -> str | None:
    if host == "github.com":
        return f"https://github.com/{path}/settings/keys/new"
    if "gitlab" in host:
        return f"https://{host}/{path}/-/settings/repository#js-deploy-keys-settings"
    return None


def ensure_access(ctx, team: Team, sleep=time.sleep) -> None:
    """Make sure team.url accepts this VM's deploy key, adding the key with gh or glab when possible."""
    slug = repo_slug(team.url)
    if slug is None:
        raise TeamError(f"{team.url}: use an SSH URL (git@host:owner/repo.git); deploy keys work over SSH only")
    pub = ctx.paths.keys / f"{team.name}.pub"
    if not pub.is_file():
        ctx.runner.keygen(team.name, f"openclaw-{ctx.registry.vm}-{team.name}")
    ok, error = _reachable(ctx, team)
    if ok:
        return
    host, path = slug
    tool = add_deploy_key(host, path, pub, ctx.registry.vm)
    if tool is None:
        url = settings_url(host, path)
        where = f" at {url}" if url else ""
        raise NeedsKey(
            f"{team.url} refuses access ({error}). Add this deploy key with write access{where}, "
            f"then run the command again: {pub.read_text().strip()}"
        )
    ctx.say(f"  added deploy key {pub.name} to {path} with {tool}")
    for delay in RETRY_DELAYS:
        sleep(delay)
        ok, error = _reachable(ctx, team)
        if ok:
            return
    raise TeamError(f"{team.url} still refuses the deploy key ({error})")


def add_deploy_key(host: str, path: str, pub: Path, title: str) -> str | None:
    """Add pub as a writable deploy key with gh (github.com) or glab (other hosts). None when neither is usable."""
    if host == "github.com":
        tool, status = "gh", ["gh", "auth", "status", "--hostname", host]
        add = ["gh", "repo", "deploy-key", "add", str(pub), "--repo", path, "--title", title, "--allow-write"]
    else:
        tool, status = "glab", ["glab", "auth", "status", "--hostname", host]
        add = ["glab", "deploy-key", "add", str(pub), "--repo", f"{host}/{path}", "--title", title, "--can-push"]
    if not shutil.which(tool) or subprocess.run(status, capture_output=True).returncode != 0:
        return None
    result = subprocess.run(add, capture_output=True, text=True)
    if result.returncode:
        raise TeamError(f"{tool} could not add the deploy key: {result.stderr.strip()}")
    return tool


def _reachable(ctx, team: Team) -> tuple[bool, str]:
    result = ctx.runner.git(ctx.paths.teams, "ls-remote", "--heads", team.url, team.branch, team=team.name, check=False)
    lines = [line for line in result.stderr.strip().splitlines() if line.strip()]
    return result.returncode == 0, lines[-1] if lines else f"exit {result.returncode}"
```

If `glab` is installed, confirm the flags with `glab deploy-key add --help` (`--title`, `--can-push`, `--repo [HOST/]OWNER/REPO`); `gh repo deploy-key add --help` shows `--title`, `--allow-write`, `--repo`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m unittest tests.test_teams_access -v`
Expected: all PASS.

- [ ] **Step 5: Commit (only if the user approved commits)**

```bash
git add stacks/openclaw/teamlib/access.py tests/test_teams_access.py
git commit -m "feat: team deploy keys, added with gh or glab when logged in"
```

---

### Task 9: Git sync engine, commands and teams.py

The two-way sync (stage, commit, settings rescue, fetch, check, rebase with auto-resolve, push with retries), the commands (`add`, `sync`, `remove`, `list`, `apply`, local folders as sources) and the `teams.py` entry point.

**Files:**
- Create: `stacks/openclaw/teamlib/gitsync.py`
- Create: `stacks/openclaw/teamlib/commands.py`
- Create: `stacks/openclaw/teams.py`
- Test: `tests/test_teams_sync.py`, `tests/test_teams_commands.py`

**Interfaces:**
- Consumes: everything above: `registry`, `context`, `tree.from_rev`, `contract`, `scan.problem`/`load_secrets`, `gateway.check_team`/`apply_team`/`remove_team`/`parse_texts`/`installed_plugins`/`unlisted_plugins`, `access.is_local`/`ensure_access`/`NeedsKey`, `runner.DockerRunner`.
- Produces:
  - `gitsync.ATTEMPTS = 3`, `MAX_REBASE_STEPS = 200`, `nul(paths) -> str`; `Sync(runner, clone: Path, team: str, branch: str, vm: str, say)` with `upstream`, `git(*args, check=True, binary=False, input=None)`, `status(*pathspec) -> list[tuple[str, str]]`, `stage_workspaces(secrets)`, `commit(when: datetime) -> bool`, `rescue_settings(stamp) -> str | None`, `fetch()`, `rebase(stamp)`, `push() -> bool`, `integrate(check, stamp)`, `ahead_behind() -> tuple[int, int]`, `unpushed() -> list[str]`.
  - `commands.add(ctx, name, source, branch="main") -> list[str]`, `clone_team(ctx, team) -> list[str]`, `sync(ctx, name=None) -> bool`, `sync_team(ctx, team) -> list[str]`, `remove(ctx, name) -> None`, `list_teams(ctx) -> None`, `apply(ctx) -> None` (prints `action: team <name>: ...` lines), `local_folder(ctx, url) -> Path | None`.
  - `teams.main(argv=None, *, runner=None, stack=STACK, repo=REPO, out=None) -> int`; CLI `teams.py add <team> <source> [--branch B] | sync [team] | remove <team> | list | apply`.

- [ ] **Step 1: Write the failing sync tests**

Create `tests/test_teams_sync.py`:

```python
import json
import unittest

from tests.teamfixtures import EXAMPLE, StackCase, run_git
from teamlib import commands, gateway

KEY = "sk-ant-" + "b" * 30


class SyncCase(StackCase):
    """The example team added from a local folder, plus a person's clone of the same remote."""

    def setUp(self):
        super().setUp()
        self.assertEqual(commands.add(self.ctx, "tech", str(EXAMPLE)), [])
        self.clone = self.paths.clone("tech")
        self.person = self.root / "person"
        run_git(self.root, "clone", "-q", str(self.paths.remote("tech")), "person")
        run_git(self.person, "config", "user.name", "Person")
        run_git(self.person, "config", "user.email", "person@example.com")

    def person_push(self, files, message="person edit"):
        run_git(self.person, "pull", "-q", "--rebase", "origin", "main")
        for rel, text in files.items():
            path = self.person / rel
            if text is None:
                run_git(self.person, "rm", "-q", "-r", rel)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        run_git(self.person, "add", "-A")
        run_git(self.person, "commit", "-q", "-m", message)
        run_git(self.person, "push", "-q", "origin", "HEAD:main")

    def vm_write(self, rel, text):
        path = self.clone / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def upstream(self, *args):
        return run_git(self.paths.remote("tech"), *args).stdout

    def sync(self):
        self.out.truncate(0)
        self.out.seek(0)
        return commands.sync(self.ctx, "tech")


class SyncTest(SyncCase):
    def test_agent_memory_reaches_git(self):
        self.vm_write("workspaces/coordinator/memory/2026-10-16.md", "learned something\n")
        self.assertTrue(self.sync(), self.output())
        self.assertRegex(self.upstream("log", "-1", "--format=%s", "main"), r"^sync: test-vm \d{4}-\d{2}-\d{2} \d{2}:\d{2}\n$")
        self.assertEqual(self.upstream("log", "-1", "--format=%an", "main").strip(), "OpenClaw test-vm")
        self.assertIn("pushed 1 commit(s)", self.output())

    def test_persona_change_from_git_reaches_the_vm(self):
        self.person_push({"workspaces/reviewer/SOUL.md": "# Soul\n\nStricter.\n"})
        self.assertTrue(self.sync(), self.output())
        self.assertEqual((self.clone / "workspaces/reviewer/SOUL.md").read_text(), "# Soul\n\nStricter.\n")
        self.assertIn("pulled 1 commit(s)", self.output())

    def test_memory_edited_on_both_sides_merges(self):
        self.person_push({"workspaces/coordinator/MEMORY.md": "# Memory\n\n- from person\n"})
        self.vm_write("workspaces/coordinator/MEMORY.md", "# Memory\n\n- from agent\n")
        self.assertTrue(self.sync(), self.output())
        merged = (self.clone / "workspaces/coordinator/MEMORY.md").read_text()
        self.assertIn("- from person", merged)
        self.assertIn("- from agent", merged)
        self.assertNotIn("rescue", self.output())

    def test_persona_conflict_saves_vm_version_and_git_wins(self):
        self.person_push({"workspaces/reviewer/SOUL.md": "# Soul\n\nPerson version.\n"})
        self.vm_write("workspaces/reviewer/SOUL.md", "# Soul\n\nVM version.\n")
        self.vm_write("workspaces/reviewer/memory/note.md", "kept\n")
        self.assertTrue(self.sync(), self.output())
        self.assertEqual((self.clone / "workspaces/reviewer/SOUL.md").read_text(), "# Soul\n\nPerson version.\n")
        self.assertEqual((self.clone / "workspaces/reviewer/memory/note.md").read_text(), "kept\n")
        rescue = [b.strip() for b in self.upstream("branch", "--list", "rescue/*").splitlines()]
        self.assertEqual(len(rescue), 1, self.output())
        self.assertRegex(rescue[0], r"^rescue/test-vm/\d{8}-\d{6}$")
        self.assertIn("VM version", self.upstream("show", f"{rescue[0]}:workspaces/reviewer/SOUL.md"))
        self.assertIn("git wins conflicting lines", self.output())

    def test_file_deleted_in_git_and_edited_on_vm(self):
        self.person_push({"workspaces/reviewer/USER.md": None})
        self.vm_write("workspaces/reviewer/USER.md", "edited on the vm\n")
        self.assertTrue(self.sync(), self.output())
        self.assertFalse((self.clone / "workspaces/reviewer/USER.md").exists())
        self.assertIn("upstream kept for workspaces/reviewer/USER.md", self.output())

    def test_settings_edited_on_the_vm_are_rescued_and_reset(self):
        original = (self.clone / "team.json5").read_text()
        self.vm_write("team.json5", '{"description": "edited on vm"}\n')
        self.vm_write("notes.txt", "stray file\n")
        self.assertTrue(self.sync(), self.output())
        self.assertEqual((self.clone / "team.json5").read_text(), original)
        self.assertFalse((self.clone / "notes.txt").exists())
        branch = next(b.strip() for b in self.upstream("branch", "--list", "rescue/*").splitlines())
        self.assertTrue(branch.endswith("-settings"))
        self.assertIn("edited on vm", self.upstream("show", f"{branch}:team.json5"))
        self.assertIn("reset to git: notes.txt, team.json5", self.output())

    def test_secret_files_stay_local(self):
        self.ctx.secrets = ["gateway-token-123"]
        self.vm_write("workspaces/coordinator/memory/leak.md", f"key {KEY}\n")
        self.vm_write("workspaces/coordinator/memory/env.md", "token gateway-token-123\n")
        self.vm_write("workspaces/coordinator/memory/fine.md", "fine\n")
        self.assertTrue(self.sync(), self.output())
        files = self.upstream("ls-tree", "-r", "--name-only", "main")
        self.assertIn("workspaces/coordinator/memory/fine.md", files)
        self.assertNotIn("leak.md", files)
        self.assertNotIn("env.md", files)
        self.assertIn("not committed: workspaces/coordinator/memory/leak.md (looks like it contains a key or token)", self.output())
        self.assertTrue((self.clone / "workspaces/coordinator/memory/leak.md").exists())

    def test_skipped_file_changed_in_git_too(self):
        self.vm_write("workspaces/reviewer/USER.md", f"my key {KEY}\n")
        self.person_push({"workspaces/reviewer/USER.md": "# The people\n\nUpdated by a person.\n"})
        self.assertTrue(self.sync(), self.output())
        self.assertEqual((self.clone / "workspaces/reviewer/USER.md").read_text(), "# The people\n\nUpdated by a person.\n")
        self.assertIn("kept in `git stash` in data/teams/tech", self.output())
        self.assertIn(KEY, run_git(self.clone, "stash", "show", "-p").stdout)
        self.assertTrue(self.sync(), self.output())

    def test_nested_git_folder_is_skipped(self):
        run_git(self.clone / "workspaces/reviewer", "init", "-q")
        self.vm_write("workspaces/reviewer/memory/x.md", "x\n")
        self.vm_write("workspaces/coordinator/memory/y.md", "y\n")
        self.assertTrue(self.sync(), self.output())
        files = self.upstream("ls-tree", "-r", "--name-only", "main")
        self.assertIn("workspaces/coordinator/memory/y.md", files)
        self.assertNotIn("workspaces/reviewer/memory/x.md", files)
        self.assertIn("workspaces/reviewer holds its own .git", self.output())

    def test_broken_settings_in_git_do_not_go_live(self):
        self.vm_write("workspaces/coordinator/memory/today.md", "local\n")
        self.person_push({"team.json5": '{"workspace": "/elsewhere"}\n', "workspaces/reviewer/SOUL.md": "# new\n"})
        head = run_git(self.clone, "rev-parse", "HEAD").stdout
        self.assertFalse(self.sync())
        self.assertIn("must not set workspace", self.output())
        self.assertEqual((self.clone / "workspaces/reviewer/SOUL.md").read_text().splitlines()[0], "# Soul")
        self.assertNotEqual(run_git(self.clone, "rev-parse", "HEAD").stdout, head)
        self.assertEqual(run_git(self.clone, "rev-list", "--count", "origin/main..HEAD").stdout.strip(), "1")

    def test_settings_openclaw_rejects_do_not_go_live(self):
        self.person_push({"team.json5": '{"model": 5}\n'})
        self.runner.validation = {"valid": False, "issues": [{"path": "agents.entries.tech-coordinator.model", "message": "Invalid input"}]}
        self.assertFalse(self.sync())
        self.assertIn("agents.entries.tech-coordinator.model: Invalid input", self.output())
        self.assertNotIn("model", json.loads((self.paths.agents_d / "tech-coordinator.json5").read_text()))

    def test_agent_removed_in_git_leaves_the_gateway(self):
        self.vm_write("workspaces/reviewer/.openclaw/state.json", "{}\n")
        self.person_push({
            "workspaces/reviewer": None,
            "agents/reviewer.json5": None,
            "team.json5": '{"description": "Tech team agent"}\n',
        })
        self.assertTrue(self.sync(), self.output())
        self.assertTrue((self.clone / "workspaces/reviewer/.openclaw/state.json").exists())
        self.assertNotIn("tech-reviewer", self.roster())
        self.assertFalse((self.paths.agents_d / "tech-reviewer.json5").exists())
        self.assertIn("removed tech-reviewer", self.output())

    def test_agent_added_in_git_joins_the_gateway(self):
        self.person_push({"workspaces/writer/SOUL.md": "# Soul\n", "workspaces/writer/AGENTS.md": "# Notes\n"})
        self.assertTrue(self.sync(), self.output())
        self.assertEqual(self.roster()["tech-writer"], gateway.include("tech-writer"))

    def test_rejected_push_is_retried(self):
        self.vm_write("workspaces/coordinator/memory/a.md", "vm\n")
        fired = []

        def race(cwd, args):
            if args[:1] == ("push",) and args[-1] == "HEAD:refs/heads/main" and not fired:
                fired.append(True)
                self.person_push({"workspaces/reviewer/memory/b.md": "person\n"}, "race")

        self.runner.before_git = race
        self.assertTrue(self.sync(), self.output())
        files = self.upstream("ls-tree", "-r", "--name-only", "main")
        self.assertIn("workspaces/coordinator/memory/a.md", files)
        self.assertIn("workspaces/reviewer/memory/b.md", files)

    def test_offline_keeps_the_local_commit(self):
        self.vm_write("workspaces/coordinator/memory/a.md", "vm\n")
        self.paths.remote("tech").rename(self.root / "moved.git")
        self.assertFalse(self.sync())
        self.assertIn("fetch failed", self.output())
        self.assertIn("sync: test-vm", run_git(self.clone, "log", "-1", "--format=%s").stdout)

    def test_sync_all_continues_after_a_failing_team(self):
        commands.add(self.ctx, "fin", str(EXAMPLE))
        self.paths.remote("tech").rename(self.root / "moved.git")
        self.assertFalse(commands.sync(self.ctx))
        self.assertIn("==> team fin", self.output())
        self.assertIn("error: fetch failed", self.output())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Write the failing command tests**

Create `tests/test_teams_commands.py`:

```python
import io
import shutil
import unittest
from pathlib import Path
from unittest import mock

from tests.teamfixtures import EXAMPLE, REPO, StackCase, run_git
from teamlib import TeamError, access, commands, registry

import teams


class AddTest(StackCase):
    def test_add_local_folder(self):
        self.assertEqual(commands.add(self.ctx, "tech", str(EXAMPLE)), [])
        self.assertEqual(registry.load(self.paths.registry).teams["tech"].url, "examples/teams/tech")
        clone = self.paths.clone("tech")
        self.assertEqual(run_git(clone, "config", "pull.rebase").stdout.strip(), "true")
        self.assertEqual(run_git(clone, "config", "rebase.autoStash").stdout.strip(), "true")
        self.assertEqual(run_git(clone, "config", "user.email").stdout.strip(), "openclaw@test-vm")
        self.assertEqual(run_git(clone, "remote", "get-url", "origin").stdout.strip(), str(self.paths.remote("tech")))
        self.assertIn("tech-coordinator", self.roster())

    def test_contract_violation_leaves_nothing_behind(self):
        broken = self.root / "broken"
        shutil.copytree(EXAMPLE, broken)
        (broken / "workspaces" / "reviewer" / "SOUL.md").unlink()
        with self.assertRaisesRegex(TeamError, "workspaces/reviewer: missing SOUL.md"):
            commands.add(self.ctx, "tech", str(broken))
        self.assertFalse(self.paths.clone("tech").exists())
        self.assertFalse(self.paths.remote("tech").exists())
        self.assertFalse(self.paths.registry.exists())
        self.assertEqual(self.config(), {"gateway": {"mode": "local"}})

    def test_rejects_bad_names_and_sources(self):
        with self.assertRaisesRegex(TeamError, "must match"):
            commands.add(self.ctx, "Tech", str(EXAMPLE))
        with self.assertRaisesRegex(TeamError, "no such folder"):
            commands.add(self.ctx, "tech", str(self.root / "nope"))
        with self.assertRaisesRegex(TeamError, "use an SSH URL"):
            commands.add(self.ctx, "tech", "https://github.com/org/repo.git")

    def test_plugin_problems_do_not_undo_the_add(self):
        team = self.root / "plugged"
        shutil.copytree(EXAMPLE, team)
        (team / "plugins.json").write_text('{"plugins": ["risky"]}\n')
        self.runner.refuse = {"risky"}
        problems = commands.add(self.ctx, "tech", str(team))
        self.assertEqual(len(problems), 1)
        self.assertIn("tech", registry.load(self.paths.registry).teams)


class RemoveTest(StackCase):
    def test_refuses_unpushed_changes_then_removes(self):
        commands.add(self.ctx, "tech", str(EXAMPLE))
        (self.paths.clone("tech") / "workspaces/coordinator/memory/x.md").write_text("x\n")
        with self.assertRaisesRegex(TeamError, "not in git: workspaces/coordinator/memory/x.md"):
            commands.remove(self.ctx, "tech")
        self.assertTrue(commands.sync(self.ctx, "tech"))
        commands.remove(self.ctx, "tech")
        self.assertFalse(self.paths.clone("tech").exists())
        self.assertEqual(list(self.roster()), ["main"])
        self.assertEqual(registry.load(self.paths.registry).teams, {})
        self.assertIn("team tech removed: tech-coordinator, tech-reviewer", self.output())
        self.assertIn("kept: sessions in data/config/agents/<id>/, data/team-remotes/tech.git", self.output())

    def test_unknown_team(self):
        with self.assertRaisesRegex(TeamError, "unknown team nope"):
            commands.remove(self.ctx, "nope")


class ListTest(StackCase):
    def test_lists_agents_git_state_and_unlisted_plugins(self):
        commands.add(self.ctx, "tech", str(EXAMPLE))
        (self.paths.clone("tech") / "workspaces/coordinator/memory/x.md").write_text("x\n")
        self.runner.installed = [{"id": "stray", "origin": "global"}]
        commands.list_teams(self.ctx)
        out = self.output()
        self.assertIn("tech  examples/teams/tech (main)", out)
        self.assertIn("agents: tech-coordinator, tech-reviewer", out)
        self.assertIn("git: 0 to push, 0 to pull (as of the last fetch), 1 changed file(s)", out)
        self.assertIn("plugins no team lists: stray", out)

    def test_no_teams(self):
        commands.list_teams(self.ctx)
        self.assertIn("no teams yet", self.output())


class ApplyTest(StackCase):
    def test_new_vm_recreates_local_teams(self):
        self.ctx.registry.teams["tech"] = registry.Team("tech", "examples/teams/tech")
        commands.apply(self.ctx)
        self.assertTrue(self.paths.remote("tech").is_dir())
        self.assertIn("tech-reviewer", self.roster())
        self.assertNotIn("action:", self.output())
        commands.apply(self.ctx)
        self.assertNotIn("action:", self.output())

    def test_missing_access_becomes_an_action(self):
        self.ctx.registry.teams["tech"] = registry.Team("tech", "git@github.com:org/tech.git")
        with mock.patch.object(access, "ensure_access", side_effect=access.NeedsKey("git@github.com:org/tech.git refuses access.\nAdd key")):
            commands.apply(self.ctx)
        self.assertIn("action: team tech: git@github.com:org/tech.git refuses access. Add key\n", self.output())


class MainTest(StackCase):
    def main(self, *argv):
        out = io.StringIO()
        code = teams.main(list(argv), runner=self.runner, stack=self.paths.stack, repo=REPO, out=out)
        return code, out.getvalue()

    def test_add_sync_list_remove(self):
        self.assertEqual(self.main("add", "tech", str(EXAMPLE))[0], 0)
        self.assertEqual(self.main("sync")[0], 0)
        code, out = self.main("list")
        self.assertIn("tech-reviewer", out)
        self.assertEqual(self.main("remove", "tech")[0], 0)

    def test_errors_exit_non_zero(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(self.main("sync", "nope")[0], 1)
        self.assertIn("error: unknown team nope", err.getvalue())

    def test_apply_without_teams_does_nothing(self):
        self.assertEqual(self.main("apply"), (0, ""))
        self.assertEqual(self.runner.openclaw_calls, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_teams_sync tests.test_teams_commands -v`
Expected: ERROR `ImportError: cannot import name 'commands' from 'teamlib'` (and `No module named 'teams'`).

- [ ] **Step 4: Implement gitsync.py**

Conflict handling was checked on git 2.39 and 2.53: `rebase -X ours` lets upstream win conflicting hunks; the loop takes upstream's side of modify/delete conflicts; a commit that ends up empty is dropped by `rebase --continue`.

Create `stacks/openclaw/teamlib/gitsync.py`:

```python
"""Two-way git sync of one team clone: commit, rescue, fetch, rebase, push."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from teamlib import TeamError, scan
from teamlib.tree import Tree, from_rev

ATTEMPTS = 3
MAX_REBASE_STEPS = 200


def nul(paths: list[str]) -> str:
    return "\0".join(paths) + "\0"


@dataclass
class Sync:
    runner: object
    clone: Path
    team: str
    branch: str
    vm: str
    say: Callable[[str], None]

    @property
    def upstream(self) -> str:
        return f"origin/{self.branch}"

    def git(self, *args: str, check: bool = True, binary: bool = False, input: str | None = None):
        return self.runner.git(self.clone, *args, team=self.team, check=check, binary=binary, input=input)

    def status(self, *pathspec: str) -> list[tuple[str, str]]:
        """(XY, path) for every changed or untracked file, as `git status --porcelain` reports it."""
        out = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *pathspec).stdout
        items, entries = out.split("\0"), []
        index = 0
        while index < len(items):
            item = items[index]
            index += 1
            if not item:
                continue
            xy, path = item[:2], item[3:]
            if xy[0] in "RC":
                index += 1  # the next item is the rename source
            entries.append((xy, path))
        return entries

    def stage_workspaces(self, secrets: list[str]) -> None:
        """Stage changes under workspaces/, leaving out secrets, big files and nested repos."""
        nested = {p.parent.name for p in (self.clone / "workspaces").glob("*/.git")}
        for folder in sorted(nested):
            self.say(f"  warning: workspaces/{folder} holds its own .git; it is not synced until you remove it")
        paths = []
        for _, path in self.status("workspaces"):
            parts = path.split("/")
            if len(parts) > 1 and parts[1] in nested:
                continue
            reason = scan.problem(self.clone / path, secrets)
            if reason:
                self.say(f"  not committed: {path} ({reason})")
                continue
            paths.append(path)
        if paths:
            self.git("add", "--all", "--pathspec-from-file=-", "--pathspec-file-nul", input=nul(paths))

    def commit(self, when: datetime) -> bool:
        if self.git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return False
        self.git("commit", "-q", "--no-verify", "-m", f"sync: {self.vm} {when:%Y-%m-%d %H:%M}")
        return True

    def rescue_settings(self, stamp: str) -> str | None:
        """Move edits made on the VM outside workspaces/ to a rescue branch and reset them to git."""
        changed = self.status(".", ":(exclude)workspaces/")
        if not changed:
            return None
        paths = [path for _, path in changed]
        self.git("add", "--all", "--pathspec-from-file=-", "--pathspec-file-nul", input=nul(paths))
        tree = self.git("write-tree").stdout.strip()
        commit = self.git("commit-tree", tree, "-p", "HEAD", "-m", f"rescue: settings edited on {self.vm}").stdout.strip()
        branch = f"rescue/{self.vm}/{stamp}-settings"
        pushed = self._save(branch, commit)
        self.git("reset", "-q")
        tracked = [path for xy, path in changed if xy != "??"]
        untracked = [path for xy, path in changed if xy == "??"]
        if tracked:
            self.git("checkout", "HEAD", "--pathspec-from-file=-", "--pathspec-file-nul", input=nul(tracked))
        if untracked:
            self.git("clean", "-fq", "--", *untracked)
        where = branch if pushed else f"{branch} (local only: push failed)"
        self.say(f"  settings edited on this VM were saved to {where} and reset to git: {', '.join(sorted(paths))}")
        return branch

    def fetch(self) -> None:
        result = self.git("fetch", "-q", "origin", self.branch, check=False)
        if result.returncode:
            raise TeamError(f"fetch failed ({_last_line(result.stderr)}); local changes stay committed, sync again later")

    def rebase(self, stamp: str) -> None:
        """Rebase onto upstream. On conflict, save local commits to a rescue branch and let upstream win."""
        first = self.git("rebase", "-q", self.upstream, check=False)
        if first.returncode == 0:
            self._settle_autostash()
            return
        if not self._unmerged():
            self.git("rebase", "--abort", check=False)
            raise TeamError(f"rebase failed: {_last_line(first.stderr or first.stdout)}")
        self.git("rebase", "--abort")
        branch = f"rescue/{self.vm}/{stamp}"
        pushed = self._save(branch, "HEAD")
        resolved = []
        result = self.git("rebase", "-q", "-X", "ours", self.upstream, check=False)
        for _ in range(MAX_REBASE_STEPS):
            if result.returncode == 0:
                break
            unmerged = self._unmerged()
            if not unmerged:
                skipped = self.git("rebase", "--skip", check=False)
                if "no rebase in progress" in skipped.stderr.lower():
                    raise TeamError(f"rebase failed: {_last_line(result.stderr or result.stdout)}")
                result = skipped
                continue
            for path in unmerged:
                # During a rebase "ours" is upstream: keep its version, or its deletion.
                if self.git("cat-file", "-e", f"HEAD:{path}", check=False).returncode == 0:
                    self.git("checkout", "--ours", "--", path)
                    self.git("add", "--", path)
                else:
                    self.git("rm", "-q", "--", path)
                resolved.append(path)
            result = self.git("rebase", "--continue", check=False)
        if result.returncode != 0:
            self.git("rebase", "--abort", check=False)
            raise TeamError("rebase did not finish; local commits are kept in " + branch)
        self._settle_autostash()
        where = branch if pushed else f"{branch} (local only: push failed)"
        resolved_note = f"; upstream kept for {', '.join(sorted(set(resolved)))}" if resolved else ""
        self.say(f"  conflict with git: this VM's commits are saved in {where}, git wins conflicting lines{resolved_note}")

    def push(self) -> bool:
        """Push local commits. False when the remote moved on (fetch and rebase again)."""
        ahead = int(self.git("rev-list", "--count", f"{self.upstream}..HEAD").stdout.strip())
        if not ahead:
            return True
        result = self.git("push", "-q", "origin", f"HEAD:refs/heads/{self.branch}", check=False)
        if result.returncode == 0:
            self.say(f"  pushed {ahead} commit(s)")
            return True
        if "[rejected]" in result.stderr or "fetch first" in result.stderr or "non-fast-forward" in result.stderr:
            return False
        raise TeamError(f"push failed ({_last_line(result.stderr)}); local commits are kept")

    def integrate(self, check: Callable[[Tree], object], stamp: str) -> None:
        """Fetch, check incoming settings, rebase and push, retrying when the push is rejected."""
        for _ in range(ATTEMPTS):
            self.fetch()
            behind = int(self.git("rev-list", "--count", f"HEAD..{self.upstream}").stdout.strip())
            if behind:
                check(from_rev(self.git, self.upstream))
            self.rebase(stamp)
            if behind:
                self.say(f"  pulled {behind} commit(s)")
            if self.push():
                return
        raise TeamError(f"push was rejected {ATTEMPTS} times; local commits are kept, sync again later")

    def ahead_behind(self) -> tuple[int, int]:
        out = self.git("rev-list", "--left-right", "--count", f"HEAD...{self.upstream}").stdout.split()
        return int(out[0]), int(out[1])

    def unpushed(self) -> list[str]:
        """Local changes and commits that would be lost by deleting the clone."""
        pending = [path for _, path in self.status("workspaces")]
        ahead, _ = self.ahead_behind()
        return pending + ([f"{ahead} unpushed commit(s)"] if ahead else [])

    def _save(self, branch: str, rev: str) -> bool:
        self.git("branch", "-f", branch, rev)
        return self.git("push", "-q", "origin", f"{rev}:refs/heads/{branch}", check=False).returncode == 0

    def _settle_autostash(self) -> None:
        """Uncommitted local edits that clash with git stay in `git stash`; git's version goes live."""
        unmerged = self._unmerged()
        if not unmerged:
            return
        self.git("checkout", "HEAD", "--", *unmerged)
        self.say(
            f"  uncommitted edits to {', '.join(unmerged)} clash with git; git's version is live, "
            f"the edits are kept in `git stash` in data/teams/{self.team}"
        )

    def _unmerged(self) -> list[str]:
        return [p for p in self.git("diff", "--name-only", "-z", "--diff-filter=U").stdout.split("\0") if p]


def _last_line(text: str) -> str:
    lines = [line for line in (text or "").strip().splitlines() if line.strip()]
    return lines[-1] if lines else "no output"
```

- [ ] **Step 5: Implement commands.py**

Create `stacks/openclaw/teamlib/commands.py`:

```python
"""teams.py commands: add, sync, remove, list and apply."""

from datetime import datetime
from pathlib import Path

from teamlib import TeamError, access, contract, gateway, registry
from teamlib.gitsync import Sync
from teamlib.registry import Team
from teamlib.tree import from_rev


def add(ctx, name: str, source: str, branch: str = "main") -> list[str]:
    """team-add: clone, check, apply and record a team. Returns non-fatal problems."""
    registry.check_new_name(name, ctx.registry.teams)
    registry.check_branch(branch)
    url = source
    if access.is_local(source):
        folder = Path(source).expanduser()
        folder = (folder if folder.is_absolute() else Path.cwd() / folder).resolve()
        if not folder.is_dir():
            raise TeamError(f"{source}: no such folder, and not an SSH git URL")
        url = folder.relative_to(ctx.repo).as_posix() if folder.is_relative_to(ctx.repo) else str(folder)
    team = Team(name, url, branch)
    problems = clone_team(ctx, team)
    ctx.registry.teams[name] = team
    registry.save(ctx.paths.registry, ctx.registry)
    ctx.say(f"team {name} added; sync it with: rune openclaw::team-sync {name}")
    return problems


def clone_team(ctx, team: Team) -> list[str]:
    """Steps 2 to 5 of team-add. Leaves no clone behind when it fails."""
    clone = ctx.paths.clone(team.name)
    if clone.exists():
        raise TeamError(f"{clone} already exists; move it away first")
    folder = local_folder(ctx, team.url)
    bare = ctx.paths.remote(team.name)
    create_remote = folder is not None and not bare.exists()
    if folder is None:
        access.ensure_access(ctx, team)
    elif create_remote and not folder.is_dir():
        raise TeamError(f"{team.url}: folder not found, and data/team-remotes/{bare.name} does not exist")
    remote = ctx.runner.git_path(bare) if folder is not None else team.url
    try:
        if create_remote:
            _import(ctx, team, folder, remote)
        else:
            ctx.runner.git(ctx.paths.teams, "clone", "-q", "--branch", team.branch, remote, team.name, team=team.name)
            _configure(ctx, team)
        return gateway.apply_team(ctx, team.name, from_rev(_sync(ctx, team).git, "HEAD"))
    except BaseException:
        ctx.runner.remove_tree(clone)
        if create_remote:
            ctx.runner.remove_tree(bare)
        raise


def sync(ctx, name: str | None = None) -> bool:
    """team-sync for one team or all. False when any team failed or reported problems."""
    ok = True
    for team in _selected(ctx, name):
        ctx.say(f"==> team {team.name}")
        try:
            problems = sync_team(ctx, team)
        except TeamError as exc:
            ctx.say(f"  error: {exc}")
            ok = False
            continue
        for problem in problems:
            ctx.say(f"  problem: {problem}")
        ok = ok and not problems
    return ok


def sync_team(ctx, team: Team) -> list[str]:
    clone = ctx.paths.clone(team.name)
    if not clone.is_dir():
        raise TeamError(f"{clone} is missing; clone it with: python3 init.py openclaw")
    state = _sync(ctx, team)
    when = datetime.now()
    stamp = when.strftime("%Y%m%d-%H%M%S")
    state.stage_workspaces(ctx.secrets)
    state.commit(when)
    state.rescue_settings(stamp)
    state.integrate(lambda tree: gateway.check_team(ctx, team.name, tree), stamp)
    return gateway.apply_team(ctx, team.name, from_rev(state.git, "HEAD"))


def remove(ctx, name: str) -> None:
    """team-remove: refuse while changes are unpushed, then drop agents, clone and registry entry."""
    team = _selected(ctx, name)[0]
    clone = ctx.paths.clone(name)
    if clone.is_dir():
        pending = _sync(ctx, team).unpushed()
        if pending:
            shown = ", ".join(pending[:10]) + (", ..." if len(pending) > 10 else "")
            raise TeamError(
                f"team {name} has changes that are not in git: {shown}. "
                f"Run rune openclaw::team-sync {name} first, or delete those files"
            )
    removed = gateway.remove_team(ctx, name)
    if clone.is_dir():
        ctx.runner.remove_tree(clone)
    del ctx.registry.teams[name]
    registry.save(ctx.paths.registry, ctx.registry)
    ctx.say(f"team {name} removed" + (f": {', '.join(removed)}" if removed else ""))
    kept = ["sessions in data/config/agents/<id>/"]
    kept += [str(p.relative_to(ctx.paths.stack)) for p in (ctx.paths.keys / name, ctx.paths.remote(name)) if p.exists()]
    ctx.say(f"  kept: {', '.join(kept)}")


def list_teams(ctx) -> None:
    if not ctx.registry.teams:
        ctx.say("no teams yet; add one with: rune openclaw::team-add <team> <SSH git URL or folder>")
        return
    specs = []
    for team in ctx.registry.teams.values():
        ctx.say(f"{team.name}  {team.url} ({team.branch})")
        if not ctx.paths.clone(team.name).is_dir():
            ctx.say("  not cloned yet: python3 init.py openclaw")
            continue
        state = _sync(ctx, team)
        tree = from_rev(state.git, "HEAD")
        ahead, behind = state.ahead_behind()
        changed = len(state.status("workspaces"))
        ctx.say(f"  agents: {', '.join(contract.agent_id(team.name, f) for f in sorted(tree.workspaces))}")
        ctx.say(f"  git: {ahead} to push, {behind} to pull (as of the last fetch), {changed} changed file(s)")
        if "plugins.json" in tree.texts:
            parsed, _ = gateway.parse_texts(ctx, {"plugins.json": tree.texts["plugins.json"]})
            specs += contract.plugin_specs(parsed.get("plugins.json")) or []
    try:
        unlisted = gateway.unlisted_plugins(gateway.installed_plugins(ctx), specs)
    except TeamError as exc:
        ctx.say(f"plugins: {exc}")
        return
    if unlisted:
        ctx.say(f"plugins no team lists: {', '.join(unlisted)}")


def apply(ctx) -> None:
    """Installer step: clone missing teams, refresh the others. Problems become `action:` lines."""
    for team in ctx.registry.teams.values():
        ctx.say(f"==> team {team.name}")
        try:
            if ctx.paths.clone(team.name).is_dir():
                problems = gateway.apply_team(ctx, team.name, from_rev(_sync(ctx, team).git, "HEAD"))
            else:
                problems = clone_team(ctx, team)
        except TeamError as exc:
            problems = [str(exc)]
        for problem in problems:
            ctx.say(f"action: team {team.name}: {' '.join(problem.split())}")


def local_folder(ctx, url: str) -> Path | None:
    if not access.is_local(url):
        return None
    path = Path(url)
    return path if path.is_absolute() else ctx.repo / path


def _import(ctx, team: Team, folder: Path, remote: str) -> None:
    """Turn a plain folder into a bare repo in data/team-remotes and a clone of it."""
    ctx.runner.git(ctx.paths.remotes, "init", "-q", "--bare", "-b", team.branch, f"{team.name}.git")
    ctx.runner.git(ctx.paths.teams, "init", "-q", "-b", team.branch, team.name)
    _configure(ctx, team)
    ctx.runner.copy_in(folder, ctx.paths.clone(team.name))
    git = _sync(ctx, team).git
    git("add", "-A")
    git("commit", "-q", "--no-verify", "-m", f"import {team.url}")
    git("remote", "add", "origin", remote)
    git("push", "-q", "-u", "origin", f"HEAD:refs/heads/{team.branch}")


def _configure(ctx, team: Team) -> None:
    git = _sync(ctx, team).git
    for key, value in (
        ("pull.rebase", "true"),
        ("rebase.autoStash", "true"),
        ("user.name", ctx.registry.git_name),
        ("user.email", ctx.registry.git_email),
    ):
        git("config", key, value)


def _sync(ctx, team: Team) -> Sync:
    return Sync(ctx.runner, ctx.paths.clone(team.name), team.name, team.branch, ctx.registry.vm, ctx.say)


def _selected(ctx, name: str | None) -> list[Team]:
    if name is None:
        return list(ctx.registry.teams.values())
    if name not in ctx.registry.teams:
        known = ", ".join(ctx.registry.teams) or "none"
        raise TeamError(f"unknown team {name}; teams in teams.toml: {known}")
    return [ctx.registry.teams[name]]
```

- [ ] **Step 6: Implement teams.py**

Create `stacks/openclaw/teams.py`:

```python
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
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS (about 160 tests, under 10 s).

- [ ] **Step 8: Commit (only if the user approved commits)**

```bash
git add stacks/openclaw/teamlib/gitsync.py stacks/openclaw/teamlib/commands.py stacks/openclaw/teams.py tests/test_teams_sync.py tests/test_teams_commands.py
git commit -m "feat: team sync with auto-resolved conflicts, team commands and teams.py"
```

---

### Task 10: Installer, rune tasks and teams.toml

Create the team folders on install, fix their ownership from `team-git`, pin `tools.sessions.visibility: "tree"`, run `teams.py apply` as the last setup step, add the rune tasks, ignore `teams.toml`, ship `teams.toml.example`.

**Files:**
- Modify: `stacks/openclaw/component.toml`
- Modify: `stacks/openclaw/openclaw.rune`
- Modify: `.gitignore`
- Create: `stacks/openclaw/teams.toml.example`
- Test: `tests/test_components.py`

**Interfaces:**
- Consumes: `Step.script` (Task 2), `teams.py apply` (Task 9), service `team-git` (Task 1).
- Produces: rune tasks `openclaw::team-add team source`, `openclaw::team-sync *team`, `openclaw::teams`, `openclaw::team-remove team`.

- [ ] **Step 1: Write the failing test and update the existing ones**

In `class RepoManifestTest` of `tests/test_components.py`, the config step is no longer the last step and a script step has no `run`. Replace in `test_openclaw_manifest_uses_its_sidecar`:

```python
        config_step = openclaw.setup[-1].run
        for pinned in ('"gateway.bind","value":"loopback"', '"gateway.trustedProxies","value":["127.0.0.1"]',
                       '"logging.file","value":"/home/node/.openclaw/logs/openclaw.log"'):
            self.assertIn(pinned, config_step)
        self.assertNotIn("--no-deps", " ".join(step.run for step in openclaw.setup))
```

with:

```python
        config_step = next(step.run for step in openclaw.setup if step.run and "config set --batch-json" in step.run)
        for pinned in ('"gateway.bind","value":"loopback"', '"gateway.trustedProxies","value":["127.0.0.1"]',
                       '"logging.file","value":"/home/node/.openclaw/logs/openclaw.log"',
                       '"tools.sessions.visibility","value":"tree"'):
            self.assertIn(pinned, config_step)
        self.assertNotIn("--no-deps", " ".join(step.run for step in openclaw.setup if step.run))
```

In `test_openclaw_onboarding_keeps_keys_in_env_and_has_headless_variant` replace:

```python
        self.assertIn('"gateway.tailscale.mode","value":"off"', openclaw.setup[-1].run)
```

with:

```python
        self.assertIn('"gateway.tailscale.mode","value":"off"', " ".join(step.run for step in openclaw.setup if step.run))
```

Add after `test_openclaw_compose_wires_teams`:

```python
    def test_openclaw_manifest_sets_up_teams(self):
        openclaw = {c.name: c for c in components.discover(REPO)}["openclaw"]
        for folder in ("data/teams", "data/team-keys", "data/team-remotes", "data/config/agents.d"):
            self.assertIn(folder, openclaw.dirs)
        self.assertEqual(openclaw.setup[-1].script, "teams.py apply")
        self.assertIn("run --rm --user root --entrypoint chown team-git node:node /teams /keys /remotes",
                      [step.run for step in openclaw.setup])
        rune = (REPO / "stacks/openclaw/openclaw.rune").read_text()
        for task in ("team-add team source:", "team-sync *team:", "teams:", "team-remove team:"):
            self.assertIn(task, rune)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m unittest tests.test_components -v`
Expected: FAIL in `test_openclaw_manifest_uses_its_sidecar` (`tools.sessions.visibility` missing) and `test_openclaw_manifest_sets_up_teams`.

- [ ] **Step 3: Change component.toml**

```diff
--- a/stacks/openclaw/component.toml
+++ b/stacks/openclaw/component.toml
@@ -1,12 +1,12 @@
 description = "OpenClaw: personal AI assistant gateway on its own tailnet node"
 order = 20
-dirs = ["data/config", "data/config/logs", "data/workspace", "data/auth", "data/tailscale", "backups"]
+dirs = ["data/config", "data/config/logs", "data/config/agents.d", "data/workspace", "data/auth", "data/tailscale", "data/teams", "data/team-keys", "data/team-remotes", "backups"]
 
 [env]
 generate = ["OPENCLAW_GATEWAY_TOKEN"]
 ask = ["TS_AUTHKEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
 
-# Setup steps mirror upstream scripts/docker/setup.sh (v2026.9.8). No --no-deps:
+# Setup steps mirror upstream scripts/docker/setup.sh (v2026.10.3). No --no-deps:
 # one-off containers join the tailscale sidecar's namespace, so it must be running.
 
 # The image runs as uid 1000 ("node"). Fix ownership from a short-lived root
@@ -14,6 +14,11 @@ ask = ["TS_AUTHKEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
 [[setup]]
 run = '''run --rm --user root --entrypoint sh openclaw-gateway -c 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; export PATH; /usr/bin/find -P /home/node/.openclaw -xdev \( ! -path /home/node/.openclaw/workspace -o -prune \) -execdir /usr/bin/chown -h node:node {} +; /usr/bin/chown -h node:node /home/node/.config /home/node/backups; /usr/bin/find -P /home/node/.config/openclaw -xdev -execdir /usr/bin/chown -h node:node {} +; if [ -d /home/node/.openclaw/workspace/.openclaw ] && [ ! -L /home/node/.openclaw/workspace/.openclaw ]; then /usr/bin/find -P /home/node/.openclaw/workspace/.openclaw -xdev -execdir /usr/bin/chown -h node:node {} +; fi || true' '''
 
+# Team folders are written by uid 1000 from the team-git container (teams.py).
+# Only the mount points: clones and keys inside keep their owners.
+[[setup]]
+run = "run --rm --user root --entrypoint chown team-git node:node /teams /keys /remotes"
+
 # Onboarding stores API keys as env references (--secret-input-mode ref), so
 # they live only in .env. With --yes the headless variant picks the provider
 # from the keys in .env.
@@ -28,8 +33,15 @@ headless = '''run -T --rm --entrypoint sh openclaw-gateway -c 'if [ -n "$ANTHROP
 # loopback-bound gateway from 127.0.0.1 with forwarded headers, hence
 # trustedProxies. Managed Tailscale stays off: the sidecar owns Serve and the
 # image has no tailscale CLI. Logs go to the mounted state dir instead of /tmp.
+# Session visibility "tree": an agent sees its own sessions and the subagents
+# it spawned, not other teams' sessions.
+[[setup]]
+run = '''run --rm --entrypoint node openclaw-gateway dist/index.js config set --batch-json '[{"path":"gateway.mode","value":"local"},{"path":"gateway.bind","value":"loopback"},{"path":"gateway.trustedProxies","value":["127.0.0.1"]},{"path":"gateway.tailscale.mode","value":"off"},{"path":"gateway.auth.rateLimit","value":{"maxAttempts":10,"windowMs":60000,"lockoutMs":300000}},{"path":"logging.file","value":"/home/node/.openclaw/logs/openclaw.log"},{"path":"tools.sessions.visibility","value":"tree"}]' '''
+
+# Clone teams listed in teams.toml that are missing and refresh their agents
+# (TEAMS.md). Problems, such as a deploy key to add, go to Action needed.
 [[setup]]
-run = '''run --rm --entrypoint node openclaw-gateway dist/index.js config set --batch-json '[{"path":"gateway.mode","value":"local"},{"path":"gateway.bind","value":"loopback"},{"path":"gateway.trustedProxies","value":["127.0.0.1"]},{"path":"gateway.tailscale.mode","value":"off"},{"path":"gateway.auth.rateLimit","value":{"maxAttempts":10,"windowMs":60000,"lockoutMs":300000}},{"path":"logging.file","value":"/home/node/.openclaw/logs/openclaw.log"}]' '''
+script = "teams.py apply"
 
 [tailscale]
 service = "tailscale"
```

- [ ] **Step 4: Add the rune tasks**

```diff
--- a/stacks/openclaw/openclaw.rune
+++ b/stacks/openclaw/openclaw.rune
@@ -112,3 +112,24 @@ backup:
 [working-directory("stacks/openclaw")]
 url:
     @docker compose exec -T tailscale tailscale status --json 2>/dev/null | python3 -c 'import json, sys; s = json.loads(sys.stdin.read() or "{}"); d = s.get("CertDomains") or []; print("https://" + d[0] + "/" if d else "HTTPS certificates are off: enable them at https://login.tailscale.com/admin/dns" if s.get("BackendState") == "Running" else "node not running or not logged in: rune openclaw::up, then check TS_AUTHKEY in stacks/openclaw/.env")'
+
+# Add a team from an SSH git URL or a local folder, e.g. `rune openclaw::team-add tech examples/teams/tech`.
+[group("openclaw")]
+team-add team source:
+    python3 stacks/openclaw/teams.py add {{team}} {{source}}
+
+# Two-way git sync of one team, or of all teams.
+[group("openclaw")]
+team-sync *team:
+    python3 stacks/openclaw/teams.py sync {{team}}
+
+# List teams, their agents and git state.
+[group("openclaw")]
+teams:
+    python3 stacks/openclaw/teams.py list
+
+# Remove a team's agents and clone from this VM; its git repo is untouched.
+[group("openclaw")]
+[confirm("Remove this team's agents and clone from this VM?")]
+team-remove team:
+    python3 stacks/openclaw/teams.py remove {{team}}
```

- [ ] **Step 5: Ignore teams.toml and add the example**

```diff
--- a/.gitignore
+++ b/.gitignore
@@ -224,4 +224,5 @@ tailscale/data/
 dockge/data/
 stacks/*/data/
 stacks/*/backups/
+stacks/openclaw/teams.toml
 .rune/
```

Create `stacks/openclaw/teams.toml.example`:

```toml
# Teams this VM runs. Copy to teams.toml, or let `rune openclaw::team-add` create it.
# teams.toml is ignored by git; see TEAMS.md.

[git]
name = "OpenClaw agents-vm"     # author of sync commits
email = "openclaw@agents-vm"
vm = "agents-vm"                # in commit messages and rescue branch names; default: hostname

[teams.tech]
url = "git@github.com:org/openclaw-tech.git"   # SSH URL, or a local folder such as examples/teams/tech
branch = "main"
```

- [ ] **Step 6: Run tests and check the tasks**

Run: `python3 -m unittest discover -s tests && rune validate && rune --list | grep team`
Expected: all PASS; four `openclaw::team*` tasks listed.

Run: `rune openclaw::teams`
Expected (no `teams.toml` yet): `no teams yet; add one with: rune openclaw::team-add <team> <SSH git URL or folder>`

- [ ] **Step 7: Commit (only if the user approved commits)**

```bash
git add stacks/openclaw/component.toml stacks/openclaw/openclaw.rune .gitignore stacks/openclaw/teams.toml.example tests/test_components.py
git commit -m "feat: install team folders, pin session visibility, apply teams on install, team rune tasks"
```

---

### Task 11: Documentation

**Files:**
- Create: `stacks/openclaw/TEAMS.md`
- Modify: `stacks/openclaw/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: commands, behavior and paths from Tasks 1 to 10.
- Produces: the team repo contract and operating guide referenced by `teams.py --help`, rune task comments and the READMEs.

- [ ] **Step 1: Write TEAMS.md**

Create `stacks/openclaw/TEAMS.md`:

````markdown
# OpenClaw teams

A team is a group of OpenClaw agents whose personas, memory and settings live in one private git repo. Every team runs in this stack's single gateway, next to the default `main` agent. `teams.py` clones team repos, turns them into agents, and syncs them with git when you run `rune openclaw::team-sync`.

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
| `rune openclaw::team-remove <team>` | Remove the agents and the clone from this VM (refused while changes are not in git). The repo, sessions and deploy key stay |

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

`team-sync` runs these steps per team. Git runs in the `team-git` container, which holds the deploy key; the gateway never does.

1. Stage the changes the agents made under `workspaces/`. Files holding a secret (a value from `.env`, or something shaped like an API key or private key) and files over 5 MB are left out and listed.
2. Commit them as `sync: <vm> <date time>`.
3. Edits made on the VM outside `workspaces/` (settings, plugins.json) are saved to the branch `rescue/<vm>/<time>-settings` and reset to git. Settings change through git only.
4. Fetch. When git has new commits, check them first: the contract above, then `openclaw config validate`. If they fail, sync stops and nothing goes live; the gateway keeps the last good settings.
5. Rebase this VM's commits onto git's (see Conflicts).
6. Push; when someone pushed in between, fetch and rebase again, up to three times.
7. Apply: write each agent's merged settings to `data/config/agents.d/<agent id>.json5`, add or remove agents in `openclaw.json`, install missing plugins. The gateway picks this up without a restart.

Offline, sync keeps the local commit and stops that team; `team-sync` without a name carries on with the other teams and exits non-zero.

## Conflicts

| Case | Result |
|------|--------|
| Same memory file (`MEMORY.md`, `DREAMS.md`, `memory/`) changed on both sides | Both versions kept (`merge=union`), no conflict |
| Same lines of another file changed on both sides | This VM's commits are saved to `rescue/<vm>/<time>`, git's version wins those lines, the rest of the VM's edits stay |
| File deleted in git, edited on the VM (or the other way round) | Git's side wins; the VM's version is in the rescue branch |
| A file left out in step 1 also changed in git | Git's version goes live; the VM's edit stays in `git stash` in `data/teams/<team>` |
| Settings edited in the Control UI | Saved to the agent's `agents.d` file, then overwritten by the next sync |
| A workspace with its own `.git` | Reported and skipped until you delete that `.git` |

To look at a rescue branch: `git fetch` in your own clone, then `git log -p origin/rescue/<vm>/<time>`.

## Plugins

`plugins.json` names plugins the team needs. Plugins are gateway-wide: every agent of every team can use them. `teams.py` installs a missing plugin only when OpenClaw installs it without asking. Anything OpenClaw wants confirmed (a source outside ClawHub, extra capabilities) is reported with the command for a person to run after reviewing it, for example `rune openclaw::cli plugins install <spec>`. Plugins are never uninstalled automatically; `rune openclaw::teams` lists installed plugins no team names.

## A new VM

`teams.toml` (ignored by git, see `teams.toml.example`) lists the teams a VM runs. Copy it to the new VM, optionally with `data/team-keys/`, and run `python3 init.py openclaw`: the install clones every team and adds its agents. Teams it cannot reach show up under **Action needed** with the key to add.

## On the VM

| Path | Contents |
|------|----------|
| `stacks/openclaw/teams.toml` | Teams and the git identity of sync commits |
| `data/teams/<team>/` | The clone; the agents work in `workspaces/` (`/home/node/teams` in the containers) |
| `data/config/agents.d/` | Generated agent entries, one file per agent |
| `data/team-keys/` | Deploy keys and `known_hosts`; mounted only into `team-git` |
| `data/team-remotes/` | Bare repos made from local folders |

## Security

- One gateway is one trust boundary. Teams are organized apart (persona, memory, sessions, settings), not isolated: an operator sees every agent, and an agent can read other teams' files by absolute path until the sandbox is on.
- `tools.sessions.visibility` is pinned to `tree`: an agent sees its own sessions and the subagents it spawned, not other teams' sessions.
- Anyone who can push to a team repo changes that team's agents and can ask for gateway-wide plugins. Give push access accordingly.
- Deploy keys are credentials. Treat `data/team-keys/` like `.env`.
````

- [ ] **Step 2: Update the OpenClaw README**

```diff
--- a/stacks/openclaw/README.md
+++ b/stacks/openclaw/README.md
@@ -1,6 +1,6 @@
 # OpenClaw
 
-[OpenClaw](https://github.com/openclaw/openclaw) personal AI assistant gateway, pinned to `ghcr.io/openclaw/openclaw:2026.9.8`, on its own tailnet node.
+[OpenClaw](https://github.com/openclaw/openclaw) personal AI assistant gateway, pinned to `ghcr.io/openclaw/openclaw:2026.10.3`, on its own tailnet node. Groups of agents can be added as git-managed teams ([TEAMS.md](TEAMS.md)).
 
 ## How it runs
 
@@ -35,6 +35,9 @@ Everything is under `stacks/openclaw/`, ignored by git, owned by uid 1000.
 | `data/auth/` | `~/.config/openclaw` | Key for legacy encrypted OAuth credentials |
 | `data/tailscale/` | sidecar `/var/lib/tailscale` | Tailnet node identity |
 | `backups/` | `/home/node/backups` | `rune openclaw::backup` archives |
+| `data/teams/` | `/home/node/teams` | Team repo clones; team agents work in their `workspaces/` ([TEAMS.md](TEAMS.md)) |
+| `data/config/agents.d/` | `~/.openclaw/agents.d` | Generated team agent entries |
+| `data/team-keys/`, `data/team-remotes/` | `team-git` only | Team deploy keys; bare repos made from local folders |
 
 Treat `data/config`, `data/auth` and `backups/` as credentials: OAuth tokens are stored as plain text in SQLite. Never copy the live `*.sqlite` files; use `rune openclaw::backup` or stop the stack first. The workspace is a good candidate for its own private git repo.
 
@@ -45,6 +48,10 @@ Treat `data/config`, `data/auth` and `backups/` as credentials: OAuth tokens are
 - Vector search needs an embeddings provider. The default is OpenAI (`OPENAI_API_KEY`); with only an Anthropic key you get keyword search. Settings live under `memory.search` (`provider`, `model`, `sources`); this repo does not pin them.
 - Commands: `rune openclaw::cli memory status`, `rune openclaw::cli memory search "query"`, `rune openclaw::cli memory index --force`, `rune openclaw::cli memory reset`. Never delete the SQLite files to reset memory.
 
+## Teams
+
+Groups of agents with their own personas, memory and settings, each kept in a private git repo and synced by hand: `rune openclaw::team-add <team> <source>`, `team-sync [team]`, `teams`, `team-remove <team>`. Start from the example in `examples/teams/tech/`; the repo structure, sync rules and conflict handling are in [TEAMS.md](TEAMS.md).
+
 ## Plugins and skills
 
 - Plugins: `rune openclaw::cli plugins list|search|install <pkg>|enable <id>|disable <id>|update --all`, or the Control UI. Changes apply without a restart and persist in `data/config`. Per-plugin settings live under `plugins.entries.<id>`.
@@ -103,13 +110,13 @@ With `logging.file` set, logs rotate at 100 MB and keep five archives (about 600
 
 `rune openclaw::update` writes a verified backup, pulls the tags in `.env`, recreates the stack and runs `doctor --json`. On start the image migrates config and state itself (`doctor --fix`) and keeps `*.pre-startup-migration-*.bak` files; keep them with the backup, because a rollback needs the old image and the matching state.
 
-Plain version tags such as `2026.9.8` never change, so they get no OS security refreshes. Bump `OPENCLAW_IMAGE` to a newer release (or a dated `-rYYYYMMDD` tag when one exists) and run `rune openclaw::update`.
+Plain version tags such as `2026.10.3` never change, so they get no OS security refreshes. Bump `OPENCLAW_IMAGE` to a newer release (or a dated `-rYYYYMMDD` tag when one exists) and run `rune openclaw::update`.
 
-A later OpenClaw release (already on upstream `main`) needs `pid: "service:openclaw-gateway"` on the `openclaw-cli` service; without it `tui`, `status` and `backup` refuse to run. Add it when you upgrade past 2026.9.8.
+`openclaw-cli` shares the gateway's PID namespace (`pid: "service:openclaw-gateway"`); since 2026.10.1, `tui`, `status` and `backup` refuse to run without it.
 
 ## Optional features
 
-- **Browser control** (`openclaw browser`, the Control UI browser panel): Chromium must be inside the gateway image. Set `OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.9.8-browser` and run `rune openclaw::update`. Do not install Chromium at runtime; it does not survive recreation.
+- **Browser control** (`openclaw browser`, the Control UI browser panel): Chromium must be inside the gateway image. Set `OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.10.3-browser` and run `rune openclaw::update`. Do not install Chromium at runtime; it does not survive recreation.
 - **Sandbox** (agent tools in separate containers): needs a custom image with the Docker CLI, the sandbox image and the Docker socket; see the comment in `compose.yaml`.
 - **Gmail webhooks** (`openclaw webhooks gmail`): Pub/Sub needs a public HTTPS endpoint (Tailscale Funnel on its own port in `tailscale/serve.json`, routed only to the watcher on `127.0.0.1:8788`), a custom image with `gog`, and a working sandbox. Plain `/hooks` endpoints already work for callers on the tailnet with a dedicated hook token.
 
```

- [ ] **Step 3: Update the root README**

````diff
--- a/README.md
+++ b/README.md
@@ -90,6 +90,7 @@ rune dockge::up | down | logs | update
 rune openclaw::up | down | restart | logs | update
 rune openclaw::shell | tui | status | doctor | configure | onboard | backup | url
 rune openclaw::cli channels list
+rune openclaw::team-add <team> <source> | team-sync [team] | teams | team-remove <team>
 ```
 
 Without rune, use `docker compose` inside a component folder; the project name is the folder name, the same one Dockge uses.
@@ -104,6 +105,7 @@ tailscale/         the VM's tailnet node, outside stacks/ so Dockge cannot stop
 dockge/            Dockge itself, outside stacks/ so it never manages itself
 stacks/            DOCKGE_STACKS_DIR: one folder per tool
   openclaw/
+examples/teams/    example OpenClaw team repo (stacks/openclaw/TEAMS.md)
 tests/             python3 -m unittest discover -s tests
 .docs/             design specs and implementation plans
 ```
@@ -140,11 +142,14 @@ Each component folder holds:
    interactive = false             # true: skipped with --yes, unless headless is set
    # headless = "run -T --rm app migrate --yes"  # with interactive = true: used instead under --yes
 
+   [[setup]]                       # or `python3 <script>` in the component folder
+   script = "tool.py apply"        # stdout lines "action: ..." go to Action needed; non-zero exit fails
+
    [tailscale]                     # optional: print the URL of this stack's Tailscale sidecar
    service = "tailscale"
    ```
 
-   Placeholders in `set` and `run`: `{repo_dir}`, `{stacks_dir}`, `{uid}`, `{gid}`. Every key named in `[env]` must exist in `.env.example`. If a setup step ran while the stack was already running, `init.py` restarts it (except the sidecar).
+   Placeholders in `set`, `run` and `script`: `{repo_dir}`, `{stacks_dir}`, `{uid}`, `{gid}`. Every key named in `[env]` must exist in `.env.example`. If a setup step ran while the stack was already running, `init.py` restarts it (except the sidecar).
 3. To give the tool its own tailnet name, copy the `tailscale` service and the `tailscale/serve.json` folder from `stacks/openclaw/`, point `Proxy` at the app's port, set `network_mode: service:tailscale` on the app, add `TS_IMAGE`, `TS_AUTHKEY`, `TS_HOSTNAME` to `.env.example` and `TS_AUTHKEY` to `[env] ask`. If the app loads `.env` with `env_file`, set `TS_AUTHKEY: ""` in its `environment:`.
 4. Optionally add `stacks/<name>/<name>.rune` (use `[working-directory("stacks/<name>")]` on each task) and a `mod <name> "stacks/<name>/<name>.rune"` line in `Runefile`.
 5. `rune validate && python3 init.py <name>`
@@ -156,5 +161,6 @@ Stacks created from the Dockge UI work too. They have no `component.toml`, so `i
 - Dockge mounts the Docker socket, which is root-equivalent on the host. It listens on loopback only and is reachable through the tailnet. Its web console is enabled (`DOCKGE_ENABLE_CONSOLE: true` in `dockge/compose.yaml`), so anyone who can log in to Dockge gets a shell with Docker socket access, which means root on the VM. Use a strong Dockge password and limit who can reach `agents-vm` port 443 with a tailnet ACL, or set it to `false` and run `rune dockge::up` to turn the console off.
 - The Tailscale containers have `NET_ADMIN` and `/dev/net/tun`; the VM node shares the host network. Treat `tailscale/data/` and `stacks/*/data/tailscale/` as credentials (node keys).
 - OpenClaw's sandbox (agents in sibling containers) is off. Enabling it needs the Docker socket; see the comments in `stacks/openclaw/compose.yaml`.
+- OpenClaw teams share one gateway, which is one trust boundary: teams are organized apart, not isolated, and anyone who can push to a team repo changes that team's agents. Team deploy keys in `stacks/openclaw/data/team-keys/` are credentials. See `stacks/openclaw/TEAMS.md`.
 - OpenClaw keeps OAuth tokens in plain SQLite under `stacks/openclaw/data/`. Treat that folder and `stacks/openclaw/backups/` as credentials.
 - Dockge 1.5.0 (`louislam/dockge:1`) ignores `PUID`/`PGID`, so stacks *created* in the UI are owned by root until a release ships that support. Editing existing files keeps their owner. `DOCKGE_IMAGE=louislam/dockge:nightly` in `dockge/.env` has it today.
````

- [ ] **Step 4: Check for leftovers**

Run: `grep -rn "2026.9.8" . --exclude-dir=.docs --exclude-dir=.git; grep -rln $'\u2014' stacks examples tests installer README.md`
Expected: no output.

- [ ] **Step 5: Commit (only if the user approved commits)**

```bash
git add stacks/openclaw/TEAMS.md stacks/openclaw/README.md README.md
git commit -m "docs: team repo contract, sync rules and team tasks"
```

---

### Task 12: Live smoke test on 2026.10.3

Run the real flow against Docker in a throwaway copy of the repo, with its own compose project so nothing touches a real stack. The tailnet sidecar stays logged out; the gateway still runs.

**Files:** none (scratch only).

**Interfaces:**
- Consumes: everything.
- Produces: verification evidence for the final report.

- [ ] **Step 1: Make a scratch copy and set up the stack**

```bash
SMOKE=$(mktemp -d)/agents-infra
mkdir -p "$SMOKE" && git ls-files -z -co --exclude-standard | xargs -0 cp --parents -t "$SMOKE"
cd "$SMOKE/stacks/openclaw"
export COMPOSE_PROJECT_NAME=oc-smoke
cat > .env <<EOF
OPENCLAW_IMAGE=ghcr.io/openclaw/openclaw:2026.10.3
OPENCLAW_GATEWAY_TOKEN=$(openssl rand -hex 32)
ANTHROPIC_API_KEY=sk-ant-api03-smoke-placeholder-$(openssl rand -hex 16)
TS_HOSTNAME=oc-smoke
EOF
python3 - <<'EOF'
import os, shlex, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd().parent.parent))
from installer import components
c = components.load(Path.cwd())
for rel in c.dirs:
    Path(rel).mkdir(parents=True, exist_ok=True)
ctx = {"repo_dir": str(Path.cwd().parent.parent), "stacks_dir": str(Path.cwd().parent), "uid": str(os.getuid()), "gid": str(os.getgid())}
for step in c.setup:
    if step.run:
        subprocess.run(["docker", "compose", *shlex.split(components.expand(step.headless or step.run, ctx))], check=True)
EOF
docker compose up -d openclaw-gateway
until [ "$(docker inspect -f '{{.State.Health.Status}}' oc-smoke-openclaw-gateway-1)" = healthy ]; do sleep 3; done
cd "$SMOKE"
```

Expected: every setup step exits 0 (the config step reports `Updated 7 config paths`); the gateway becomes healthy.

- [ ] **Step 2: Add two teams**

```bash
FIN=$(mktemp -d)/fin && cp -r examples/teams/tech "$FIN"
printf '{"description": "Finance team agent", "subagents": {"allowAgents": ["reviewer"]}}\n' > "$FIN/team.json5"
python3 stacks/openclaw/teams.py add tech examples/teams/tech
python3 stacks/openclaw/teams.py add fin "$FIN"
python3 stacks/openclaw/teams.py add tech-ops examples/teams/tech; echo "exit=$?"
(cd stacks/openclaw && docker compose run --rm -T openclaw-cli agents list --json) | python3 -c 'import json,sys; print([(a["id"], a["workspace"]) for a in json.load(sys.stdin)])'
(cd stacks/openclaw && docker compose run --rm -T openclaw-cli config get tools.sessions.visibility)
```

Expected: both adds print `agents: ...` and `team ... added`; `tech-ops` fails with `overlaps team 'tech'` and `exit=1`; the list shows `main` on `/home/node/.openclaw/workspace` plus `tech-coordinator`, `tech-reviewer`, `fin-coordinator`, `fin-reviewer` on `/home/node/teams/<team>/workspaces/<folder>`; visibility prints `tree`.

- [ ] **Step 3: Sync in both directions**

```bash
cd stacks/openclaw
tg() { docker compose run --rm -T --entrypoint sh team-git -c "$1"; }
tg 'echo "learned" > /teams/tech/workspaces/coordinator/memory/2026-10-16.md'
tg 'git clone -q /remotes/tech.git /remotes/person && cd /remotes/person && printf "# Soul\n\nPerson version.\n" > workspaces/reviewer/SOUL.md && git -c user.name=P -c user.email=p@x commit -qam persona && git push -q origin HEAD:main'
cd "$SMOKE" && python3 stacks/openclaw/teams.py sync
```

Expected: `==> team tech` with `pulled 1 commit(s)` and `pushed 1 commit(s)`; `==> team fin` with no errors; exit 0. `stacks/openclaw/data/teams/tech/workspaces/reviewer/SOUL.md` holds `Person version.`

- [ ] **Step 4: Force a persona conflict**

```bash
cd stacks/openclaw
tg 'printf "# Soul\n\nVM version.\n" > /teams/tech/workspaces/reviewer/SOUL.md'
tg 'cd /remotes/person && git pull -q --rebase && printf "# Soul\n\nPerson again.\n" > workspaces/reviewer/SOUL.md && git -c user.name=P -c user.email=p@x commit -qam again && git push -q origin HEAD:main'
cd "$SMOKE" && python3 stacks/openclaw/teams.py sync tech
(cd stacks/openclaw && docker compose run --rm -T team-git -C /remotes/tech.git branch --list 'rescue/*')
```

Expected: `conflict with git: this VM's commits are saved in rescue/<vm>/<time>, git wins conflicting lines`; the rescue branch is listed; local `SOUL.md` holds `Person again.`

- [ ] **Step 5: Broken settings, then fixed**

```bash
cd stacks/openclaw
tg 'cd /remotes/person && git pull -q --rebase && printf "{\"model\": 5}\n" > team.json5 && git -c user.name=P -c user.email=p@x commit -qam broken && git push -q origin HEAD:main'
cd "$SMOKE" && python3 stacks/openclaw/teams.py sync tech; echo "exit=$?"
cd stacks/openclaw
tg 'cd /remotes/person && git checkout -q HEAD~1 -- team.json5 && git -c user.name=P -c user.email=p@x commit -qam fixed && git push -q origin HEAD:main'
cd "$SMOKE" && python3 stacks/openclaw/teams.py sync tech; echo "exit=$?"
```

Expected: first sync prints `OpenClaw rejects the team's settings:` with `agents.entries.tech-coordinator.model: Invalid input` and `exit=1`; the second prints `pulled 2 commit(s)` and `exit=0`.

- [ ] **Step 6: List, remove, apply on a "new VM"**

```bash
python3 stacks/openclaw/teams.py list
python3 stacks/openclaw/teams.py remove fin
(cd stacks/openclaw && docker compose run --rm --user root --entrypoint rm team-git -rf /teams/tech /remotes/tech.git /remotes/person)
python3 stacks/openclaw/teams.py apply
(cd stacks/openclaw && docker compose run --rm -T openclaw-cli agents list --json) | python3 -c 'import json,sys; print([a["id"] for a in json.load(sys.stdin)])'
```

Expected: `list` shows both teams with agents and `0 to push`; `remove fin` prints `team fin removed: fin-coordinator, fin-reviewer`; `apply` recreates `tech` from `examples/teams/tech` without `action:` lines; the final list is `['main', 'tech-coordinator', 'tech-reviewer']`.

- [ ] **Step 7: Clean up**

```bash
cd "$SMOKE/stacks/openclaw" && docker compose --profile cli --profile tools down
docker run --rm --user root -v "$SMOKE/stacks/openclaw:/s" --entrypoint rm ghcr.io/openclaw/openclaw:2026.10.3 -rf /s/data /s/backups /s/teams.toml
rm -rf "$SMOKE" "$FIN"
docker compose ls --all
```

Expected: no `oc-smoke` project left.

- [ ] **Step 8: Final checks in the repo**

Run: `cd <repo> && python3 -m unittest discover -s tests && rune validate && git status --short`
Expected: all tests PASS, three `ok` lines, only the intended files changed (or a clean tree when every task was committed).

The live delegation check (`tools.sessions.visibility: "tree"` blocks cross-team session reads while `subagents.allowAgents` delegation works) needs a real model key; the user runs it on the VM with a real team repo (spec Testing 6).
