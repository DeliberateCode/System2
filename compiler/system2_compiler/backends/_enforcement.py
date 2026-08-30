"""Shared enforcement matcher generation."""

import json
from typing import Dict, List, Tuple

__all__ = [
    "build_dangerous_command_patterns",
    "build_lease_gate_source",
    "build_sensitive_path_patterns",
]

# Fixed order is semantic because consumers report the first matching reason.
# These patterns mirror the Claude dangerous-command and sensitive-path hooks.
_RM_RF_FLAGS = (
    r"(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*"
    r"|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*"
    r"|-r\s+-f|-f\s+-r"
    r"|-[a-zA-Z]*r[a-zA-Z]*\s+-[a-zA-Z]*f[a-zA-Z]*"
    r"|-[a-zA-Z]*f[a-zA-Z]*\s+-[a-zA-Z]*r[a-zA-Z]*"
    r"|--recursive\s+--force|--force\s+--recursive"
    r"|--recursive\s+-f|-f\s+--recursive"
    r"|-r\s+--force|--force\s+-r"
    r"|--recursive[^|;&]*-f|-f[^|;&]*--recursive"
    r"|-r[^|;&]*--force|--force[^|;&]*-r)"
)

_DANGEROUS_REGEXES = (
    (
        r"\brm\s+" + _RM_RF_FLAGS + r"\s*(/|/\*)\s*($|;|\||&)",
        "m",
        "rm -rf targeting root filesystem (/) is extremely dangerous",
    ),
    (
        r"\brm\s+" + _RM_RF_FLAGS + r"\s*\./?(?:\s|$|;|\||&)",
        "m",
        "rm -rf targeting current directory (.) could delete critical files",
    ),
    (
        r"\brm\s+" + _RM_RF_FLAGS + r"\s*\.\./?(?:\s|$|;|\||&)",
        "m",
        "rm -rf targeting parent directory (..) could delete critical files",
    ),
    (
        r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*|-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*)\s*~",
        "m",
        "rm -rf targeting the home directory (~) could delete user data",
    ),
    (
        r"\bsudo\s+rm\s+" + _RM_RF_FLAGS + r"\s+",
        "m",
        "sudo rm -rf with elevated privileges is extremely dangerous",
    ),
    (
        r"\bchmod\s+(.*\s+)?777\s+",
        "m",
        "chmod 777 makes files world-writable and executable, a security risk",
    ),
    (
        r"\bgit\s+reset\s+--hard\b",
        "m",
        "git reset --hard discards uncommitted changes permanently",
    ),
    (
        r"\bgit\s+push\s+[^;|&]*--force(-with-lease)?[^;|&]*\b(main|master)\b",
        "m",
        "git push --force to main/master can destroy shared commit history",
    ),
    (
        r"\bgit\s+push\s+[^;|&]*\b(main|master)\b[^;|&]*--force(-with-lease)?",
        "m",
        "git push --force to main/master can destroy shared commit history",
    ),
    (
        r"\bgit\s+push\s+[^;|&]*-f\s+[^;|&]*\b(main|master)\b",
        "m",
        "git push -f to main/master can destroy shared commit history",
    ),
    (
        r"\bgit\s+push\s+[^;|&]*\b(main|master)\b[^;|&]*\s+-f\b",
        "m",
        "git push -f to main/master can destroy shared commit history",
    ),
    (
        r"\bmkfs\b",
        "m",
        "mkfs reformats a filesystem and destroys all data on the device",
    ),
    (
        r"\bdd\s+[^|;&]*\bof=",
        "m",
        "dd writing to a device/file (of=) can irreversibly overwrite data",
    ),
    (
        r"\bshutdown\b",
        "m",
        "shutdown halts the machine",
    ),
    (
        r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(sh|bash|zsh)\b",
        "m",
        "piping a downloaded script straight into a shell executes untrusted code",
    ),
    (
        r"\bDROP\s+TABLE\b",
        "i",
        "DROP TABLE would permanently delete a database table and its data",
    ),
    (
        r"\bDELETE\s+FROM\s+\w+\s*($|;|\|)",
        "im",
        "DELETE FROM without a WHERE clause would delete all rows in the table",
    ),
)

# Codex-only canary used to verify hook liveness.
_CANARY_SENTINEL = "system2-hook-canary"
_CANARY_ENTRY = (
    _CANARY_SENTINEL,
    "",
    "system2-canary-blocked",
)

