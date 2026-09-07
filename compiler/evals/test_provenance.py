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


def _artifact_bytes(root):
    files = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for filename in sorted(filenames):
            path = os.path.join(dirpath, filename)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            if relative == provenance.PROVENANCE_FILENAME or filename.endswith(".pyc"):
                continue
            with open(path, "rb") as fh:
                files[relative] = fh.read()
    return files


def _emit_channel(channel, dest, ctx):
    os.makedirs(dest, exist_ok=True)
    if channel == "codex":
        result = regen_all.ir.compose(
            ctx.plugin_root, list(ctx.codex_overlays), dest
        )
        if result.graph is None:
            raise AssertionError(result.errors)
        regen_all.CodexBackend(
            overlay_sources=list(ctx.codex_overlays)
        ).emit(result.graph, dest)
        return
    with tempfile.TemporaryDirectory(prefix="provenance-pi-emit-") as staging:
        result = regen_all.ir.compose(ctx.plugin_root, [], staging)
        if result.graph is None:
            raise AssertionError(result.errors)
        regen_all.PiBackend(overlay_sources=[]).emit(result.graph, staging)
        regen_all.build_pi_package.build(
            staging, dest, regen_all.build_pi_package.PACKAGE_VERSION
        )


def _write_overlay(root):
    paths = {
        "content/principle.md": b"Provenance principle.\n",
        "content/executor.md": b"Executor provenance guidance.\n",
        "agents/provenance-scout.md": (
            b"---\nname: provenance-scout\ndescription: Provenance scout\n"
            b"tools: [Read]\n---\nInspect provenance.\n"
        ),
        "hooks/check.py": b"value = 1\n",
    }
    for relative, content in paths.items():
        path = os.path.join(root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(content)
    manifest = {
        "name": "provenance-overlay",
        "version": "1.0.0",
        "description": "provenance test",
        "schema_version": "1.0.0",
        "contributions": {
            "orchestrator": {
                "principles": [{
                    "id": "provenance-principle",
                    "content_file": "content/principle.md",
                }]
            },
            "agents": {
                "executor": {
                    "prompt_sections": {
                        "implementation_discipline": [{
                            "id": "provenance-executor",
                            "content_file": "content/executor.md",
                            "inline": True,
                        }]
                    },
                    "hooks": [{
                        "event": "SubagentStop",
                        "command": "hooks/check.py",
                    }],
                }
            },
            "auxiliary_agents": [{
                "name": "provenance-scout",
                "role": "Inspect provenance",
                "pipeline": False,
                "delegation_policy": "orchestrator_optional",
                "agent_file": "agents/provenance-scout.md",
            }],
        },
    }
    manifest_path = os.path.join(root, "system2.overlay.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    return manifest_path, paths


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
            "base/plugin_metadata", "base/init_template",
            "base/schema/overlay", "base/schema/anchor_map",
            "lowering/ir", "lowering/system2_compiler_init", "backend/base",
            "backend/degradation", "backend/enforcement", "backend/yaml",
            "generator/regen_all", "generator/provenance",
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

    def test_base_inputs_name_only_canonical_agents_and_allowlists(self):
        ctx = regen_all._context()
        labels = {
            label for label, _path in regen_all._plugin_graph_inputs(ctx)
        }
        with open(
            os.path.join(ctx.plugin_root, "schemas", "anchor-map.json"),
            encoding="utf-8",
        ) as fh:
            anchor_map = json.load(fh)
        agent_prefix = "base/agent/"
        allowlist_prefix = "base/allowlist/"
        self.assertEqual(
            {label[len(agent_prefix):] for label in labels
             if label.startswith(agent_prefix)},
            set(anchor_map["agents"]),
        )
        self.assertEqual(
            {label[len(allowlist_prefix):] for label in labels
             if label.startswith(allowlist_prefix)},
            set(regen_all.ir_build._ROLE_ALLOWLISTS),
        )
        self.assertNotIn("plugin", labels)
        self.assertFalse(any("task-lease" in label for label in labels))

    def test_overlay_inputs_use_ordered_stable_reference_labels(self):
        with tempfile.TemporaryDirectory() as overlay:
            manifest_path, paths = _write_overlay(overlay)
            base = regen_all._context()
            ctx = regen_all._Context(
                compiler_root=base.compiler_root,
                repo_root=base.repo_root,
                plugin_root=base.plugin_root,
                codex_overlays=[overlay],
            )
            inputs = dict(regen_all._distribution_inputs("codex", ctx))
            self.assertEqual(inputs["overlay/0000/manifest"], manifest_path)
            for relative in paths:
                self.assertEqual(
                    inputs[f"overlay/0000/reference/{relative}"],
                    os.path.join(overlay, relative),
                )
            self.assertFalse(any("unrelated" in label for label in inputs))

    def test_overlay_missing_and_unsafe_references_fail_closed(self):
        with tempfile.TemporaryDirectory() as overlay:
            manifest_path, _paths = _write_overlay(overlay)
            base = regen_all._context()
            ctx = regen_all._Context(
                compiler_root=base.compiler_root,
                repo_root=base.repo_root,
                plugin_root=base.plugin_root,
                codex_overlays=[overlay],
            )
            with open(manifest_path, encoding="utf-8") as fh:
                original = json.load(fh)
            references = (
                ("content", "content_file", lambda man: man["contributions"]
                 ["orchestrator"]["principles"][0], "content_file not found"),
                ("agent", "agent_file", lambda man: man["contributions"]
                 ["auxiliary_agents"][0], "content_file not found"),
                ("hook", "command", lambda man: man["contributions"]
                 ["agents"]["executor"]["hooks"][0], "hook file does not exist"),
            )
            for kind, field, select, missing_message in references:
                for invalid, message in (
                    ("missing.md", missing_message),
                    ("../outside.md", "path traversal rejected"),
                ):
                    with self.subTest(kind=kind, reference=invalid):
                        manifest = json.loads(json.dumps(original))
                        select(manifest)[field] = invalid
                        with open(manifest_path, "w", encoding="utf-8") as fh:
                            json.dump(manifest, fh)
                        with self.assertRaisesRegex(ValueError, message):
                            regen_all._distribution_inputs("codex", ctx)

    def test_manifest_and_referenced_overlay_bytes_change_hash(self):
        with tempfile.TemporaryDirectory() as overlay:
            manifest_path, paths = _write_overlay(overlay)
            base = regen_all._context()
            ctx = regen_all._Context(
                compiler_root=base.compiler_root,
                repo_root=base.repo_root,
                plugin_root=base.plugin_root,
                codex_overlays=[overlay],
            )
            baseline = provenance.source_sha256(
                regen_all._distribution_inputs("codex", ctx)
            )
            for relative in ("content/principle.md", "content/executor.md",
                             "agents/provenance-scout.md", "hooks/check.py"):
                with self.subTest(reference=relative):
                    path = os.path.join(overlay, relative)
                    with open(path, "rb") as fh:
                        original = fh.read()
                    with open(path, "ab") as fh:
                        fh.write(b"# mutation\n")
                    self.assertNotEqual(
                        baseline,
                        provenance.source_sha256(
                            regen_all._distribution_inputs("codex", ctx)
                        ),
                    )
                    with open(path, "wb") as fh:
                        fh.write(original)
            with open(manifest_path, "rb") as fh:
                original = fh.read()
            with open(manifest_path, "wb") as fh:
                fh.write(original.replace(
                    b"provenance test", b"provenance test changed"
                ))
            self.assertNotEqual(
                baseline,
                provenance.source_sha256(
                    regen_all._distribution_inputs("codex", ctx)
                ),
            )

    def test_missing_declared_input_fails_closed(self):
        with self.assertRaises(FileNotFoundError):
            provenance.source_sha256([("missing", os.path.join(self.id(), "missing"))])

    def test_symlinked_source_inputs_and_intermediate_directories_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "source")
            os.makedirs(os.path.join(source, "nested"))
            regular = os.path.join(source, "regular.txt")
            nested = os.path.join(source, "nested", "member.txt")
            with open(regular, "wb") as fh:
                fh.write(b"same bytes")
            with open(nested, "wb") as fh:
                fh.write(b"nested bytes")

            regular_hash = provenance.source_sha256([("source", source)])
            self.assertEqual(regular_hash, provenance.source_sha256([("source", source)]))

            source_link = os.path.join(root, "source-link")
            os.symlink(source, source_link)
            with self.assertRaisesRegex(ValueError, "not a regular file or directory"):
                provenance.source_sha256([("source", source_link)])

            file_link = os.path.join(root, "file-link")
            os.symlink(regular, file_link)
            with self.assertRaisesRegex(ValueError, "not a regular file or directory"):
                provenance.source_sha256([("source", file_link)])

            external = os.path.join(root, "external.txt")
            shutil.copyfile(nested, external)
            os.remove(nested)
            os.symlink(external, nested)
            with self.assertRaisesRegex(ValueError, "provenance input file"):
                provenance.source_sha256([("source", source)])

            os.remove(nested)
            with open(nested, "wb") as fh:
                fh.write(b"nested bytes")
            external_dir = os.path.join(root, "external-dir")
            os.rename(os.path.join(source, "nested"), external_dir)
            os.symlink(external_dir, os.path.join(source, "nested"))
            with self.assertRaisesRegex(ValueError, "provenance input directory"):
                provenance.source_sha256([("source", source)])

    def test_effective_base_backend_and_package_mutations_change_hash(self):
        ctx = regen_all._context()
        cases = (
            ("codex", "backend/codex", "# backend mutation\n"),
            ("pi", "package/pi_templates", "template mutation\n"),
            ("pi", "metadata/license", "license mutation\n"),
            ("codex", "base/plugin_metadata", "plugin metadata mutation\n"),
            ("codex", "base/init_template", "init mutation\n"),
            ("codex", "base/schema/overlay", "schema mutation\n"),
            ("pi", "base/schema/anchor_map", "anchor mutation\n"),
            ("codex", "base/agent/executor", "agent mutation\n"),
            ("pi", "base/allowlist/executor", "allowlist mutation\n"),
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

    def test_unrelated_plugin_bytes_leave_digests_and_artifacts_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="provenance-plugin-controls-") as root:
            base = regen_all._context()
            plugin = os.path.join(root, "plugin")
            shutil.copytree(base.plugin_root, plugin)
            ctx = regen_all._Context(
                compiler_root=base.compiler_root,
                repo_root=root,
                plugin_root=plugin,
                codex_overlays=[],
            )
            before_hashes = {
                channel: provenance.source_sha256(
                    regen_all._distribution_inputs(channel, ctx)
                )
                for channel in ("codex", "pi")
            }
            destinations = {
                channel: os.path.join(root, channel)
                for channel in ("codex", "pi")
            }
            for channel, dest in destinations.items():
                _emit_channel(channel, dest, ctx)
            before_artifacts = {
                channel: _artifact_bytes(dest)
                for channel, dest in destinations.items()
            }

            mutations = (
                "scripts/composer.py.preflip",
                "hooks/HOOKS.md",
                "skills/doctor/SKILL.md",
                "scripts/_system2_compiler/BUNDLE.json",
            )
            for relative in mutations:
                with open(os.path.join(plugin, relative), "ab") as fh:
                    fh.write(b"\nprovenance negative control\n")
            with open(
                os.path.join(plugin, "allowlists", ".task-lease.regex"), "wb"
            ) as fh:
                fh.write(b"temporary/.*\n")
            with open(os.path.join(plugin, "unrelated.txt"), "wb") as fh:
                fh.write(b"unrelated plugin input\n")

            for channel, dest in destinations.items():
                self.assertEqual(
                    before_hashes[channel],
                    provenance.source_sha256(
                        regen_all._distribution_inputs(channel, ctx)
                    ),
                )
                _emit_channel(channel, dest, ctx)
                self.assertEqual(before_artifacts[channel], _artifact_bytes(dest))

    def test_unrelated_overlay_bytes_leave_digest_and_artifacts_unchanged(self):
        with tempfile.TemporaryDirectory(prefix="provenance-overlay-controls-") as root:
            overlay = os.path.join(root, "overlay")
            os.makedirs(overlay)
            _write_overlay(overlay)
            base = regen_all._context()
            ctx = regen_all._Context(
                compiler_root=base.compiler_root,
                repo_root=root,
                plugin_root=base.plugin_root,
                codex_overlays=[overlay],
            )
            dest = os.path.join(root, "codex")
            before_hash = provenance.source_sha256(
                regen_all._distribution_inputs("codex", ctx)
            )
            _emit_channel("codex", dest, ctx)
            before_artifacts = _artifact_bytes(dest)

            unrelated = os.path.join(overlay, "notes", "unrelated.md")
            os.makedirs(os.path.dirname(unrelated))
            with open(unrelated, "wb") as fh:
                fh.write(b"not referenced by the manifest\n")

            self.assertEqual(
                before_hash,
                provenance.source_sha256(
                    regen_all._distribution_inputs("codex", ctx)
                ),
            )
            _emit_channel("codex", dest, ctx)
            self.assertEqual(before_artifacts, _artifact_bytes(dest))


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

                comparison = dest + "-comparison"
                shutil.copytree(dest, comparison)
                self.assertTrue(
                    regen_all._trees_match(dest, comparison),
                    "ordinary regular-file trees must remain equivalent",
                )
                victim_rel = records[channel]["artifact_inventory"][0]
                victim = os.path.join(comparison, victim_rel.replace("/", os.sep))
                external = dest + "-same-bytes"
                shutil.copyfile(victim, external)
                os.remove(victim)
                os.symlink(external, victim)
                self.assertFalse(
                    provenance.artifacts_match(comparison, records[channel]),
                    "a same-byte symlink is not a distribution artifact",
                )
                self.assertFalse(
                    regen_all._trees_match(comparison, dest),
                    "tree comparison must not dereference a distribution symlink",
                )
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

    def test_same_byte_symlinked_artifacts_and_directories_fail_closed(self):
        with tempfile.TemporaryDirectory() as workspace:
            root_link = os.path.join(workspace, "root-link")
            os.symlink(self.root, root_link)
            self.assertFalse(provenance.artifacts_match(root_link, self.prov))
            with self.assertRaisesRegex(ValueError, "artifact root directory"):
                provenance.artifact_inventory(root_link)

            regular_tree = os.path.join(workspace, "regular-tree")
            shutil.copytree(self.root, regular_tree)
            self.assertTrue(provenance.artifacts_match(regular_tree, self.prov))

            external_file = os.path.join(workspace, "external-file")
            victim = os.path.join(regular_tree, "a.txt")
            shutil.copyfile(victim, external_file)
            os.remove(victim)
            os.symlink(external_file, victim)
            self.assertFalse(provenance.artifacts_match(regular_tree, self.prov))
            with self.assertRaisesRegex(ValueError, "artifact file"):
                provenance.artifact_inventory(regular_tree)

            shutil.rmtree(regular_tree)
            shutil.copytree(self.root, regular_tree)
            external_dir = os.path.join(workspace, "external-dir")
            os.rename(os.path.join(regular_tree, "nested"), external_dir)
            os.symlink(external_dir, os.path.join(regular_tree, "nested"))
            self.assertFalse(provenance.artifacts_match(regular_tree, self.prov))
            with self.assertRaisesRegex(ValueError, "artifact directory"):
                provenance.artifact_inventory(regular_tree)

    def test_non_regular_artifact_fails_closed(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo is unavailable")
        fifo = os.path.join(self.root, "non-regular")
        os.mkfifo(fifo)
        self.assertFalse(provenance.artifacts_match(self.root, self.prov))
        with self.assertRaisesRegex(ValueError, "artifact file"):
            provenance.artifact_inventory(self.root)

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
