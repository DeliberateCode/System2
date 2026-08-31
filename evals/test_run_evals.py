"""Regression tests for repository-level evaluations."""

import importlib.util
import os
import tempfile
from pathlib import Path
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "root_run_evals", Path(__file__).with_name("run_evals.py")
)
assert _SPEC and _SPEC.loader
run_evals = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(run_evals)


def test_generated_identifier_guard_rejects_a_root_file():
    fd, planted_path = tempfile.mkstemp(
        prefix=".identifier-guard-", suffix=".txt", dir=run_evals.REPO_ROOT
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("TASK" + "-999\n")
        with mock.patch.object(run_evals, "record") as record:
            run_evals.check_no_generated_spec_identifiers_in_implementation_files()
    finally:
        os.unlink(planted_path)

    record.assert_called_once()
    assert record.call_args.args[2] is False
    assert os.path.basename(planted_path) in record.call_args.args[3]
