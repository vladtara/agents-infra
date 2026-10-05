import itertools
import tempfile
import unittest
from pathlib import Path

from installer import components
from installer.components import ManifestError, SelectionError

OPENCLAW_TOML = """\
description = "Assistant"
order = 20
dirs = ["data/config"]

[env]
set = { HOST = "{uid}" }
generate = ["TOKEN"]
ask = ["API_KEY"]

[[setup]]
run = "run --rm gateway chown"

[[setup]]
run = "run --rm gateway onboard"
once = "data/.onboarded"
interactive = true

[tailscale]
service = "tailscale"
"""


class RepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self._ids = itertools.count()

    def tearDown(self):
        self._tmp.cleanup()

    def add(self, rel, toml, env_example="HOST=\nTOKEN=\nAPI_KEY=\n", compose=True):
        path = self.repo / rel
        path.mkdir(parents=True)
        (path / "component.toml").write_text(toml)
        if env_example is not None:
            (path / ".env.example").write_text(env_example)
        if compose:
            (path / "compose.yaml").write_text("services: {}\n")
        return path


class LoadTest(RepoCase):
    def test_loads_full_manifest(self):
        comp = components.load(self.add("stacks/openclaw", OPENCLAW_TOML))
        self.assertEqual(comp.name, "openclaw")
        self.assertEqual(comp.description, "Assistant")
        self.assertEqual(comp.order, 20)
        self.assertEqual(comp.dirs, ("data/config",))
        self.assertEqual(comp.env_set, {"HOST": "{uid}"})
        self.assertEqual(comp.env_generate, ("TOKEN",))
        self.assertEqual(comp.env_ask, ("API_KEY",))
        self.assertEqual(len(comp.setup), 2)
        self.assertIsNone(comp.setup[0].once)
        self.assertFalse(comp.setup[0].interactive)
        self.assertEqual(comp.setup[1].once, "data/.onboarded")
        self.assertTrue(comp.setup[1].interactive)
        self.assertEqual(comp.tailscale_service, "tailscale")

    def test_minimal_manifest_defaults(self):
        comp = components.load(self.add("stacks/tiny", 'description = "x"\n', env_example=None))
        self.assertEqual(comp.order, 100)
        self.assertEqual((comp.dirs, comp.setup, comp.env_set), ((), (), {}))
        self.assertIsNone(comp.tailscale_service)

    def assert_invalid(self, toml, message, **kwargs):
        with self.assertRaisesRegex(ManifestError, message):
            components.load(self.add(f"stacks/bad-{next(self._ids)}", toml, **kwargs))

    def test_headless_alternative_for_interactive_steps(self):
        toml = 'description = "x"\n[[setup]]\nrun = "run a"\ninteractive = true\nheadless = "run b"\n'
        comp = components.load(self.add("stacks/hl", toml, env_example=None))
        self.assertEqual(comp.setup[0].headless, "run b")
        self.assert_invalid('description = "x"\n[[setup]]\nrun = "a"\nheadless = "b"\n', "headless.*interactive")
        self.assert_invalid('description = "x"\n[[setup]]\nrun = "a"\ninteractive = true\nheadless = 1\n', "headless")

    def test_step_note(self):
        comp = components.load(self.add("stacks/nt", 'description = "x"\n[[setup]]\nrun = "a"\nnote = "hi"\n', env_example=None))
        self.assertEqual(comp.setup[0].note, "hi")
        self.assert_invalid('description = "x"\n[[setup]]\nrun = "a"\nnote = 1\n', "note")

    def test_rejects_unknown_keys(self):
        self.assert_invalid('description = "x"\nport = 1\n', "unknown key.*port")
        self.assert_invalid('description = "x"\n[env]\nsecret = []\n', "unknown key.*secret")
        self.assert_invalid('description = "x"\n[[setup]]\nrun = "a"\nwhen = true\n', "unknown key.*when")

    def test_requires_description_and_compose(self):
        self.assert_invalid("order = 1\n", "description")
        self.assert_invalid('description = "x"\n', "compose.yaml", compose=False)

    def test_rejects_bad_types(self):
        self.assert_invalid('description = "x"\norder = "1"\n', "order")
        self.assert_invalid('description = "x"\ndirs = "data"\n', "dirs")
        self.assert_invalid('description = "x"\n[tailscale]\nservice = 1\n', "tailscale.service")
        self.assert_invalid('description = "x"\n[tailscale]\nhttps_port = 443\n', "unknown key.*https_port")

    def test_rejects_paths_outside_component(self):
        self.assert_invalid('description = "x"\ndirs = ["../etc"]\n', "dirs")
        self.assert_invalid('description = "x"\ndirs = ["/etc"]\n', "dirs")
        self.assert_invalid('description = "x"\n[[setup]]\nrun = "a"\nonce = "../x"\n', "once")

    def test_env_keys_must_exist_in_example(self):
        self.assert_invalid('description = "x"\n[env]\ngenerate = ["NOPE"]\n', "NOPE")
        self.assert_invalid('description = "x"\n[env]\nask = ["A"]\n', ".env.example", env_example=None)

    def test_rejects_invalid_folder_name(self):
        with self.assertRaisesRegex(ManifestError, "name"):
            components.load(self.add("stacks/Bad.Name", 'description = "x"\n'))

    def test_reports_toml_syntax_errors(self):
        self.assert_invalid("description = \n", "component.toml")


