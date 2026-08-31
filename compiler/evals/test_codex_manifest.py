"""Validate the Codex plugin structure, lock, skills, and uninstall behavior."""

import copy
import json
import os
import posixpath
import re
import shutil
import tempfile
import unittest

from system2_compiler import ir
from system2_compiler.backends import codex as codex_backend
from system2_compiler.backends.codex import CodexBackend
from evals import matrix, oracle

_BASE = oracle.PLUGIN_ROOT
_TEST_OVERLAY = matrix.TEST_OVERLAY
_REPO_ROOT = oracle.PLUGIN_REPO_ROOT

# Emitted artifact locations (relative to the plugin root).
_MANIFEST_REL = os.path.join(".codex-plugin", "plugin.json")
_LOCK_REL = "system2.codex.lock.json"

# Committed distribution and repository marketplace locations.
_DIST_CODEX_ROOT = os.path.join(_REPO_ROOT, "distributions", "codex")
_MARKETPLACE_PATH = os.path.join(_REPO_ROOT, ".agents", "plugins", "marketplace.json")

# Kebab-case, mirroring backends.codex._KEBAB_RE (the shared naming contract).
_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_MANIFEST_REQUIRED = ("name", "version", "description")
# The inert plugin-level ``hooks`` pointer is omitted; only ``skills`` remains.
_MANIFEST_POINTERS = ("skills",)

# The pinned 0.2.2 version, imported from the backend (never re-typed).
_CODEX_PLUGIN_VERSION = codex_backend._CODEX_PLUGIN_VERSION

# exact-name 18-skill inventory (orchestrator + system2-doctor + 13 role skills + the 3 adapted utility skills).
_EXPECTED_SKILL_NAMES = frozenset({
    "system2",
    "system2-doctor",
    "system2-codex",
    "system2-gemini",
    "system2-stateless-loop",
    "system2-role-code-reviewer",
    "system2-role-design-architect",
    "system2-role-docs-release",
    "system2-role-eval-engineer",
    "system2-role-executor",
    "system2-role-mcp-toolsmith",
    "system2-role-postmortem-scribe",
    "system2-role-repo-governor",
    "system2-role-requirements-engineer",
    "system2-role-security-sentinel",
    "system2-role-spec-coordinator",
    "system2-role-task-planner",
    "system2-role-test-engineer",
})

_STATUS_ENUM = frozenset({"native", "adapted", "advisory", "unsupported"})
_CAP_FIELDS = ("status", "mechanism", "enforced", "gated")
_LOCK_ENVELOPE = (
    "backend",
    "codex_plugin_version",
    "enforcement",
    "subagent_isolation",
    "FIDELITY",
    "capabilities",
    "overlay_sources",
)

# status -> (enforced, gated); total over the enum (the shared _degradation rule).
_FLAG_BY_STATUS = {
    "native": (True, False),
    "adapted": (False, True),
    "advisory": (False, False),
    "unsupported": (False, False),
}


# Emit harness (mirrors test_pi_goldens / test_pi_degradation)

def _emit_codex(project_dir):
    """Compose the core+overlay cell and emit the Codex plugin under *project_dir*."""
    result = ir.compose(_BASE, [_TEST_OVERLAY], project_dir)
    if result.graph is None:
        raise AssertionError(
            f"compose refused the core+overlay cell: {result.errors!r}"
        )
    CodexBackend(overlay_sources=[_TEST_OVERLAY]).emit(result.graph, project_dir)
    return project_dir


def _read_json(root, rel):
    with open(os.path.join(root, rel), "r", encoding="utf-8") as fh:
        return json.load(fh)


# Pure validators (raise on violation; the negative tests assert the teeth)

class ManifestValidationError(ValueError):
    """A Codex manifest / pointer-hygiene contract violation."""


class LockValidationError(ValueError):
    """A Codex lock (``system2.codex.lock.json``) shape violation."""


