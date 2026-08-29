"""
Hook Security Checks

Shared module for validating hook scripts comply with System2 security
requirements: no external dependencies, no network calls.

Uses only Python 3.8+ stdlib. No external dependencies.

Public API:
    check_no_external_deps(hook_path) -> list of violation strings
    check_no_network_calls(hook_path) -> list of violation strings
    check_hook_security(hook_path) -> dict with 'passed' and 'violations'
"""

import ast
import re
from pathlib import Path
from typing import Dict, List, Optional

# Modules allowed in hook scripts. Includes stdlib and the internal
# _hook_utils helper used across the System2 hook codebase.
STDLIB_MODULES = frozenset({
    # Built-ins and core
    "__future__", "_thread", "abc", "ast", "atexit", "builtins",
    # Collections and data
    "array", "bisect", "collections", "copy", "dataclasses", "enum",
    "heapq", "itertools", "operator", "pprint", "reprlib", "types",
    # Concurrency
    "concurrent", "multiprocessing", "queue", "sched", "threading",
    # Encoding and compression
    "base64", "binascii", "bz2", "codecs", "gzip", "lzma", "quopri",
    "uu", "zlib",
    # File and I/O
    "filecmp", "fileinput", "fnmatch", "glob", "io", "linecache",
    "mmap", "os", "pathlib", "shutil", "stat", "tempfile",
    # Functional programming
    "contextlib", "functools",
    # Inspection and debugging
    "dis", "inspect", "pdb", "profile", "traceback", "tracemalloc",
    # Logging
    "logging", "warnings",
    # Math and numbers
    "cmath", "decimal", "fractions", "math", "numbers", "random",
    "statistics",
    # Parsing and text
    "argparse", "configparser", "csv", "difflib", "getopt", "gettext",
    "json", "locale", "optparse", "re", "shlex", "string", "textwrap",
    "token", "tokenize", "unicodedata",
    # Platform and system
    "ctypes", "errno", "platform", "signal", "struct", "subprocess",
    "sys", "sysconfig",
    # Serialization
    "marshal", "pickle", "shelve",
    # Testing
    "doctest", "unittest",
    # Time
    "calendar", "datetime", "time",
    # Type hints
    "typing",
    # XML and markup
    "html", "xml",
    # Crypto
    "hashlib", "hmac", "secrets",
    # Misc stdlib
    "pkgutil", "importlib", "runpy", "ensurepip", "venv",
    "uuid",
    # Internal hook utility
    "_hook_utils",
})

# Regex patterns that indicate network access (direct Python modules).
NETWORK_PATTERNS = [
    r"\brequests\.",
    r"\burllib\.",
    r"\bhttp\.client",
    r"\bhttp\.server",
    r"\bsocket\.",
    r"\bhttpx\.",
    r"\baiohttp\.",
    r"\bwebbrowser\.",
]

# Patterns that indicate network access via process execution.
# These are checked only on lines that also contain subprocess/os.system/os.popen
# to avoid false positives on path strings like ".ssh/".
SUBPROCESS_NETWORK_COMMANDS = frozenset({
    "curl", "wget", "nc", "ncat", "netcat", "httpie",
    "ssh", "scp", "sftp", "rsync", "telnet",
    "nslookup", "dig",
})

_NETWORK_RE = re.compile("|".join(NETWORK_PATTERNS))

# Modules that are stdlib but banned in overlay hooks because they enable
# arbitrary process execution (and thus undetectable network access).
OVERLAY_BANNED_MODULES = frozenset({"subprocess", "multiprocessing", "ctypes"})
_SUBPROCESS_CALL_RE = re.compile(
    r"subprocess\.(run|call|Popen|check_call|check_output)"
    r"|os\.(system|popen)"
)


