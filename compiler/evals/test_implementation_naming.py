"""Guard durable names in maintained compiler implementation code."""

import re
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_ROOTS = (
    _REPO_ROOT / "compiler" / "system2_compiler",
    _REPO_ROOT / "compiler" / "evals",
    _REPO_ROOT / "compiler" / "tools",
    _REPO_ROOT / "evals",
    _REPO_ROOT / "plugin" / "scripts",
)
_NUMBERED_MILESTONE = re.compile(r"phase[-_ ]?\d", re.IGNORECASE)


class DurableImplementationNamingTest(unittest.TestCase):
    def test_maintained_source_and_eval_names_contain_no_numbered_milestones(self):
        offenders = []
        paths = [
            path
            for root in _SOURCE_ROOTS
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts
        ]
        for path in paths:
            rel = path.relative_to(_REPO_ROOT).as_posix()
            if _NUMBERED_MILESTONE.search(rel):
                offenders.append(f"{rel}: numbered milestone in filename")
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if _NUMBERED_MILESTONE.search(line):
                    offenders.append(f"{rel}:{number}: {line.strip()}")
        self.assertEqual([], offenders, "numbered milestones leaked into maintained implementation:\n" + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
