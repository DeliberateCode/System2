"""Shared native-Pi test discovery helpers."""

import os
import shutil
import subprocess

_PI_PKG_REL = os.path.join(
    "@earendil-works", "pi-coding-agent", "dist", "index.js"
)


def resolve_pi_pkg_entry(pi_bin=None):
    """Resolve Pi's ESM entry across npm and custom-prefix installations."""
    override = os.environ.get("PI_PKG_ENTRY")
    if override and os.path.isfile(override):
        return override

    roots = []
    npm = os.environ.get("NPM_BIN") or shutil.which("npm")
    if npm:
        try:
            completed = subprocess.run(
                [npm, "root", "-g"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                roots.append(completed.stdout.strip())
        except (OSError, subprocess.SubprocessError):
            pass

    pi_bin = pi_bin or os.environ.get("PI_BIN") or shutil.which("pi")
    if pi_bin:
        prefix = os.path.dirname(os.path.dirname(os.path.abspath(pi_bin)))
        roots.extend(
            [
                os.path.join(prefix, "lib", "node_modules"),
                os.path.join(prefix, "node_modules"),
            ]
        )

    for root in roots:
        candidate = os.path.join(root, _PI_PKG_REL)
        if os.path.isfile(candidate):
            return candidate
    return None