def _iter_path_pointers(node, path="$"):
    """Yield ``(jsonpath, value)`` for every ANCHORED path-ish string in *node*."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _iter_path_pointers(value, f"{path}.{key}")
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            yield from _iter_path_pointers(value, f"{path}[{idx}]")
    elif isinstance(node, str):
        if "://" in node:
            return
        if node.startswith(("./", "../", "/")):
            yield (path, node)


def _pointer_hygiene_errors(field, value):
    """a ``./``-relative, in-root pointer — NO ``..``, NO absolute. Returns errors."""
    errors = []
    if value.startswith("/") or posixpath.isabs(value):
        errors.append(f"{field}: absolute path not allowed: {value!r}")
        return errors  # absolute is disqualifying; downstream checks are moot
    if any(seg == ".." for seg in value.split("/")):
        errors.append(f"{field}: parent-escape '..' not allowed: {value!r}")
    normalized = posixpath.normpath(value)
    if normalized == ".." or normalized.startswith("../") or posixpath.isabs(normalized):
        errors.append(f"{field}: pointer escapes the plugin root: {value!r}")
    if not value.startswith("./"):
        errors.append(f"{field}: pointer must be './'-relative and in-root: {value!r}")
    return errors


def validate_pointer_hygiene_errors(doc):
    """Every anchored path pointer anywhere in *doc* must be in-root. Returns errors."""
    errors = []
    for field, value in _iter_path_pointers(doc):
        errors.extend(_pointer_hygiene_errors(field, value))
    return errors


def validate_manifest(manifest):
    """Validate a Codex ``plugin.json`` manifest; raise ManifestValidationError on any breach."""
    errors = []
    if not isinstance(manifest, dict):
        raise ManifestValidationError("manifest is not a JSON object")

    for field in _MANIFEST_REQUIRED:
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing or empty required field: {field!r}")

    name = manifest.get("name")
    if isinstance(name, str) and not _KEBAB_RE.match(name):
        errors.append(f"name is not kebab-case: {name!r}")

    for field in _MANIFEST_POINTERS:
        if field not in manifest:
            errors.append(f"missing component pointer: {field!r}")

    errors.extend(validate_pointer_hygiene_errors(manifest))

    if errors:
        raise ManifestValidationError("; ".join(errors))


def validate_marketplace(doc):
    """Validate the repo-scoped marketplace doc's pointer hygiene. Raise on breach."""
    errors = validate_pointer_hygiene_errors(doc)
    if errors:
        raise ManifestValidationError("; ".join(errors))


def validate_lock(lock, descriptor=None):
    """Validate a ``system2.codex.lock.json`` against the shared envelope + Codex guard."""
    errors = []
    if not isinstance(lock, dict):
        raise LockValidationError("lock is not a JSON object")

    for key in _LOCK_ENVELOPE:
        if key not in lock:
            errors.append(f"missing envelope key: {key!r}")

    if lock.get("backend") != "codex":
        errors.append(f"backend tag must be 'codex', got {lock.get('backend')!r}")

    banner = lock.get("FIDELITY")
    if not isinstance(banner, str) or not banner.strip():
        errors.append("FIDELITY banner is missing or empty")

    if not isinstance(lock.get("overlay_sources"), list):
        errors.append("overlay_sources must be a list")

    caps = lock.get("capabilities")
    if not isinstance(caps, dict) or not caps:
        errors.append("capabilities must be a non-empty object")
    else:
        descriptor_caps = (descriptor or {}).get("capabilities", {})
        for cap, entry in caps.items():
            if not isinstance(entry, dict):
                errors.append(f"capability {cap!r} is not an object")
                continue
            for field in _CAP_FIELDS:
                if field not in entry:
                    errors.append(f"capability {cap!r}: missing field {field!r}")
            status = entry.get("status")
            if status not in _STATUS_ENUM:
                errors.append(f"capability {cap!r}: status {status!r} not in enum")
            if status == "native":
                errors.append(
                    f"capability {cap!r}: Codex may not carry a 'native' capability "
                    f"(standing guard — nothing is native at rest)"
                )
            expected_flags = _FLAG_BY_STATUS.get(status)
            if expected_flags is not None:
                actual = (entry.get("enforced"), entry.get("gated"))
                if actual != expected_flags:
                    errors.append(
                        f"capability {cap!r}: flags {actual} do not follow the "
                        f"status rule {expected_flags} for status {status!r}"
                    )
            if descriptor_caps and cap in descriptor_caps:
                want = descriptor_caps[cap].get("status")
                if status != want:
                    errors.append(
                        f"capability {cap!r}: status {status!r} != descriptor {want!r}"
                    )

    if errors:
        raise LockValidationError("; ".join(errors))


