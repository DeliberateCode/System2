"""The installed compiler package carries the Codex hook reference tree."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_COMPILER_ROOT = Path(__file__).resolve().parents[1]


class CodexWheelInstallTest(unittest.TestCase):
    def test_fresh_wheel_install_can_materialize_hooks_outside_checkout(self):
        """Package data, not the repository's distributions/ directory, backs init."""
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            wheel_dir = temp / "wheel"
            source = temp / "compiler-source"
            shutil.copytree(_COMPILER_ROOT, source, ignore=shutil.ignore_patterns(
                "build", "*.egg-info", "__pycache__"
            ))
            subprocess.run(
                [
                    sys.executable, "-m", "pip", "wheel", "--no-deps",
                    str(source), "-w", str(wheel_dir),
                ],
                check=True, capture_output=True, text=True,
            )
            wheel = next(wheel_dir.glob("system2_compiler-*.whl"))
            venv = temp / "venv"
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
            python = venv / "bin" / "python"
            subprocess.run(
                [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
                check=True, capture_output=True, text=True,
            )
            home = temp / "codex-home"
            code = (
                "from system2_compiler.backends.codex import codex_init; "
                f"r = codex_init(codex_home={str(home)!r}); "
                "assert r['status'] == 'installed'; "
                "assert r['hook_files']"
            )
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            subprocess.run(
                [str(python), "-c", code], cwd=temp, env=env,
                check=True, capture_output=True, text=True,
            )
            self.assertTrue((home / "system2" / "hooks" / "system2-shell-guard.js").is_file())


if __name__ == "__main__":
    unittest.main()