_SENSITIVE_REGEXES = (
    (r"(^|/)\.env$", "", "Environment file (.env)"),
    (r"(^|/)\.env\.[A-Za-z0-9_-]+$", "", "Environment file (.env.*)"),
    (r"(^|/)\.ssh(/|$)", "", "SSH directory (.ssh/)"),
    (r"(^|/)\.git(/|$)", "", "Git directory (.git/)"),
    (r"(^|/)\.aws(/|$)", "", "AWS credentials directory (.aws/)"),
    (r"(^|/)\.gnupg(/|$)", "", "GPG directory (.gnupg/)"),
    (r"credentials", "i", "File containing 'credentials'"),
    (r"secrets", "i", "File containing 'secrets'"),
    (r"\.pem$", "i", "PEM certificate/key file (*.pem)"),
    (r"\.key$", "i", "Key file (*.key)"),
    (r"(^|/)id_rsa$", "", "RSA private key (id_rsa)"),
    (r"(^|/)id_ed25519$", "", "Ed25519 private key (id_ed25519)"),
    (r"(^|/)id_ecdsa$", "", "ECDSA private key (id_ecdsa)"),
    (r"(^|/)\.netrc$", "", "Netrc credentials file (.netrc)"),
    (r"(^|/)\.npmrc$", "", "NPM config file (.npmrc) - may contain tokens"),
    (r"(^|/)\.pypirc$", "", "PyPI config file (.pypirc) - may contain tokens"),
)


def build_dangerous_command_patterns(
    include_canary: bool = False,
) -> List[Tuple[str, str, str]]:
    """The proven dangerous-command matcher set as ``(source, flags, reason)`` tuples."""
    patterns = list(_DANGEROUS_REGEXES)
    if include_canary:
        patterns.append(_CANARY_ENTRY)
    return patterns


def build_sensitive_path_patterns() -> List[Tuple[str, str, str]]:
    """The proven sensitive-path matcher set as ``(source, flags, reason)`` tuples."""
    return list(_SENSITIVE_REGEXES)


def build_lease_gate_source(write_scopes: Dict[str, List[str]]) -> str:
    """Emit the proven, runtime-neutral lease-gate matcher source (JavaScript)."""
    scope_items = ",\n  ".join(
        f"[{json.dumps(role)}, {json.dumps('|'.join(patterns))}]"
        for role, patterns in write_scopes.items()
    )
    writeable = ", ".join(json.dumps(role) for role in sorted(write_scopes))

    lines: List[str] = []
    lines.append("// Per-role write scopes; absent or empty scopes deny writes.")
    lines.append("const ROLE_WRITE_SCOPES = new Map([")
    if scope_items:
        lines.append(f"  {scope_items},")
    lines.append("]);")
    lines.append(f"const WRITEABLE_ROLES = new Set([{writeable}]);")
    lines.append("")
    lines.append("// Normalize project-relative paths and return null on any escape.")
    lines.append("function normalizeProjectPath(p) {")
    lines.append('  if (typeof p !== "string" || p.length === 0) return null;')
    lines.append('  if (p === "~" || p.startsWith("~/")) return null; // home dir: outside project')
    lines.append('  const slashed = p.replace(/\\\\/g, "/");')
    lines.append('  if (slashed.startsWith("/")) return null; // absolute: cannot confirm in-project -> block')
    lines.append("  const segs = [];")
    lines.append('  for (const part of slashed.split("/")) {')
    lines.append('    if (part === "" || part === ".") continue;')
    lines.append('    if (part === "..") {')
    lines.append("      if (segs.length === 0) return null; // escapes the project root -> block")
    lines.append("      segs.pop();")
    lines.append("      continue;")
    lines.append("    }")
    lines.append("    segs.push(part);")
    lines.append("  }")
    lines.append('  return segs.join("/");')
    lines.append("}")
    lines.append("")
    lines.append("// Return the first lease violation; invalid scopes fail closed.")
    lines.append("function leaseViolation(activeRole, paths) {")
    lines.append("  if (!paths || paths.length === 0) return undefined;")
    lines.append("  const scope = ROLE_WRITE_SCOPES.get(activeRole);")
    lines.append("  if (!WRITEABLE_ROLES.has(activeRole) || !scope) return paths[0];")
    lines.append("  let re;")
    lines.append("  try {")
    lines.append('    re = new RegExp("^(?:" + scope + ")");')
    lines.append("  } catch {")
    lines.append("    return paths[0]; // an uncompilable scope fails closed, not open")
    lines.append("  }")
    lines.append("  for (const p of paths) {")
    lines.append("    const norm = normalizeProjectPath(p);")
    lines.append("    if (norm === null || !re.test(norm)) return p; // escape or off-scope -> block")
    lines.append("  }")
    lines.append("  return undefined;")
    lines.append("}")
    return "\n".join(lines) + "\n"