def _codex_descriptor():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "system2_compiler", "backends", "capabilities", "codex.json",
    )
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _build_sources():
    """Return [(label, root_dir, is_temp)] — staging always; committed dist if present."""
    staging = tempfile.mkdtemp(prefix="codex-manifest-")
    _emit_codex(staging)
    sources = [("staging-emit", staging, True)]
    manifest_at_dist = os.path.join(_DIST_CODEX_ROOT, _MANIFEST_REL)
    if os.path.isfile(manifest_at_dist):
        sources.append(("distributions/codex", _DIST_CODEX_ROOT, False))
    return sources


class _CodexEmissionBase(unittest.TestCase):
    """Shared per-class staging emission + committed-dist discovery (path-parameterized)."""

    @classmethod
    def setUpClass(cls):
        cls.sources = _build_sources()
        cls.descriptor = _codex_descriptor()

    @classmethod
    def tearDownClass(cls):
        for _label, root, is_temp in getattr(cls, "sources", []):
            if is_temp:
                shutil.rmtree(root, ignore_errors=True)


# Positive: manifest structure + pointer hygiene

class CodexManifestStructureTest(_CodexEmissionBase):
    """The emitted manifest satisfies its structure and pointer contract."""

    def test_manifest_validates(self):
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                manifest = _read_json(root, _MANIFEST_REL)
                validate_manifest(manifest)  # must not raise

    def test_required_fields_present_and_nonempty(self):
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                manifest = _read_json(root, _MANIFEST_REL)
                for field in _MANIFEST_REQUIRED:
                    self.assertIsInstance(manifest.get(field), str)
                    self.assertTrue(manifest[field].strip(), f"{field} is empty")

    def test_name_is_kebab_case(self):
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                manifest = _read_json(root, _MANIFEST_REL)
                self.assertRegex(manifest["name"], _KEBAB_RE)

    def test_component_pointers_present(self):
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                manifest = _read_json(root, _MANIFEST_REL)
                for field in _MANIFEST_POINTERS:
                    self.assertIn(field, manifest, f"missing component pointer {field!r}")

    def test_pointers_are_dot_relative_in_root(self):
        # every anchored pointer is './'-relative and inside the plugin root.
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                manifest = _read_json(root, _MANIFEST_REL)
                pointers = dict(_iter_path_pointers(manifest))
                self.assertTrue(pointers, "manifest carries no path pointers to check")
                for field, value in pointers.items():
                    self.assertTrue(
                        value.startswith("./"),
                        f"{field} pointer {value!r} is not './'-relative",
                    )
                    self.assertNotIn(
                        "..", value.split("/"),
                        f"{field} pointer {value!r} contains a '..' segment",
                    )
                    self.assertFalse(
                        posixpath.isabs(value),
                        f"{field} pointer {value!r} is absolute",
                    )
                self.assertEqual(
                    [], validate_pointer_hygiene_errors(manifest),
                    "manifest pointer hygiene must be clean",
                )


# Positive: lock schema (shared envelope + per-cap fields + Codex guard)

