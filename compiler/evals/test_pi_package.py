"""Pi package layout and install-path checks."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_COMPILER_ROOT = os.path.dirname(_HERE)
_TOOLS_DIR = os.path.join(_COMPILER_ROOT, "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import build_pi_package  # noqa: E402
from evals import oracle  # noqa: E402
from system2_compiler import ir  # noqa: E402
from system2_compiler.backends.pi import PiBackend  # noqa: E402


def _build(dest):
    with tempfile.TemporaryDirectory(prefix="pi-emission-") as staging:
        result = ir.compose(oracle.PLUGIN_ROOT, [], staging)
        if result.graph is None:
            raise AssertionError(result.errors)
        PiBackend().emit(result.graph, staging)
        build_pi_package.build(staging, dest)


class PiPackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pi-package-")
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        _build(self.root)
        self.package = json.loads(Path(self.root, "package.json").read_text())

    def test_package_contains_only_guidance_resources(self):
        self.assertEqual(
            set(os.listdir(self.root)),
            {"LICENSE", "README.md", "package.json", "prompts", "skills"},
        )
        self.assertEqual(
            self.package["pi"],
            {"skills": ["./skills"], "prompts": ["./prompts/*.md"]},
        )
        for key in ("extensions", "scripts", "dependencies", "peerDependencies"):
            self.assertNotIn(key, self.package)

    def test_package_has_the_simple_command_surface(self):
        self.assertEqual(
            set(os.listdir(os.path.join(self.root, "prompts"))),
            {"system2.md", "system2-doctor.md"},
        )
        skills = os.listdir(os.path.join(self.root, "skills"))
        self.assertEqual(len(skills), 13)
        self.assertTrue(all(name.startswith("system2-role-") for name in skills))

    def test_readme_documents_install_run_update_and_remove(self):
        readme = Path(self.root, "README.md").read_text()
        source = "git:github.com/DeliberateCode/System2"
        self.assertIn(f"pi install {source}", readme)
        self.assertIn("/system2 <your task>", readme)
        self.assertIn(f"pi update {source}", readme)
        self.assertIn(f"pi remove {source}", readme)
        self.assertIn("does not install an extension", readme)

    def test_repository_root_is_installable_from_git(self):
        root_manifest = json.loads(Path(
            oracle.PLUGIN_REPO_ROOT, "package.json"
        ).read_text())
        self.assertEqual(
            root_manifest["pi"],
            {
                "skills": ["./distributions/pi/skills"],
                "prompts": ["./distributions/pi/prompts/*.md"],
            },
        )
        self.assertIn("pi-package", root_manifest["keywords"])
        self.assertEqual(root_manifest["files"], ["distributions/pi"])
        self.assertEqual(root_manifest["name"], self.package["name"])


if __name__ == "__main__":
    unittest.main()
