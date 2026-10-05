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
set = { HOST = "{ts_hostname}" }
generate = ["TOKEN"]
ask = ["API_KEY"]

[[setup]]
run = "run --rm gateway chown"

[[setup]]
run = "run --rm gateway onboard"
once = "data/.onboarded"
interactive = true

[tailscale]
https_port = 443
target = "http://127.0.0.1:18789"
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
        self.assertEqual(comp.env_set, {"HOST": "{ts_hostname}"})
        self.assertEqual(comp.env_generate, ("TOKEN",))
        self.assertEqual(comp.env_ask, ("API_KEY",))
        self.assertEqual(len(comp.setup), 2)
        self.assertIsNone(comp.setup[0].once)
        self.assertFalse(comp.setup[0].interactive)
        self.assertEqual(comp.setup[1].once, "data/.onboarded")
        self.assertTrue(comp.setup[1].interactive)
        self.assertEqual(comp.tailscale_port, 443)
        self.assertEqual(comp.tailscale_target, "http://127.0.0.1:18789")

    def test_minimal_manifest_defaults(self):
        comp = components.load(self.add("stacks/tiny", 'description = "x"\n', env_example=None))
        self.assertEqual(comp.order, 100)
        self.assertEqual((comp.dirs, comp.setup, comp.env_set), ((), (), {}))
        self.assertIsNone(comp.tailscale_port)

    def assert_invalid(self, toml, message, **kwargs):
        with self.assertRaisesRegex(ManifestError, message):
            components.load(self.add(f"stacks/bad-{next(self._ids)}", toml, **kwargs))

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
        self.assert_invalid('description = "x"\n[tailscale]\nhttps_port = 0\ntarget = "t"\n', "https_port")

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
    def test_finds_dockge_and_stacks_sorted_by_order_then_name(self):
        self.add("dockge", 'description = "d"\norder = 10\n')
        self.add("stacks/zeta", 'description = "z"\norder = 20\n')
        self.add("stacks/alpha", 'description = "a"\norder = 20\n')
        (self.repo / "stacks" / "not-a-component").mkdir()
        names = [c.name for c in components.discover(self.repo)]
        self.assertEqual(names, ["dockge", "alpha", "zeta"])

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
        text = '[{"path":"x","value":"https://{ts_hostname}"}] {unknown}'
        out = components.expand(text, {"ts_hostname": "vm.ts.net"})
        self.assertEqual(out, '[{"path":"x","value":"https://vm.ts.net"}] {unknown}')


if __name__ == "__main__":
    unittest.main()
