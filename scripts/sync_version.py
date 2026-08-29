#!/usr/bin/env python3
"""Sync version from VERSION to all manifest files.

Usage:
  python3 scripts/sync_version.py           # write version to all manifests
  python3 scripts/sync_version.py --check   # verify manifests match VERSION (CI)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
version = (ROOT / "VERSION").read_text().strip()

MANIFESTS = [
    (ROOT / "plugin" / ".claude-plugin" / "plugin.json", lambda d: d["version"],              lambda d: d.update({"version": version})),
    (ROOT / ".claude-plugin" / "marketplace.json",       lambda d: d["plugins"][0]["version"], lambda d: d["plugins"][0].update({"version": version})),
]

if "--check" in sys.argv:
    errors = []
    for path, get_ver, _ in MANIFESTS:
        actual = get_ver(json.loads(path.read_text()))
        if actual != version:
            errors.append(f"  {path.relative_to(ROOT)}: found {actual!r}, expected {version!r}")
    if errors:
        print(f"Version mismatch (VERSION={version!r}):")
        print("\n".join(errors))
        print("Run 'python3 scripts/sync_version.py' and commit the result.")
        sys.exit(1)
    print(f"OK: all manifests consistent with VERSION ({version}).")
else:
    for path, _, update in MANIFESTS:
        data = json.loads(path.read_text())
        update(data)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"Synced version {version} to all manifests.")
