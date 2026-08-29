"""Shared enforcement-matcher generation (extraction, NOT net-new logic).

The proven System2 enforcement primitives — the flag-permutation dangerous-command
regex set, the sensitive-path regex set, and the path-normalizing / fail-closed
lease-gate algorithm — ported VERBATIM from the Claude reference hooks and first
proven in the Pi backend. Factored out here so a second native-enforcement backend
(Codex) reuses the *identical* proven semantics rather than re-implementing them.

Extraction discipline: ``pi.py`` serializes ``build_dangerous_command_patterns()``
and ``build_sensitive_path_patterns()`` into its ``.pi/extensions/system2.ts`` the
same way it serialized the module-level constants before, so the Pi goldens have
ZERO churn — the byte-identity of those goldens is this extraction's proof
obligation. The constants below are byte-for-byte the same tuples pi.py owned.

Imported ONLY from within ``backends/`` (no backend imports another backend; this is
a shared sibling, not a backend). Stdlib-only.

CANARY sentinel (``system2-hook-canary``): a defense-in-depth marker the Codex
doctor skill uses to prove hook liveness. It is added to the dangerous-command set
ONLY via ``build_dangerous_command_patterns(include_canary=True)`` — a codex-only
path. Pi calls the default (``include_canary=False``) so its emitted bytes cannot
change.

------------------------------------------------------------------------------
F11 ReDoS (catastrophic-backtracking) review — constants NOT altered
------------------------------------------------------------------------------
Every dangerous/sensitive regex source below was inspected for catastrophic
backtracking (the exponential shapes ``(X+)+`` / ``(X|X)*`` / ``.*.*`` over
overlapping classes). Findings:

* No pattern contains a quantified GROUP wrapping an ambiguous quantifier — there is
  no ``(...)*`` / ``(...)+`` over an inner ``*``/``+``. ``_RM_RF_FLAGS`` is one
  non-quantified group; its inner ``[a-zA-Z]*``/``[^|;&]*`` runs are each fenced by
  MANDATORY literals (``r``, ``f``, ``-f``, ``--force``), which localizes
  backtracking. No exponential blowup is reachable.
* A few patterns place TWO unbounded runs over the same negated class in sequence,
  separated by a required literal — e.g. the ``git push`` force variants
  (``[^;|&]*--force...[^;|&]*\b(main|master)\b``). On adversarial input that omits
  the required literals this is worst-case POLYNOMIAL (quadratic), never
  exponential.
* The sensitive-path sources are anchored (``^``/``$``/``(^|/)``) with single
  bounded runs — linear.

Per policy the proven constants are LEFT UNCHANGED here. The mitigation for the
quadratic worst case is an input-length cap + a match timeout that resolves to BLOCK
(fail closed) in the CONSUMER — the Codex Node hooks (the implementation work), NOT a constant edit
in this module. The Pi extension already runs these under Pi's own tool_call seam.
"""

import json
from typing import Dict, List, Tuple

__all__ = [
    "build_dangerous_command_patterns",
    "build_lease_gate_source",
    "build_sensitive_path_patterns",
]

# Each entry is (js_regex_source, js_flags, reason). Order is FIXED (deterministic,
# NOT sorted — regex evaluation order is semantic). Serialized by the consuming
# backend into `new RegExp(source, flags)`; the sources are backend constants.
#
#   * _DANGEROUS_REGEXES mirrors dangerous-command-blocker.py: the flag-permutation
#     `_RM_RF_FLAGS` helper + DANGER_PATTERNS (rm -rf at /,.,..; sudo rm -rf; chmod
#     777; git reset --hard; git push --force/-f to main/master; DROP TABLE; DELETE
#     FROM without WHERE), plus word-boundaried mkfs/dd-of=/shutdown/curl|sh and
#     rm -rf ~ from the prior policy set (anchored, FP-resistant — no bare "dd").
#   * _SENSITIVE_REGEXES mirrors sensitive-file-protector.py: segment/basename-anchored
#     (.env, .env.*, .ssh/, .git/, .aws/, .gnupg/, id_rsa/ed25519/ecdsa, *.pem, *.key,
#     .netrc/.npmrc/.pypirc) plus the reference's case-insensitive credentials/secrets
#     markers.
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

# Defense-in-depth marker (F6). Codex registers this per-hook so its doctor skill can
# prove hook liveness: a canary command carrying the sentinel is BLOCKED, and the
# block reason lets the doctor parse back the nonce (`system2-canary-blocked:<nonce>`
# — the consumer appends the nonce). Codex-only; never in Pi's default set.
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
    """The proven dangerous-command matcher set as ``(source, flags, reason)`` tuples.

    Fixed order (regex evaluation order is semantic). Language-neutral: the consuming
    backend serializes each tuple into its runtime's regex literal. ``include_canary``
    (codex-only, default off) appends the ``system2-hook-canary`` sentinel entry LAST
    so it never perturbs Pi's emission, which calls the default.
    """
    patterns = list(_DANGEROUS_REGEXES)
    if include_canary:
        patterns.append(_CANARY_ENTRY)
    return patterns


def build_sensitive_path_patterns() -> List[Tuple[str, str, str]]:
    """The proven sensitive-path matcher set as ``(source, flags, reason)`` tuples.

    Segment/basename-anchored, fixed order. Language-neutral; the consuming backend
    serializes each tuple into its runtime's regex literal.
    """
    return list(_SENSITIVE_REGEXES)


def build_lease_gate_source(write_scopes: Dict[str, List[str]]) -> str:
    """Emit the proven, runtime-neutral lease-gate matcher source (JavaScript).

    Encodes the SAME algorithm the Pi backend proved inline: project-relative path
    normalization that FAILS CLOSED on any escape (leading ``~``, absolute path, or a
    ``..`` that leaves the root), per-role scopes OR-joined from the role allowlist,
    start-anchored ``^(?:scope)`` matching, and empty-scope / unknown-role fail-closed
    (an unscoped role cannot write). ``write_scopes`` maps role name -> allowlist
    regex-source list; a role present with an empty list, or absent entirely, blocks
    every write.

    Consumed by the Codex Node hooks (the implementation work); Pi keeps its own inline emission
    (interwoven with Pi's ExtensionAPI event shape) so Pi's TS bytes cannot drift.
    """
    scope_items = ",\n  ".join(
        f"[{json.dumps(role)}, {json.dumps('|'.join(patterns))}]"
        for role, patterns in write_scopes.items()
    )
    writeable = ", ".join(json.dumps(role) for role in sorted(write_scopes))

    lines: List[str] = []
    lines.append("// Per-role write scope (OR-joined allowlist regex source).")
    lines.append("// A role absent from this map, or present with an empty scope,")
    lines.append("// writes nothing (fail-closed, see WRITEABLE_ROLES).")
    lines.append("const ROLE_WRITE_SCOPES = new Map([")
    if scope_items:
        lines.append(f"  {scope_items},")
    lines.append("]);")
    lines.append(f"const WRITEABLE_ROLES = new Set([{writeable}]);")
    lines.append("")
    lines.append("// normalize_path equivalent: project-relativize a path and FAIL")
    lines.append("// CLOSED on any escape (leading `..`, absolute, or `~` home path)")
    lines.append("// so the lease blocks it regardless of suffix. `.` / `./` collapse;")
    lines.append("// interior `..` resolves and blocks only if it escapes the root.")
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
    lines.append("// Return the first path that violates the active role's lease, else")
    lines.append("// undefined. Fail closed: a write by a role with no scope (read-only")
    lines.append("// or empty allowlist) or an uncompilable scope is blocked.")
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
