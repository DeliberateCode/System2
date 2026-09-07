"""Regression coverage for permanent files written through mkstemp + replace."""

import os
import stat
import tempfile
import unittest

from system2_compiler.backends import claude_code, codex, pi


class EmissionFileModeTest(unittest.TestCase):
    def _assert_mode(self, writer, target_rel):
        with tempfile.TemporaryDirectory() as root:
            old_umask = os.umask(0o027)
            try:
                writer(root)
            finally:
                os.umask(old_umask)
            target = os.path.join(root, target_rel)
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o640)

            os.chmod(target, 0o600)
            writer(root)
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)

    def test_codex_writer_respects_umask_and_preserves_existing_mode(self):
        self._assert_mode(
            lambda root: codex._write_outputs(root, [("example.md", "content\n")]),
            "example.md",
        )

    def test_pi_writer_respects_umask_and_preserves_existing_mode(self):
        self._assert_mode(
            lambda root: pi._write_outputs(root, [("example.md", "content\n")]),
            "example.md",
        )

    def test_claude_writer_respects_umask_and_preserves_existing_mode(self):
        self._assert_mode(
            lambda root: claude_code._write_outputs(root, "# System2\n", {}, []),
            "CLAUDE.md",
        )


if __name__ == "__main__":
    unittest.main()