class CodexLockSchemaTest(_CodexEmissionBase):
    """The emitted lock matches the shared ``system2.<target>.lock.json`` shape."""

    def test_lock_validates(self):
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                lock = _read_json(root, _LOCK_REL)
                validate_lock(lock, self.descriptor)  # must not raise

    def test_envelope_keys_present(self):
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                lock = _read_json(root, _LOCK_REL)
                for key in _LOCK_ENVELOPE:
                    self.assertIn(key, lock, f"lock missing envelope key {key!r}")
                self.assertEqual(lock["backend"], "codex")
                self.assertEqual(lock["subagent_isolation"], "adapted")

    def test_each_capability_has_the_four_fields(self):
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                caps = _read_json(root, _LOCK_REL)["capabilities"]
                self.assertTrue(caps, "capabilities map is empty")
                for cap, entry in caps.items():
                    for field in _CAP_FIELDS:
                        self.assertIn(field, entry, f"{cap} missing {field!r}")

    def test_nothing_is_native(self):
        # Codex standing guard: no capability is 'native' at rest.
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                caps = _read_json(root, _LOCK_REL)["capabilities"]
                native = {c for c, e in caps.items() if e.get("status") == "native"}
                self.assertEqual(set(), native, f"Codex native offenders: {native!r}")

    def test_flags_follow_status_rule(self):
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                caps = _read_json(root, _LOCK_REL)["capabilities"]
                for cap, entry in caps.items():
                    expected = _FLAG_BY_STATUS[entry["status"]]
                    self.assertEqual(
                        (entry["enforced"], entry["gated"]), expected,
                        f"{cap} (status {entry['status']!r}) flags violate the rule",
                    )

    def test_status_equals_descriptor(self):
        descriptor_caps = self.descriptor["capabilities"]
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                caps = _read_json(root, _LOCK_REL)["capabilities"]
                for cap, entry in caps.items():
                    self.assertEqual(
                        entry["status"], descriptor_caps[cap]["status"],
                        f"report status for {cap!r} != descriptor status",
                    )

    def test_fidelity_banner_present_and_loud(self):
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                lock = _read_json(root, _LOCK_REL)
                banner = lock.get("FIDELITY", "")
                self.assertTrue(banner.strip(), "FIDELITY banner must be non-empty")
                self.assertIn("ADAPTED", banner, "banner must state enforcement is ADAPTED")

    def test_overlay_sources_is_a_list(self):
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                lock = _read_json(root, _LOCK_REL)
                self.assertIsInstance(lock["overlay_sources"], list)