class DiscoverTest(RepoCase):
    def test_finds_infra_and_stacks_sorted_by_order_then_name(self):
        self.add("dockge", 'description = "d"\norder = 10\n')
        self.add("tailscale", 'description = "t"\norder = 5\n')
        self.add("stacks/zeta", 'description = "z"\norder = 20\n')
        self.add("stacks/alpha", 'description = "a"\norder = 20\n')
        (self.repo / "stacks" / "not-a-component").mkdir()
        names = [c.name for c in components.discover(self.repo)]
        self.assertEqual(names, ["tailscale", "dockge", "alpha", "zeta"])

    def test_rejects_duplicate_names(self):
        self.add("dockge", 'description = "d"\n')
        self.add("stacks/dockge", 'description = "d"\n')
        with self.assertRaisesRegex(ManifestError, "duplicate"):
            components.discover(self.repo)


class SelectTest(RepoCase):
    def setUp(self):
        super().setUp()
        self.add("dockge", 'description = "d"\norder = 10\n')
        self.add("stacks/openclaw", 'description = "o"\norder = 20\n')
        self.all = components.discover(self.repo)

    def test_empty_selection_means_all(self):
        self.assertEqual(components.select(self.all, []), self.all)

    def test_keeps_install_order(self):
        names = [c.name for c in components.select(self.all, ["openclaw", "dockge"])]
        self.assertEqual(names, ["dockge", "openclaw"])

    def test_unknown_name_lists_available(self):
        with self.assertRaisesRegex(SelectionError, "nope.*dockge, openclaw"):
            components.select(self.all, ["nope"])


class ExpandTest(unittest.TestCase):
    def test_replaces_known_placeholders_only(self):
        text = '[{"path":"x","value":"{uid}"}] {unknown}'
        out = components.expand(text, {"uid": "1000"})
        self.assertEqual(out, '[{"path":"x","value":"1000"}] {unknown}')


REPO = Path(__file__).resolve().parent.parent


class RepoManifestTest(unittest.TestCase):
    def test_shipped_components_load_in_install_order(self):
        found = {c.name: c for c in components.discover(REPO)}
        self.assertEqual(list(found), ["tailscale", "dockge", "openclaw"])
        self.assertEqual(found["tailscale"].tailscale_service, "tailscale")
        self.assertEqual(found["tailscale"].env_ask, ("TS_AUTHKEY",))
        self.assertIsNone(found["dockge"].tailscale_service)

    def test_openclaw_manifest_uses_its_sidecar(self):
        openclaw = {c.name: c for c in components.discover(REPO)}["openclaw"]
        self.assertEqual(openclaw.tailscale_service, "tailscale")
        self.assertEqual(openclaw.env_ask, ("TS_AUTHKEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"))
        self.assertIn("backups", openclaw.dirs)
        self.assertIn("data/config/logs", openclaw.dirs)
        config_step = openclaw.setup[-1].run
        for pinned in ('"gateway.bind","value":"loopback"', '"gateway.trustedProxies","value":["127.0.0.1"]',
                       '"logging.file","value":"/home/node/.openclaw/logs/openclaw.log"'):
            self.assertIn(pinned, config_step)
        self.assertNotIn("--no-deps", " ".join(step.run for step in openclaw.setup))

    def test_openclaw_onboarding_keeps_keys_in_env_and_has_headless_variant(self):
        openclaw = {c.name: c for c in components.discover(REPO)}["openclaw"]
        onboard = next(step for step in openclaw.setup if step.once == "data/.onboarded")
        self.assertIn("--secret-input-mode ref", onboard.run)
        self.assertIn("--non-interactive --accept-risk", onboard.headless)
        self.assertIn("--secret-input-mode ref", onboard.headless)
        self.assertIn('"gateway.tailscale.mode","value":"off"', openclaw.setup[-1].run)
        self.assertIn("--secret-input-mode ref", (REPO / "stacks/openclaw/openclaw.rune").read_text())
        self.assertIn("Gateway not detected yet", onboard.note)

if __name__ == "__main__":
    unittest.main()