def check_no_external_deps(hook_path: str) -> List[str]:
    """Check a hook script for non-stdlib imports.

    Args:
        hook_path: Absolute or relative path to a Python hook script.

    Returns:
        List of violation strings. Empty list means the file is clean.
        Each entry has the format ``"<filename>:<lineno>: non-stdlib import: <line>"``.
    """
    path = Path(hook_path)
    violations: List[str] = []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        violations.append(
            f"{path.name}: syntax error (line {exc.lineno}): {exc.msg}"
        )
        # Also fall back to line-based parsing to catch import violations.
        for i, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                if stripped.startswith("from "):
                    module = stripped.split()[1].split(".")[0]
                else:
                    for tok in stripped.split()[1:]:
                        mod = tok.rstrip(",").split(".")[0]
                        if mod and mod not in STDLIB_MODULES:
                            violations.append(
                                f"{path.name}:{i}: non-stdlib import: {stripped}"
                            )
                            break
                    continue
                if module not in STDLIB_MODULES:
                    violations.append(
                        f"{path.name}:{i}: non-stdlib import: {stripped}"
                    )
        return violations

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_module = alias.name.split(".")[0]
                if top_module not in STDLIB_MODULES:
                    violations.append(
                        f"{path.name}:{node.lineno}: non-stdlib import: import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_module = node.module.split(".")[0]
                if top_module not in STDLIB_MODULES:
                    violations.append(
                        f"{path.name}:{node.lineno}: non-stdlib import: from {node.module} import ..."
                    )
    return violations


_NETWORK_MODULES = {
    "requests", "urllib", "http", "socket", "httpx", "aiohttp", "webbrowser",
}


def check_no_network_calls(hook_path: str) -> List[str]:
    """Check a hook script for network call patterns using AST analysis.

    Tracks import aliases (``import socket as s``, ``from urllib import
    request``) and flags attribute access or calls on network-module
    bindings.  Falls back to line-based regex if the file has syntax errors.
    """
    path = Path(hook_path)
    violations: List[str] = []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        # Fallback: line-based scan for direct patterns.
        for i, line in enumerate(source.splitlines(), 1):
            if _NETWORK_RE.search(line):
                violations.append(
                    f"{path.name}:{i}: network pattern: {line.strip()}"
                )
        return violations

    # Build a map of local names bound to network modules.
    # e.g. ``import socket as s`` → net_bindings["s"] = "socket"
    # e.g. ``from urllib import request`` → net_bindings["request"] = "urllib"
    net_bindings: Dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _NETWORK_MODULES:
                    local = alias.asname if alias.asname else alias.name
                    net_bindings[local.split(".")[0]] = top
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in _NETWORK_MODULES:
                    for alias in node.names:
                        local = alias.asname if alias.asname else alias.name
                        net_bindings[local] = top

    if not net_bindings:
        return violations

    # Walk AST for attribute access or calls using network bindings.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            name = _get_root_name(node.value)
            if name and name in net_bindings:
                violations.append(
                    f"{path.name}:{node.lineno}: network module access "
                    f"({net_bindings[name]}): {name}.{node.attr}"
                )
        elif isinstance(node, ast.Call):
            name = _get_root_name(node.func)
            if name and name in net_bindings:
                violations.append(
                    f"{path.name}:{node.lineno}: network module call "
                    f"({net_bindings[name]}): {name}(...)"
                )

    return violations


def _get_root_name(node: ast.AST) -> Optional[str]:
    """Extract the leftmost Name from an attribute chain."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _get_root_name(node.value)
    return None


_DYNAMIC_IMPORT_RE = re.compile(
    r"__import__\s*\(|importlib\.import_module\s*\(|"
    r"\bexec\s*\(|\beval\s*\("
)


def check_no_banned_overlay_modules(hook_path: str) -> List[str]:
    """Check an overlay hook for modules that are banned in overlay context.

    Overlay hooks must not use subprocess, multiprocessing, or ctypes because
    these enable arbitrary process execution and undetectable network access.
    Also detects os.system/os.popen calls, dynamic imports (__import__,
    importlib.import_module), and exec/eval which bypass static import checks.
    Base System2 hooks may use these modules; this check is overlay-specific.
    """
    path = Path(hook_path)
    violations: List[str] = []
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return violations

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        violations.append(
            f"{path.name}: syntax error (line {exc.lineno}): {exc.msg}"
        )
        for i, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            for mod in OVERLAY_BANNED_MODULES:
                if stripped.startswith(f"import {mod}") or stripped.startswith(f"from {mod}"):
                    violations.append(
                        f"{path.name}:{i}: banned module in overlay hook: {stripped}"
                    )
            if _SUBPROCESS_CALL_RE.search(stripped):
                violations.append(
                    f"{path.name}:{i}: process execution in overlay hook: {stripped}"
                )
            if _DYNAMIC_IMPORT_RE.search(stripped):
                violations.append(
                    f"{path.name}:{i}: dynamic import/exec in overlay hook: {stripped}"
                )
        return violations

    # --- Static import bans ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in OVERLAY_BANNED_MODULES:
                    violations.append(
                        f"{path.name}:{node.lineno}: banned module in overlay "
                        f"hook: import {alias.name} (subprocess/process "
                        f"execution not allowed in overlay hooks)"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in OVERLAY_BANNED_MODULES:
                    violations.append(
                        f"{path.name}:{node.lineno}: banned module in overlay "
                        f"hook: from {node.module} import ... "
                        f"(subprocess/process execution not allowed in "
                        f"overlay hooks)"
                    )

    # --- Dynamic import and code execution bans ---
    # Track importlib bindings for detecting importlib.import_module().
    importlib_bindings: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib" or alias.name.startswith("importlib."):
                    local = alias.asname if alias.asname else alias.name
                    importlib_bindings[local.split(".")[0]] = "importlib"
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] == "importlib":
                for alias in node.names:
                    local = alias.asname if alias.asname else alias.name
                    if alias.name == "import_module":
                        importlib_bindings[local] = "importlib.import_module"

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        # __import__("module")
        if isinstance(func, ast.Name) and func.id == "__import__":
            violations.append(
                f"{path.name}:{node.lineno}: dynamic import in overlay "
                f"hook: __import__() (not allowed in overlay hooks)"
            )
            continue

        # exec(...) / eval(...)
        if isinstance(func, ast.Name) and func.id in ("exec", "eval"):
            violations.append(
                f"{path.name}:{node.lineno}: dynamic code execution in "
                f"overlay hook: {func.id}() (not allowed in overlay hooks)"
            )
            continue

        # importlib.import_module(...)
        if isinstance(func, ast.Attribute) and func.attr == "import_module":
            root = _get_root_name(func.value)
            if root and root in importlib_bindings:
                violations.append(
                    f"{path.name}:{node.lineno}: dynamic import in overlay "
                    f"hook: {root}.import_module() "
                    f"(not allowed in overlay hooks)"
                )
                continue

        # from importlib import import_module; import_module(...)
        if isinstance(func, ast.Name) and func.id in importlib_bindings:
            mod_path = importlib_bindings[func.id]
            if "import_module" in mod_path:
                violations.append(
                    f"{path.name}:{node.lineno}: dynamic import in overlay "
                    f"hook: {func.id}() (imported from {mod_path}, "
                    f"not allowed in overlay hooks)"
                )
                continue

    # --- os.system/os.popen bans ---
    os_bindings: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" or alias.name.startswith("os."):
                    local = alias.asname if alias.asname else alias.name
                    os_bindings[local.split(".")[0]] = "os"
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] == "os":
                for alias in node.names:
                    if alias.name in ("system", "popen"):
                        local = alias.asname if alias.asname else alias.name
                        os_bindings[local] = f"os.{alias.name}"

    _BANNED_OS_ATTRS = frozenset({"system", "popen"})

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # os.system(...) / os.popen(...)
        if isinstance(func, ast.Attribute) and func.attr in _BANNED_OS_ATTRS:
            root = _get_root_name(func.value)
            if root and root in os_bindings:
                violations.append(
                    f"{path.name}:{node.lineno}: process execution in "
                    f"overlay hook: {root}.{func.attr}() "
                    f"(not allowed in overlay hooks)"
                )
        # from os import system; system(...)
        elif isinstance(func, ast.Name) and func.id in os_bindings:
            mod_path = os_bindings[func.id]
            if mod_path.startswith("os."):
                violations.append(
                    f"{path.name}:{node.lineno}: process execution in "
                    f"overlay hook: {func.id}() (imported from {mod_path}, "
                    f"not allowed in overlay hooks)"
                )

    return violations


def check_hook_security(hook_path: str, overlay: bool = False) -> Dict:
    """Run all security checks on a hook script.

    Args:
        hook_path: Absolute or relative path to a Python hook script.
        overlay: If True, also check for overlay-banned modules
            (subprocess, multiprocessing, ctypes).

    Returns:
        Dict with keys:
            ``passed`` (bool): True if no violations found.
            ``violations`` (list of str): All violation strings from all checks.
    """
    dep_violations = check_no_external_deps(hook_path)
    net_violations = check_no_network_calls(hook_path)
    all_violations = dep_violations + net_violations
    if overlay:
        all_violations.extend(check_no_banned_overlay_modules(hook_path))
    return {
        "passed": len(all_violations) == 0,
        "violations": all_violations,
    }
