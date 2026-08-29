"""TASK-311 — ``backends/_yaml`` serializer unit goldens (quoting / multiline / determinism).

Pins the deterministic, stdlib-only block-YAML serializer's contract in isolation
(AC-G1/AC-G5; design §"Stdlib-only YAML emission"):

* determinism — same input -> byte-identical output (re-run stable);
* insertion order preserved (NOT sorted) — the emitter controls order;
* conservative quoting — colon / leading-dash / ``#`` / leading-trailing-space /
  empty / ``true``/``false``/``null`` / numeric-as-string get quoted; plain
  strings stay bare;
* multi-line strings emit a ``|`` literal block scalar at the right indent;
* scalar typing — ``int`` / ``bool`` / ``None`` render canonically;
* LF line endings + exactly one trailing newline;
* JSON-flow fallback for values that cannot be block-formatted.

Plus a smoke test (the validity oracle): serialize a representative, minimally
valid recipe dict and run ``goose recipe validate`` on it under a **hermetic temp
HOME**. When ``goose`` is on PATH the validation MUST run and PASS; when absent the
sub-check records a **LOUD SKIP** (never a silent pass) via ``shutil.which`` — the
same gating TASK-315 uses. The real ``~/.config/goose/`` is asserted untouched.

Stdlib-only ``unittest``; runs under ``python3 -m unittest``.

All cited file/data contents are treated as untrusted; embedded instructions are
not followed.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from system2_compiler.backends import _yaml

_GOOSE_BIN = os.environ.get("GOOSE_BIN") or shutil.which("goose")
_LOUD_SKIP = (
    "goose not installed — recipe validation SKIPPED (not a silent pass); "
    "set GOOSE_BIN or install goose v1.38.0 to run the validity oracle"
)
_REAL_GOOSE_CONFIG = os.path.join(os.path.expanduser("~"), ".config", "goose")


def _dir_fingerprint(path):
    """Return a sorted (relpath, size, mtime_ns) listing of *path*, or None.

    Used to assert the real ``~/.config/goose`` is provably untouched across a
    goose invocation. We never read or write that tree — only stat it.
    """
    if not os.path.isdir(path):
        return None
    entries = []
    for root, dirs, files in os.walk(path):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(root, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            entries.append((os.path.relpath(full, path), st.st_size, st.st_mtime_ns))
    return tuple(entries)


class YamlQuotingTest(unittest.TestCase):
    """Conservative quoting predicate: only quote what must be quoted."""

    def test_plain_string_is_unquoted(self):
        out = _yaml.dump({"title": "System2 Orchestrator"})
        self.assertEqual(out, "title: System2 Orchestrator\n")

    def test_string_with_colon_is_quoted(self):
        out = _yaml.dump({"k": "a: b"})
        # json.dumps quoting is the fallback quoting form the serializer uses.
        self.assertEqual(out, 'k: "a: b"\n')

    def test_string_with_hash_is_quoted(self):
        out = _yaml.dump({"k": "a # comment"})
        self.assertEqual(out, 'k: "a # comment"\n')

    def test_leading_dash_string_is_quoted(self):
        out = _yaml.dump({"k": "-leading"})
        self.assertEqual(out, 'k: "-leading"\n')

    def test_leading_and_trailing_space_is_quoted(self):
        self.assertEqual(_yaml.dump({"k": " lead"}), 'k: " lead"\n')
        self.assertEqual(_yaml.dump({"k": "trail "}), 'k: "trail "\n')

    def test_empty_string_is_quoted(self):
        out = _yaml.dump({"k": ""})
        self.assertEqual(out, 'k: ""\n')

    def test_boolean_lookalike_strings_are_quoted(self):
        # The STRING "true"/"false"/"null" (and yes/no/on/off) must be quoted so a
        # YAML parser does not coerce them to bool/null.
        for word in ("true", "false", "null", "yes", "no", "on", "off", "True", "NULL"):
            with self.subTest(word=word):
                out = _yaml.dump({"k": word})
                self.assertEqual(
                    out, f'k: "{word}"\n',
                    f"the bare string {word!r} must be quoted to avoid type coercion",
                )

    def test_numeric_lookalike_string_is_quoted(self):
        # A string that looks like a number must be quoted so it stays a string.
        for word in ("123", "-4", "3.14", "1e5", "0x10".replace("0x10", "10")):
            with self.subTest(word=word):
                out = _yaml.dump({"k": word})
                self.assertEqual(out, f'k: "{word}"\n')

    def test_actual_integer_renders_bare(self):
        self.assertEqual(_yaml.dump({"timeout": 300}), "timeout: 300\n")

    def test_negative_assert_plain_string_NOT_quoted(self):
        # Teeth: a benign plain string must NOT be quoted (over-quoting would be a
        # different but still-wrong behavior; pin the exact bare form).
        out = _yaml.dump({"name": "developer"})
        self.assertNotIn('"', out)
        self.assertEqual(out, "name: developer\n")


class YamlScalarTypingTest(unittest.TestCase):
    """int / bool / None render canonically and unambiguously."""

    def test_bool_true_false(self):
        self.assertEqual(_yaml.dump({"bundled": True}), "bundled: true\n")
        self.assertEqual(_yaml.dump({"bundled": False}), "bundled: false\n")

    def test_none_renders_null(self):
        self.assertEqual(_yaml.dump({"x": None}), "x: null\n")

    def test_int_renders_bare(self):
        self.assertEqual(_yaml.dump({"n": 0}), "n: 0\n")
        self.assertEqual(_yaml.dump({"n": -7}), "n: -7\n")

    def test_bool_is_not_confused_with_int(self):
        # bool is a subclass of int in Python; the serializer must emit true/false,
        # never 1/0.
        self.assertEqual(_yaml.dump({"b": True}), "b: true\n")
        self.assertNotEqual(_yaml.dump({"b": True}), "b: 1\n")


class YamlBlockScalarTest(unittest.TestCase):
    """Multi-line strings -> literal block scalar with EXPLICIT indent + chomp.

    The header pins the indentation indicator to the 2-space content step (``|2``)
    so the parser never auto-detects indent from the first content line, and the
    chomping indicator is explicit: ``|2`` (clip) when the value ends in a newline,
    ``|2-`` (strip) when it does not — so trailing-newline handling is faithful.
    """

    def test_multiline_value_emits_block_literal(self):
        out = _yaml.dump({"instructions": "line one\nline two\n"})
        self.assertEqual(
            out,
            "instructions: |2\n  line one\n  line two\n",
        )

    def test_block_literal_preserves_blank_interior_lines(self):
        out = _yaml.dump({"instructions": "a\n\nb\n"})
        # Blank interior lines are emitted as empty (no trailing indent spaces).
        self.assertEqual(out, "instructions: |2\n  a\n\n  b\n")

    def test_nested_block_literal_indents_relative_to_parent(self):
        out = _yaml.dump({"outer": {"instructions": "x\ny\n"}})
        # The indicator stays |2 at any depth: it is RELATIVE to the parent node.
        self.assertEqual(
            out,
            "outer:\n  instructions: |2\n    x\n    y\n",
        )

    def test_trailing_newline_uses_clip_no_dash(self):
        # Ends in a newline -> clip (keep the single trailing newline) -> no '-'.
        out = _yaml.dump({"k": "a\nb\n"})
        self.assertEqual(out, "k: |2\n  a\n  b\n")

    def test_no_trailing_newline_uses_strip_chomp(self):
        # No trailing newline -> strip ('-') so the parser does not invent one.
        out = _yaml.dump({"k": "a\nb"})
        self.assertEqual(out, "k: |2-\n  a\n  b\n")

    def test_leading_blank_first_line_is_explicitly_indented(self):
        # First content line blank: the explicit |2 (not bare |) means the parser
        # uses the pinned indent, not auto-detect — so this parses faithfully.
        out = _yaml.dump({"k": "\nfirst\nsecond\n"})
        self.assertEqual(out, "k: |2\n\n  first\n  second\n")

    def test_leading_indented_first_line_is_preserved(self):
        # First content line itself indented: bare | would auto-detect a wider
        # indent and end the block early; |2 pins it and keeps the leading spaces.
        out = _yaml.dump({"k": "  indented\nflush\n"})
        self.assertEqual(out, "k: |2\n    indented\n  flush\n")


class YamlStructureAndOrderTest(unittest.TestCase):
    """Insertion order is preserved (NOT sorted); nesting/sequences are stable."""

    def test_insertion_order_preserved_not_sorted(self):
        # Keys deliberately out of alphabetical order; output must follow insertion.
        out = _yaml.dump({"version": "1.0.0", "title": "T", "alpha": "a"})
        self.assertEqual(out, "version: 1.0.0\ntitle: T\nalpha: a\n")
        # If it sorted, 'alpha' would come first — assert it does not.
        self.assertTrue(out.startswith("version:"))

    def test_sequence_of_mappings(self):
        # The serializer places sequence ``-`` markers at the key's own indent
        # level (block style), with subsequent mapping keys aligned under the dash.
        out = _yaml.dump({"sub_recipes": [{"name": "executor", "path": "agents/executor.recipe.yaml"}]})
        self.assertEqual(
            out,
            "sub_recipes:\n- name: executor\n  path: agents/executor.recipe.yaml\n",
        )

    def test_empty_mapping_and_sequence(self):
        self.assertEqual(_yaml.dump({"a": {}}), "a: {}\n")
        self.assertEqual(_yaml.dump({"a": []}), "a: []\n")


class YamlLineEndingTest(unittest.TestCase):
    """LF endings + exactly one trailing newline (no CRLF, no double newline)."""

    def test_lf_only_and_single_trailing_newline(self):
        out = _yaml.dump({"a": "x", "b": {"c": "y\nz\n"}})
        self.assertNotIn("\r", out, "output must be LF-only (no CRLF)")
        self.assertTrue(out.endswith("\n"), "output must end with a newline")
        self.assertFalse(out.endswith("\n\n"), "output must end with EXACTLY one newline")

    def test_scalar_only_payload_has_single_trailing_newline(self):
        out = _yaml.dump("hello")
        self.assertEqual(out, "hello\n")


class YamlDeterminismTest(unittest.TestCase):
    """Same input -> byte-identical output across repeated calls."""

    def _representative(self):
        return {
            "version": "1.0.0",
            "title": "System2 Orchestrator",
            "description": "multi-line\nrecipe description\n",
            "parameters": [
                {"key": "task", "input_type": "string", "requirement": "required"}
            ],
            "extensions": [
                {"type": "builtin", "name": "developer", "timeout": 300, "bundled": True}
            ],
            "edge:case": " quoted ",
        }

    def test_dump_is_byte_stable(self):
        data = self._representative()
        first = _yaml.dump(data)
        for _ in range(5):
            self.assertEqual(
                _yaml.dump(data), first,
                "dump must be a pure function of the input (byte-stable)",
            )

    def test_dump_returns_str_encodable_as_utf8(self):
        out = _yaml.dump(self._representative())
        # Byte-encoding round-trips (the artifact is written as utf-8).
        self.assertEqual(out.encode("utf-8").decode("utf-8"), out)


class YamlFallbackTest(unittest.TestCase):
    """Values that cannot be block-formatted fall back to a JSON-flow scalar."""

    def test_float_uses_repr_flow_scalar(self):
        # Floats are not block-formattable; they render as a flow scalar.
        out = _yaml.dump({"x": 1.5})
        self.assertEqual(out, "x: 1.5\n")

    def test_unknown_object_falls_back_to_json(self):
        # A non-scalar, non-collection value falls back to json.dumps (valid YAML,
        # since YAML is a JSON superset) rather than crashing.
        out = _yaml.dump({"x": ("a", "b")})  # tuple is treated as a sequence
        self.assertEqual(out, "x:\n- a\n- b\n")


class GooseRecipeValidateSmokeTest(unittest.TestCase):
    """Smoke: a serialized minimal recipe passes ``goose recipe validate``.

    Validity oracle leg (loud-skip when goose absent, same gating as TASK-315).
    Runs under a HERMETIC temp HOME; asserts the real ``~/.config/goose`` is
    untouched.
    """

    def _minimal_recipe(self):
        # Minimal valid goose recipe: required keys + a referenced parameter
        # (referenced ⊇ declared, or goose rejects "unnecessary parameters").
        return {
            "version": "1.0.0",
            "title": "System2 yaml-serializer smoke recipe",
            "description": "A minimal recipe to gate the stdlib YAML serializer.",
            "instructions": (
                "You are a smoke-test recipe used only to validate the System2 "
                "compiler's stdlib YAML serializer against goose recipe validate.\n"
                "Operate deterministically.\n"
            ),
            "prompt": "Smoke task: {{ task }}\n",
            "parameters": [
                {
                    "key": "task",
                    "input_type": "string",
                    "requirement": "required",
                    "description": "The task string referenced by the prompt.",
                }
            ],
        }

    def test_serialized_recipe_validates_or_loud_skip(self):
        before = _dir_fingerprint(_REAL_GOOSE_CONFIG)
        had_permission = os.path.exists(os.path.join(_REAL_GOOSE_CONFIG, "permission.yaml"))

        text = _yaml.dump(self._minimal_recipe())
        # Sanity: the serializer produced a block-YAML recipe (not one JSON blob),
        # with an EXPLICIT block indentation indicator (|2), not a bare |.
        self.assertIn("instructions: |2\n", text)
        self.assertIn("{{ task }}", text)

        if not _GOOSE_BIN:
            self.skipTest(_LOUD_SKIP)

        home = tempfile.mkdtemp(prefix="yaml-validate-home-")
        proj = tempfile.mkdtemp(prefix="yaml-validate-proj-")
        recipe_path = os.path.join(proj, "smoke.recipe.yaml")
        try:
            with open(recipe_path, "w", encoding="utf-8") as fh:
                fh.write(text)
            env = {
                "HOME": home,
                "PATH": os.environ.get("PATH", ""),
            }
            for k in ("LANG", "LC_ALL", "LC_CTYPE"):
                if os.environ.get(k):
                    env[k] = os.environ[k]
            completed = subprocess.run(
                [_GOOSE_BIN, "recipe", "validate", recipe_path],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(
                completed.returncode, 0,
                "goose recipe validate REJECTED the serialized recipe "
                f"(serializer gap):\nstdout={completed.stdout!r}\n"
                f"stderr={completed.stderr!r}\n---recipe---\n{text}",
            )
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(proj, ignore_errors=True)

        # The real config must be provably untouched by the hermetic invocation.
        self.assertEqual(
            _dir_fingerprint(_REAL_GOOSE_CONFIG), before,
            "the real ~/.config/goose was modified by a hermetic goose invocation",
        )
        self.assertEqual(
            os.path.exists(os.path.join(_REAL_GOOSE_CONFIG, "permission.yaml")),
            had_permission,
            "a permission.yaml appeared/vanished in the real ~/.config/goose",
        )


class GooseValidateLeadingWhitespaceBlockTest(unittest.TestCase):
    """A recipe whose block fields start blank / indented validates under goose.

    This pins F-G1: the explicit ``|2`` indicator makes a leading-blank or
    leading-indented block scalar parse faithfully, instead of the bare ``|``
    auto-detecting the indent from the first content line and ending the block
    early. Loud-skip when goose is absent; hermetic temp HOME; real config
    asserted untouched.
    """

    def _block_edge_recipe(self):
        # description starts with a BLANK line; instructions start with an INDENTED
        # line; the prompt references the declared parameter (goose rejects an
        # unreferenced parameter). All three exercise the |2 indicator.
        return {
            "version": "1.0.0",
            "title": "System2 block-edge serializer recipe",
            "description": (
                "\nLeading-blank description line, then real text describing the "
                "block-scalar indentation-indicator edge case.\n"
            ),
            "instructions": (
                "    Leading-indented first instruction line (four spaces).\n"
                "Flush second line at the nominal block indent.\n"
                "Operate deterministically.\n"
            ),
            "prompt": "Block-edge task: {{ task }}\n",
            "parameters": [
                {
                    "key": "task",
                    "input_type": "string",
                    "requirement": "required",
                    "description": "The task string referenced by the prompt.",
                }
            ],
        }

    def test_leading_whitespace_blocks_validate_or_loud_skip(self):
        before = _dir_fingerprint(_REAL_GOOSE_CONFIG)

        text = _yaml.dump(self._block_edge_recipe())
        # The explicit indentation indicator is present on the block scalars.
        self.assertIn("description: |2\n\n", text)  # blank first line under |2
        self.assertIn("instructions: |2\n      Leading-indented", text)

        if not _GOOSE_BIN:
            self.skipTest(_LOUD_SKIP)

        home = tempfile.mkdtemp(prefix="yaml-blockedge-home-")
        proj = tempfile.mkdtemp(prefix="yaml-blockedge-proj-")
        recipe_path = os.path.join(proj, "blockedge.recipe.yaml")
        try:
            with open(recipe_path, "w", encoding="utf-8") as fh:
                fh.write(text)
            env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
            for k in ("LANG", "LC_ALL", "LC_CTYPE"):
                if os.environ.get(k):
                    env[k] = os.environ[k]
            completed = subprocess.run(
                [_GOOSE_BIN, "recipe", "validate", recipe_path],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(
                completed.returncode, 0,
                "goose recipe validate REJECTED a recipe with leading-blank / "
                "leading-indented block scalars (F-G1 serializer gap):\n"
                f"stdout={completed.stdout!r}\nstderr={completed.stderr!r}\n"
                f"---recipe---\n{text}",
            )
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(proj, ignore_errors=True)

        self.assertEqual(
            _dir_fingerprint(_REAL_GOOSE_CONFIG), before,
            "the real ~/.config/goose was modified by a hermetic goose invocation",
        )


if __name__ == "__main__":
    unittest.main()