class CodexMarketplacePointerHygieneTest(unittest.TestCase):
    """Marketplace documents enforce the same pointer rule as manifests."""

    def test_in_root_marketplace_doc_validates(self):
        doc = {
            "name": "system2",
            "plugins": [
                {"name": "system2", "source": "./distributions/codex"},
            ],
        }
        validate_marketplace(doc)  # must not raise

    def test_escaping_marketplace_pointer_is_rejected(self):
        for bad in ("../../etc/passwd", "/etc/passwd", "./a/../../b"):
            with self.subTest(pointer=bad):
                doc = {"plugins": [{"source": bad}]}
                with self.assertRaises(ManifestValidationError):
                    validate_marketplace(doc)

    def test_committed_marketplace_file_if_present(self):
        # Path-parameterized: validate the real file only once it exists;
        # the synthetic cases above keep this test non-vacuous until then.
        if not os.path.isfile(_MARKETPLACE_PATH):
            self.assertFalse(
                os.path.exists(_MARKETPLACE_PATH),
                "marketplace path unexpectedly exists as a non-file",
            )
            return
        with open(_MARKETPLACE_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
        validate_marketplace(doc)


# Negative: the manifest validator has teeth (tamper a DEEP COPY, never the real one)

class CodexManifestNegativeControlsTest(_CodexEmissionBase):
    """Tampered manifest copies must each fail validation (assertRaises)."""

    def setUp(self):
        _label, root, _tmp = self.sources[0]
        self.clean = _read_json(root, _MANIFEST_REL)
        validate_manifest(self.clean)  # sanity: the real one passes

    def test_missing_required_field_fails(self):
        for field in _MANIFEST_REQUIRED:
            with self.subTest(dropped=field):
                bad = copy.deepcopy(self.clean)
                del bad[field]
                with self.assertRaises(ManifestValidationError):
                    validate_manifest(bad)

    def test_parent_escape_pointer_fails(self):
        bad = copy.deepcopy(self.clean)
        bad["skills"] = "../../etc"
        with self.assertRaises(ManifestValidationError):
            validate_manifest(bad)

    def test_absolute_pointer_fails(self):
        bad = copy.deepcopy(self.clean)
        bad["skills"] = "/etc/passwd"
        with self.assertRaises(ManifestValidationError):
            validate_manifest(bad)

    def test_non_kebab_name_fails(self):
        for bad_name in ("System2", "system_2", "system2-", "-system2", "System2/Evil"):
            with self.subTest(name=bad_name):
                bad = copy.deepcopy(self.clean)
                bad["name"] = bad_name
                with self.assertRaises(ManifestValidationError):
                    validate_manifest(bad)

    def test_the_real_manifest_is_never_mutated(self):
        # Re-read from disk after the tampering above; the emission is untouched.
        _label, root, _tmp = self.sources[0]
        on_disk = _read_json(root, _MANIFEST_REL)
        validate_manifest(on_disk)
        self.assertEqual(on_disk["name"], "system2")


# Negative: the lock validator has teeth

class CodexLockNegativeControlsTest(_CodexEmissionBase):
    """Tampered lock copies must each fail validation (assertRaises)."""

    def setUp(self):
        _label, root, _tmp = self.sources[0]
        self.clean = _read_json(root, _LOCK_REL)
        validate_lock(self.clean, self.descriptor)  # sanity: the real one passes

    def test_missing_envelope_key_fails(self):
        for key in _LOCK_ENVELOPE:
            with self.subTest(dropped=key):
                bad = copy.deepcopy(self.clean)
                del bad[key]
                with self.assertRaises(LockValidationError):
                    validate_lock(bad, self.descriptor)

    def test_native_capability_trips_standing_guard(self):
        bad = copy.deepcopy(self.clean)
        victim = next(iter(bad["capabilities"]))
        bad["capabilities"][victim]["status"] = "native"
        with self.assertRaises(LockValidationError):
            validate_lock(bad, self.descriptor)

    def test_flag_status_mismatch_fails(self):
        bad = copy.deepcopy(self.clean)
        # An adapted capability's flags must be (False, True); force enforced=True so the
        # pair breaks the status rule. (Codex always carries >=1 adapted capability.)
        adapted = [c for c, e in bad["capabilities"].items() if e["status"] == "adapted"]
        self.assertTrue(
            adapted, "precondition: the Codex lock must carry an adapted capability"
        )
        bad["capabilities"][adapted[0]]["enforced"] = True
        with self.assertRaises(LockValidationError):
            validate_lock(bad, self.descriptor)

    def test_dropped_capability_field_fails(self):
        for field in _CAP_FIELDS:
            with self.subTest(dropped=field):
                bad = copy.deepcopy(self.clean)
                victim = next(iter(bad["capabilities"]))
                del bad["capabilities"][victim][field]
                with self.assertRaises(LockValidationError):
                    validate_lock(bad, self.descriptor)

    def test_empty_fidelity_banner_fails(self):
        bad = copy.deepcopy(self.clean)
        bad["FIDELITY"] = "   "
        with self.assertRaises(LockValidationError):
            validate_lock(bad, self.descriptor)

    def test_the_real_lock_is_never_mutated(self):
        _label, root, _tmp = self.sources[0]
        on_disk = _read_json(root, _LOCK_REL)
        validate_lock(on_disk, self.descriptor)
        self.assertEqual(on_disk["backend"], "codex")


# Exact-name 18-skill inventory and the 0.2.2 version pin.

class CodexSkillInventoryTest(_CodexEmissionBase):
    """The emitted ``skills/`` directory carries exactly the pinned 18 names,
    each with a non-empty ``SKILL.md``, and every version field reads 0.2.2."""

    def test_skills_directory_has_exactly_the_18_expected_names(self):
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                actual = set(os.listdir(os.path.join(root, "skills")))
                self.assertEqual(
                    _EXPECTED_SKILL_NAMES, actual,
                    f"skills/ diverged from the pinned 18-name inventory "
                    f"(source={label}): missing={_EXPECTED_SKILL_NAMES - actual}, "
                    f"unexpected={actual - _EXPECTED_SKILL_NAMES}",
                )
                self.assertEqual(18, len(actual))

    def test_every_expected_skill_has_a_nonempty_skill_md(self):
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                for name in _EXPECTED_SKILL_NAMES:
                    path = os.path.join(root, "skills", name, "SKILL.md")
                    self.assertTrue(os.path.isfile(path), f"{name}: SKILL.md missing")
                    with open(path, encoding="utf-8") as fh:
                        self.assertTrue(fh.read().strip(), f"{name}: SKILL.md is empty")

    def test_manifest_and_lock_pin_the_0_2_2_version(self):
        # The emitted-byte cleanup in this change is the "bug fix that changes
        self.assertEqual(
            "0.2.2", _CODEX_PLUGIN_VERSION,
            "backends.codex._CODEX_PLUGIN_VERSION drifted off the bundled 0.2.2 pin",
        )
        for label, root, _tmp in self.sources:
            with self.subTest(source=label):
                manifest = _read_json(root, _MANIFEST_REL)
                self.assertEqual(manifest["version"], "0.2.2")
                lock = _read_json(root, _LOCK_REL)
                self.assertEqual(lock["codex_plugin_version"], "0.2.2")


# uninstall-path leg (Decision 's compensating test).

class CodexUninstallSweepTest(unittest.TestCase):
    """Removing the last overlay sweeps every ``skills/*/SKILL.md`` generically."""

    def test_uninstall_removes_all_18_skills_via_the_generic_sweep(self):
        project_dir = tempfile.mkdtemp(prefix="codex-uninstall-")
        self.addCleanup(shutil.rmtree, project_dir, True)
        _emit_codex(project_dir)

        overlay_name = os.path.basename(os.path.normpath(_TEST_OVERLAY))
        backend = CodexBackend(overlay_sources=[_TEST_OVERLAY])
        result = backend.uninstall(project_dir, overlay_name)

        self.assertEqual(result.errors, [])
        self.assertTrue(
            result.is_last_overlay,
            "a single-overlay uninstall must take the last-overlay (full-tree) path",
        )

        removed_skill_names = {
            os.path.basename(os.path.dirname(p))
            for p in result.artifacts_removed
            if os.path.basename(p) == "SKILL.md"
        }
        self.assertEqual(
            _EXPECTED_SKILL_NAMES, removed_skill_names,
            "the generic skills/*/SKILL.md sweep did not cover the exact "
            f"18-skill inventory; diverged: "
            f"{_EXPECTED_SKILL_NAMES ^ removed_skill_names}",
        )

        for name in _EXPECTED_SKILL_NAMES:
            self.assertFalse(
                os.path.exists(os.path.join(project_dir, "skills", name)),
                f"{name}: SKILL.md/dir still present on disk after uninstall",
            )
        self.assertFalse(
            os.path.isdir(os.path.join(project_dir, "skills")),
            "skills/ should be pruned once empty (best-effort prune)",
        )


if __name__ == "__main__":
    unittest.main()
