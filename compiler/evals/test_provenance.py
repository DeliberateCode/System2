"""Distribution provenance producer-input and artifact-tree contracts."""

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_COMPILER_ROOT = os.path.dirname(_THIS_DIR)
_TOOLS_DIR = os.path.join(_COMPILER_ROOT, "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)


def _load_tool(name):
    path = os.path.join(_TOOLS_DIR, name + ".py")
    spec = importlib.util.spec_from_file_location("tools_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


provenance = _load_tool("_provenance")
regen_all = _load_tool("regen_all")


def _manual_hash(entries):
    digest = hashlib.sha256()
    for label, payload in sorted(entries):
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


class SourceDigestTest(unittest.TestCase):
    def test_hash_protocol_has_an_independent_manual_control(self):
        with tempfile.TemporaryDirectory() as root:
            one = os.path.join(root, "one")
            two = os.path.join(root, "two")
            with open(one, "wb") as fh:
                fh.write(b"alpha")
            with open(two, "wb") as fh:
                fh.write(b"beta")
            inputs = [("zeta", two), ("alpha", one)]
            self.assertEqual(
                provenance.source_sha256(inputs),
                _manual_hash([("zeta", b"beta"), ("alpha", b"alpha")]),
            )
            self.assertNotEqual(
                provenance.source_sha256(inputs),
                _manual_hash([("zeta", b"beta!"), ("alpha", b"alpha")]),
                "the manual negative control must distinguish a changed byte",
            )

    def test_channel_inputs_cover_effective_producers_with_stable_labels(self):
        ctx = regen_all._context()
        codex = regen_all._distribution_inputs("codex", ctx)
        pi = regen_all._distribution_inputs("pi", ctx)
        codex_labels = {label for label, _path in codex}
        pi_labels = {label for label, _path in pi}

        common = {
            "plugin", "lowering/ir", "lowering/system2_compiler_init",
            "backend/base", "backend/degradation", "backend/enforcement",
            "backend/yaml", "generator/regen_all", "generator/provenance",
            "generator/build_bundle_helpers", "metadata/channel_version",
            "metadata/compiler_project",
        }
        self.assertLessEqual(common, codex_labels)
        self.assertLessEqual(common, pi_labels)
        self.assertIn("backend/codex", codex_labels)
        self.assertIn("backend/capabilities/codex", codex_labels)
        self.assertIn("backend/pi", pi_labels)
        self.assertIn("backend/capabilities/pi", pi_labels)
        self.assertIn("package/pi_builder", pi_labels)
        self.assertIn("package/pi_templates", pi_labels)
        self.assertIn("metadata/license", pi_labels)
        self.assertNotIn("backend/pi", codex_labels)
        self.assertNotIn("backend/codex", pi_labels)
        self.assertNotEqual(
            provenance.source_sha256(codex), provenance.source_sha256(pi),
            "Codex and Pi must not share a source digest when generators differ",
        )

    def test_overlay_inputs_use_ordered_stable_labels(self):
        with tempfile.TemporaryDirectory() as overlay:
            with open(os.path.join(overlay, "system2.overlay.json"), "w") as fh:
                fh.write("{}\n")
            base = regen_all._context()
            ctx = regen_all._Context(
                compiler_root=base.compiler_root,
                repo_root=base.repo_root,
                plugin_root=base.plugin_root,
                codex_overlays=[overlay],
            )
            inputs = dict(regen_all._distribution_inputs("codex", ctx))
            self.assertEqual(inputs["overlay/0000"], overlay)

    def test_missing_declared_input_fails_closed(self):
        with self.assertRaises(FileNotFoundError):
            provenance.source_sha256([("missing", os.path.join(self.id(), "missing"))])

    def test_backend_template_license_plugin_and_lowering_mutations_change_hash(self):
        ctx = regen_all._context()
        cases = (
            ("codex", "backend/codex", "# backend mutation\n"),
            ("pi", "package/pi_templates", "template mutation\n"),
            ("pi", "metadata/license", "license mutation\n"),
            ("codex", "plugin", "plugin mutation\n"),
            ("pi", "lowering/ir", "# lowering mutation\n"),
        )
        for channel, label, addition in cases:
            with self.subTest(channel=channel, label=label), tempfile.TemporaryDirectory() as tmp:
                inputs = regen_all._distribution_inputs(channel, ctx)
                before = provenance.source_sha256(inputs)
                mutated = []
                for entry_label, path in inputs:
                    if entry_label != label:
                        mutated.append((entry_label, path))
                        continue
                    copy = os.path.join(tmp, "input")
                    if os.path.isdir(path):
                        shutil.copytree(path, copy)
                        candidates = []
                        for dirpath, _dirnames, filenames in os.walk(copy):
                            candidates.extend(os.path.join(dirpath, f) for f in filenames)
                        self.assertTrue(candidates)
                        with open(sorted(candidates)[0], "a", encoding="utf-8") as fh:
                            fh.write(addition)
                    else:
                        shutil.copyfile(path, copy)
                        with open(copy, "a", encoding="utf-8") as fh:
                            fh.write(addition)
                    mutated.append((entry_label, copy))
                self.assertNotEqual(before, provenance.source_sha256(mutated))


class DistributionProvenanceTest(unittest.TestCase):
    def test_each_distribution_records_a_distinct_source_and_exact_artifact_tree(self):
        with tempfile.TemporaryDirectory() as repo:
            base = regen_all._context()
            ctx = regen_all._Context(
                compiler_root=base.compiler_root,
                repo_root=repo,
                plugin_root=base.plugin_root,
                codex_overlays=list(base.codex_overlays),
            )
            records = {}
            for channel in ("codex", "pi"):
                art = next(item for item in regen_all.REGISTRY if item.name == channel)
                dest = os.path.join(repo, art.dest_rel)
                art.builder(dest, ctx)
                with open(os.path.join(dest, "PROVENANCE.json"), encoding="utf-8") as fh:
                    records[channel] = json.load(fh)
                self.assertTrue(provenance.artifacts_match(dest, records[channel]))
                self.assertTrue(records[channel]["artifact_inventory"])
                self.assertEqual(len(records[channel]["artifact_sha256"]), 64)
            self.assertNotEqual(
                records["codex"]["source_sha256"], records["pi"]["source_sha256"]
            )


class ArtifactDigestTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="provenance-artifacts-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "nested"))
        with open(os.path.join(self.root, "a.txt"), "wb") as fh:
            fh.write(b"alpha")
        with open(os.path.join(self.root, "nested", "b.txt"), "wb") as fh:
            fh.write(b"beta")
        self.prov = provenance.write_provenance(
            self.root,
            inputs=[("input", os.path.join(self.root, "a.txt"))],
            generator="test-generator",
            channel_version="9.9.9",
            compiler_root=_COMPILER_ROOT,
        )

    def test_provenance_records_complete_non_self_referential_artifact_tree(self):
        self.assertEqual(self.prov["artifact_inventory"], ["a.txt", "nested/b.txt"])
        self.assertNotIn("PROVENANCE.json", self.prov["artifact_inventory"])
        self.assertEqual(
            self.prov["artifact_sha256"],
            _manual_hash([("a.txt", b"alpha"), ("nested/b.txt", b"beta")]),
        )
        self.assertTrue(provenance.artifacts_match(self.root, self.prov))

    def test_extra_missing_and_changed_artifacts_each_fail(self):
        mutations = ("extra", "missing", "changed")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as copy:
                shutil.copytree(self.root, copy, dirs_exist_ok=True)
                if mutation == "extra":
                    with open(os.path.join(copy, "extra.txt"), "wb") as fh:
                        fh.write(b"extra")
                elif mutation == "missing":
                    os.remove(os.path.join(copy, "a.txt"))
                else:
                    with open(os.path.join(copy, "a.txt"), "ab") as fh:
                        fh.write(b"!")
                self.assertFalse(provenance.artifacts_match(copy, self.prov))

    def test_malformed_provenance_fails_closed(self):
        for malformed in ([], {}, {"artifact_inventory": "a.txt"}, {
            "artifact_inventory": ["a.txt", "nested/b.txt"],
            "artifact_sha256": "not-a-sha256",
        }):
            with self.subTest(malformed=malformed):
                self.assertFalse(provenance.artifacts_match(self.root, malformed))
        with open(os.path.join(self.root, "PROVENANCE.json"), "w", encoding="utf-8") as fh:
            fh.write("[]\n")
        self.assertFalse(provenance.artifacts_match(self.root))


class ChannelVersionTest(unittest.TestCase):
    def test_codex_and_pi_consume_one_authoritative_0_2_3_version(self):
        from system2_compiler.channel_version import CHANNEL_VERSION
        from system2_compiler.backends import codex
        import build_pi_package

        self.assertEqual(CHANNEL_VERSION, "0.2.3")
        self.assertEqual(codex._CODEX_PLUGIN_VERSION, CHANNEL_VERSION)
        self.assertEqual(build_pi_package.PACKAGE_VERSION, CHANNEL_VERSION)

    def test_emitted_version_mismatch_is_a_hard_error(self):
        with self.assertRaisesRegex(RuntimeError, "version mismatch"):
            regen_all._require_channel_version("codex", "0.2.2")
        with self.assertRaisesRegex(RuntimeError, "version mismatch"):
            regen_all._require_channel_version("pi", "0.2.4")
        import build_pi_package
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            build_pi_package.build(self.id(), self.id(), "0.2.4")

    def test_swapped_channel_generator_inputs_are_detectable(self):
        ctx = regen_all._context()
        codex = dict(regen_all._distribution_inputs("codex", ctx))
        pi = dict(regen_all._distribution_inputs("pi", ctx))
        normal = provenance.source_sha256(sorted(codex.items()))
        swapped = dict(codex)
        swapped["backend/codex"] = pi["backend/pi"]
        self.assertNotEqual(normal, provenance.source_sha256(sorted(swapped.items())))


if __name__ == "__main__":
    unittest.main()
