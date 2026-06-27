"""User-level overlay-profile store and resolution module.

Owns ``~/.system2/profiles.json``: load, structural validation, and atomic save.
Python stdlib only. Must NOT import ``composer.py`` (one-directional
``composer -> profiles`` edge only).
"""

import argparse
import datetime
import json
import os
import re
import sys
import tempfile
from collections import namedtuple
from typing import List, Optional, Set

DEFAULT_STORE_PATH = os.path.join(os.path.expanduser("~"), ".system2", "profiles.json")
SCHEMA_VERSION = "1.0.0"
_PROFILE_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class ProfileError(Exception):
    """Profile operation failure carrying a user-facing message and exit code."""

    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def _validate_store(store: dict) -> None:
    """Structural validation of a loaded store. Raises ProfileError (exit 1)."""
    if not isinstance(store, dict):
        raise ProfileError(
            "~/.system2/profiles.json is malformed: top level is not an object. "
            "Fix or remove the file.",
            exit_code=1,
        )
    profiles = store.get("profiles")
    if not isinstance(profiles, dict):
        raise ProfileError(
            "~/.system2/profiles.json is malformed: 'profiles' is not an object. "
            "Fix or remove the file.",
            exit_code=1,
        )
    for name, entry in profiles.items():
        if not isinstance(name, str) or not _PROFILE_NAME_RE.match(name):
            raise ProfileError(
                "~/.system2/profiles.json is malformed: invalid profile name "
                f"{name!r} (must be kebab-case). Fix or remove the file.",
                exit_code=1,
            )
        if not isinstance(entry, dict) or not isinstance(entry.get("overlays"), list):
            raise ProfileError(
                "~/.system2/profiles.json is malformed: profile "
                f"'{name}' has no 'overlays' list. Fix or remove the file.",
                exit_code=1,
            )
        for index, overlay in enumerate(entry["overlays"]):
            if not isinstance(overlay, dict):
                raise ProfileError(
                    "~/.system2/profiles.json is malformed: profile "
                    f"'{name}' overlay #{index} is not an object. "
                    "Fix or remove the file.",
                    exit_code=1,
                )
            path = overlay.get("path")
            if not isinstance(path, str) or not path:
                raise ProfileError(
                    "~/.system2/profiles.json is malformed: profile "
                    f"'{name}' overlay #{index} has no string 'path'. "
                    "Fix or remove the file.",
                    exit_code=1,
                )


def load_store(store_path: str = DEFAULT_STORE_PATH) -> dict:
    """Load and validate the profile store.

    Missing file returns an empty store. A corrupt or structurally invalid file
    raises ProfileError (exit 1) with no stack trace surfaced to the caller. A
    present-but-unreadable store (read I/O / permission failure) raises
    ProfileError (exit 3) as a single line, never a traceback.
    """
    if not os.path.exists(store_path):
        return {"schema_version": SCHEMA_VERSION, "profiles": {}}
    try:
        with open(store_path, "r", encoding="utf-8") as fh:
            store = json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProfileError(
            f"~/.system2/profiles.json is malformed: {exc}. Fix or remove the file.",
            exit_code=1,
        )
    except OSError as exc:
        raise ProfileError(
            f"Cannot read {store_path}: {exc.strerror or exc}.",
            exit_code=3,
        )
    _validate_store(store)
    return store


def save_store_atomic(store: dict, store_path: str = DEFAULT_STORE_PATH) -> None:
    """Atomically write the store: mkdirs, temp file, os.replace.

    On any pre-replace failure the temp file is unlinked and ProfileError
    (exit 3) is raised, leaving any prior store file intact.
    """
    store_dir = os.path.dirname(store_path)
    tmp_path: Optional[str] = None
    try:
        os.makedirs(store_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=store_dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(store, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, store_path)
    except ProfileError:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    except OSError as exc:
        if tmp_path is not None and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise ProfileError(
            f"Failed to write {store_path}: {exc}",
            exit_code=3,
        )


def normalize_path(raw: str, base_cwd: str) -> str:
    """Expand ``~``, resolve a relative path against ``base_cwd``, and normalize.

    Returns an absolute normalized path. Performs no filesystem access; a path
    may legitimately not yet exist, so existence is a resolution-time concern.
    """
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        expanded = os.path.join(base_cwd, expanded)
    return os.path.normpath(expanded)


def dedup_paths(paths: List[str]) -> List[str]:
    """Order-preserving dedup over already-normalized path strings (first wins)."""
    seen = set()
    result = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


StaleEntry = namedtuple("StaleEntry", ["path", "reason"])
ResolveResult = namedtuple("ResolveResult", ["ordered_paths", "stale", "unknown"])


def _read_overlay_name(path: str) -> Optional[str]:
    """Return the validated overlay name from a path's ``system2.overlay.json``.

    Returns None on any failure: missing directory, missing file, unreadable
    content, malformed JSON, or a ``name`` that is absent or fails
    ``_PROFILE_NAME_RE``. Content is parsed as untrusted data only; it is never
    executed, so no ``eval`` or dynamic import is performed.
    """
    manifest_file = os.path.join(path, "system2.overlay.json")
    try:
        with open(manifest_file, "r", encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    name = manifest.get("name")
    if isinstance(name, str) and _PROFILE_NAME_RE.match(name):
        return name
    return None


def available_profiles(store: dict) -> List[str]:
    """Return the profile names defined in *store*, in definition order."""
    profiles = store.get("profiles")
    if not isinstance(profiles, dict):
        return []
    return list(profiles.keys())


def _unknown_profile_error(name: str, available: List[str]) -> dict:
    """Build the unknown-profile error in both text and JSON forms.

    Returns a dict carrying the exit code, the stderr ``text`` line, and the
    stdout ``json`` payload, reused by the read-only CLI and by composer.py via
    the ``--resolve`` path.
    """
    if available:
        text = (
            f"ERROR: No such profile '{name}'. "
            f"Available profiles: {', '.join(available)}."
        )
    else:
        text = f"ERROR: No such profile '{name}'. No profiles are defined yet."
    return {
        "exit_code": 1,
        "text": text,
        "json": {
            "status": "error",
            "message": f"No such profile '{name}'.",
            "available_profiles": available,
        },
    }


def unknown_profile_message(
    name: str, store_path: str = DEFAULT_STORE_PATH
) -> str:
    """Return the plain unknown-profile message for *name* (no "ERROR: " prefix).

    Loads the store, lists available profiles, and formats the same text as the
    read-only CLI's stderr line, minus the leading "ERROR: " so callers that add
    their own prefix (composer/main via ``_emit_error``) get identical output.
    """
    text = _unknown_profile_error(name, list_profiles(store_path))["text"]
    prefix = "ERROR: "
    return text[len(prefix):] if text.startswith(prefix) else text


def resolve_profile(
    name: str,
    store_path: str = DEFAULT_STORE_PATH,
    stale_policy: str = "hard_fail",
) -> ResolveResult:
    """Resolve a profile name to ordered absolute source paths.

    This is the single replaceable "name -> ordered paths" boundary.
    Unknown name -> ``ResolveResult([], [], unknown=True)``. Otherwise the
    profile's ``overlays[].path`` entries are returned in definition order, and
    any path that is missing or lacks a valid ``system2.overlay.json`` is
    recorded as a ``StaleEntry``. Resolution is pure: ``stale_policy`` is
    plumbed for callers but resolution never raises on stale entries — the
    caller (``_activate_profile``) enforces hard-fail.
    """
    store = load_store(store_path)
    profiles = store.get("profiles", {})
    entry = profiles.get(name)
    if entry is None:
        return ResolveResult([], [], True)

    ordered_paths: List[str] = []
    stale: List[StaleEntry] = []
    for overlay in entry.get("overlays", []):
        if not isinstance(overlay, dict):
            continue
        path = overlay.get("path")
        if not isinstance(path, str):
            continue
        ordered_paths.append(path)
        if not os.path.isdir(path):
            stale.append(StaleEntry(path, "path missing"))
        elif _read_overlay_name(path) is None:
            stale.append(StaleEntry(path, "no valid manifest"))
    return ResolveResult(ordered_paths, stale, False)


def list_profiles(store_path: str = DEFAULT_STORE_PATH) -> List[str]:
    """Return all defined profile names in definition order (empty store -> []).

    Read-only: loads the store but never writes.
    """
    return available_profiles(load_store(store_path))


def inspect_profile(name: str, store_path: str = DEFAULT_STORE_PATH) -> dict:
    """Return a read-only inspection of *name*: one record per source path.

    Unknown name -> ProfileError (exit 1). Otherwise each overlay entry
    yields ``{path, cached_name, cached_version, resolved_name, resolved_version,
    stale, annotation}``. ``resolved_*`` come from the live manifest; a path that
    is missing or lacks a valid manifest is annotated and marked ``stale`` rather
    than failing the whole listing (skip-and-annotate). Advisory ``cached_*``
    fields default to ``None`` when absent. No disk write.
    """
    store = load_store(store_path)
    profiles = store.get("profiles", {})
    entry = profiles.get(name)
    if entry is None:
        raise ProfileError(
            _unknown_profile_error(name, available_profiles(store))["json"][
                "message"
            ],
            exit_code=1,
        )

    overlays = []
    for overlay in entry.get("overlays", []):
        if not isinstance(overlay, dict):
            continue
        path = overlay.get("path")
        if not isinstance(path, str):
            continue
        record = {
            "path": path,
            "cached_name": overlay.get("cached_name"),
            "cached_version": overlay.get("cached_version"),
            "resolved_name": None,
            "resolved_version": None,
            "stale": True,
            "annotation": "unresolvable (path missing / no valid manifest)",
        }
        resolved_name = _read_overlay_name(path)
        if resolved_name is not None:
            record["resolved_name"] = resolved_name
            try:
                with open(
                    os.path.join(path, "system2.overlay.json"),
                    "r",
                    encoding="utf-8",
                ) as fh:
                    version = json.load(fh).get("version")
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                version = None
            record["resolved_version"] = version if isinstance(version, str) else None
            record["stale"] = False
            record["annotation"] = ""
        overlays.append(record)
    return {"name": name, "overlays": overlays}


def _lock_source_paths(project_path: str) -> Set[str]:
    """Return the set of ``source_path`` values in a project's composition lock.

    Reads ``<project_path>/spec/overlay-manifest.lock`` only. A missing or
    unparseable lock yields the empty set, treated as "no lock". Lock content is
    parsed as untrusted data (``json.load`` only); missing or non-list
    ``overlays`` and entries without a string ``source_path`` are tolerated. This
    never writes the lock and never reads the project's installed-overlays
    manifest (independence).
    """
    lock_file = os.path.join(project_path, "spec", "overlay-manifest.lock")
    try:
        with open(lock_file, "r", encoding="utf-8") as fh:
            lock = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set()
    if not isinstance(lock, dict):
        return set()
    sources: Set[str] = set()
    for overlay in lock.get("overlays", []) or []:
        if not isinstance(overlay, dict):
            continue
        source = overlay.get("source_path")
        if isinstance(source, str):
            sources.add(source)
    return sources


def active_profile_for_lock(
    project_path: str,
    name: str,
    store_path: str = DEFAULT_STORE_PATH,
) -> bool:
    """Return True iff *name* is the active profile for the project's lock.

    "Active" means order-insensitive **set equality** between the profile's
    resolved source paths and the lock's ``source_path`` values. An unknown
    profile, an absent/unparseable lock, or an empty lock set all yield False
    (no-lock -> False). A profile with any stale path also yields False: it
    cannot activate (activation hard-fails on stale paths), so it is not
    meaningfully "active". The lock is read-only and the per-project
    installed-overlays config is never accessed.
    """
    resolved = resolve_profile(name, store_path)
    if resolved.unknown:
        return False
    if resolved.stale:
        return False
    lock_sources = _lock_source_paths(project_path)
    if not lock_sources:
        return False
    return set(resolved.ordered_paths) == lock_sources


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 ``...Z`` timestamp."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _read_overlay_version(path: str) -> Optional[str]:
    """Return the live overlay ``version`` string at *path*, or None.

    Reads the same untrusted ``system2.overlay.json`` as ``_read_overlay_name``;
    returns None on any failure or non-string version (never executes content).
    """
    try:
        with open(
            os.path.join(path, "system2.overlay.json"), "r", encoding="utf-8"
        ) as fh:
            manifest = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict):
        return None
    version = manifest.get("version")
    return version if isinstance(version, str) else None


def _overlay_entry(
    path: str,
    cached_name: Optional[str] = None,
    cached_version: Optional[str] = None,
) -> dict:
    """Build a stored overlay entry with an advisory name/version cache.

    The live manifest at *path* is authoritative; ``cached_name`` /
    ``cached_version`` are used only as a fallback when the live manifest is
    unreadable (e.g. capturing from a lock entry whose source path is stale).
    Either field is stored as ``None`` when unresolvable.
    """
    name = _read_overlay_name(path)
    if name is None:
        name = cached_name
    version = _read_overlay_version(path)
    if version is None:
        version = cached_version
    return {"path": path, "cached_name": name, "cached_version": version}


def _summary(name: str, entry: dict, store_path: str) -> dict:
    """Build the mutation summary for a stored profile *entry*.

    Lists each overlay path with its resolved name (live manifest preferred,
    advisory cache as fallback) and the storage location.
    """
    overlays = []
    for overlay in entry.get("overlays", []):
        if not isinstance(overlay, dict):
            continue
        path = overlay.get("path")
        resolved = _read_overlay_name(path) if isinstance(path, str) else None
        overlays.append(
            {"path": path, "name": resolved or overlay.get("cached_name")}
        )
    return {"name": name, "overlays": overlays, "store_path": store_path}


def create_profile(
    name: str,
    raw_paths: List[str],
    base_cwd: str,
    force: bool = False,
    store_path: str = DEFAULT_STORE_PATH,
) -> dict:
    """Create a new profile from *raw_paths*. Composes nothing.

    Validates *name* against ``_PROFILE_NAME_RE``; zero paths is a usage error.
    Paths are normalized against *base_cwd* and order-preservingly deduped. If
    *name* already exists and *force* is false the operation refuses to
    overwrite. Advisory name/version are captured per path from the live
    manifest. The store is written atomically (created on first mutation).
    """
    if not isinstance(name, str) or not _PROFILE_NAME_RE.match(name):
        raise ProfileError(
            f"Invalid profile name '{name}': must be kebab-case "
            "([a-z0-9] segments joined by '-').",
            exit_code=1,
        )
    if not raw_paths:
        raise ProfileError(
            "Cannot create a profile with no overlay paths. Supply at least one path.",
            exit_code=1,
        )

    store = load_store(store_path)
    if name in store.get("profiles", {}) and not force:
        raise ProfileError(
            f"Profile '{name}' already exists. Re-run with force to overwrite.",
            exit_code=1,
        )

    paths = dedup_paths([normalize_path(p, base_cwd) for p in raw_paths])
    now = _now_iso()
    entry = {
        "overlays": [_overlay_entry(p) for p in paths],
        "created_at": now,
        "updated_at": now,
    }
    store.setdefault("profiles", {})[name] = entry
    save_store_atomic(store, store_path)
    return _summary(name, entry, store_path)


def edit_profile(
    name: str,
    adds: List[str],
    removes: List[str],
    base_cwd: str,
    store_path: str = DEFAULT_STORE_PATH,
) -> dict:
    """Add and/or remove overlays on an existing profile in one call.

    Unknown profile -> ``ProfileError``. *adds* are normalized against
    *base_cwd* and appended if not already present, then the overlay set is
    deduped. *removes* match by overlay NAME, preferring the live manifest
    (authoritative) and falling back to the advisory cached name. A requested
    remove-name absent from the profile -> ``ProfileError`` "not found in
    profile" (not silent). If a stale path blocks confirming a requested name,
    ``ProfileError`` names the offending path. The store is written atomically;
    ``updated_at`` is refreshed.
    """
    store = load_store(store_path)
    entry = store.get("profiles", {}).get(name)
    if entry is None:
        raise ProfileError(
            _unknown_profile_error(name, available_profiles(store))["json"][
                "message"
            ],
            exit_code=1,
        )

    overlays = [dict(o) for o in entry.get("overlays", []) if isinstance(o, dict)]

    if removes:
        # Effective name per entry: live manifest authoritative, cached fallback.
        # An entry whose name cannot be determined (stale path with no cached
        # name) is an unresolvable blocker.
        effective_names = []
        blockers = []
        for overlay in overlays:
            path = overlay.get("path")
            eff = _read_overlay_name(path) if isinstance(path, str) else None
            if eff is None:
                eff = overlay.get("cached_name")
            effective_names.append(eff)
            if eff is None and isinstance(path, str):
                blockers.append(path)

        remove_set = set(removes)
        for target in removes:
            if target not in effective_names:
                if blockers:
                    raise ProfileError(
                        f"Cannot confirm overlay '{target}' for removal: "
                        f"stale path blocks name resolution: {blockers[0]}. "
                        "Fix the path or remove valid entries by name.",
                        exit_code=1,
                    )
                raise ProfileError(
                    f"Overlay '{target}' not found in profile '{name}'.",
                    exit_code=1,
                )
        overlays = [
            overlay
            for overlay, eff in zip(overlays, effective_names)
            if eff not in remove_set
        ]

    existing_paths = {o.get("path") for o in overlays}
    for raw in adds:
        norm = normalize_path(raw, base_cwd)
        if norm not in existing_paths:
            overlays.append(_overlay_entry(norm))
            existing_paths.add(norm)

    # Order-preserving dedup by path.
    seen = set()
    deduped = []
    for overlay in overlays:
        path = overlay.get("path")
        if path not in seen:
            seen.add(path)
            deduped.append(overlay)
    if not deduped:
        raise ProfileError(
            f"Edit would leave profile '{name}' with no overlays; a profile "
            "must keep at least one (use delete to remove the profile).",
            exit_code=1,
        )
    entry["overlays"] = deduped
    entry["updated_at"] = _now_iso()
    save_store_atomic(store, store_path)
    return _summary(name, entry, store_path)


def delete_profile(
    name: str,
    store_path: str = DEFAULT_STORE_PATH,
) -> dict:
    """Delete a profile entry and atomically save. Touches no project artifact.

    Unknown profile -> ``ProfileError``. Only ``profiles.json`` is modified.
    """
    store = load_store(store_path)
    profiles = store.get("profiles", {})
    entry = profiles.get(name)
    if entry is None:
        raise ProfileError(
            _unknown_profile_error(name, available_profiles(store))["json"][
                "message"
            ],
            exit_code=1,
        )
    summary = _summary(name, entry, store_path)
    del profiles[name]
    save_store_atomic(store, store_path)
    return summary


def save_profile_from_lock(
    name: str,
    project_path: str,
    force: bool = False,
    store_path: str = DEFAULT_STORE_PATH,
) -> dict:
    """Capture a project's current composition (from its lock) as a profile.

    Reads ``<project_path>/spec/overlay-manifest.lock`` ``source_path`` values in
    lock order; a missing/unparseable/empty lock -> ``ProfileError`` "no current
    composition to capture". Relative ``source_path`` values resolve against the
    current working directory (matching ``--from-lock``, which consumes them
    verbatim and resolves relatives cwd-relative); absolute paths (the normal
    case) are stored unchanged. Paths are deduped. Overwrite of an existing name
    requires *force*. Advisory name/version are captured from the lock entries
    (live manifest preferred at store time). Never reads or writes the
    project's installed-overlays manifest.
    """
    if not isinstance(name, str) or not _PROFILE_NAME_RE.match(name):
        raise ProfileError(
            f"Invalid profile name '{name}': must be kebab-case "
            "([a-z0-9] segments joined by '-').",
            exit_code=1,
        )

    lock_file = os.path.join(project_path, "spec", "overlay-manifest.lock")
    try:
        with open(lock_file, "r", encoding="utf-8") as fh:
            lock = json.load(fh)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        lock = None
    lock_overlays = lock.get("overlays") if isinstance(lock, dict) else None
    if not isinstance(lock_overlays, list) or not lock_overlays:
        raise ProfileError(
            "No current composition to capture: no readable overlay lock at "
            f"{lock_file}. Compose first, then save.",
            exit_code=1,
        )

    cached = {}
    ordered_raw = []
    for overlay in lock_overlays:
        if not isinstance(overlay, dict):
            continue
        source = overlay.get("source_path")
        if not isinstance(source, str) or not source:
            continue
        ordered_raw.append(source)
        ov_name = overlay.get("name")
        ov_version = overlay.get("version")
        cached[source] = (
            ov_name if isinstance(ov_name, str) else None,
            ov_version if isinstance(ov_version, str) else None,
        )
    if not ordered_raw:
        raise ProfileError(
            "No current composition to capture: the overlay lock at "
            f"{lock_file} lists no source paths. Compose first, then save.",
            exit_code=1,
        )

    cwd = os.getcwd()
    paths = dedup_paths([normalize_path(p, cwd) for p in ordered_raw])

    store = load_store(store_path)
    if name in store.get("profiles", {}) and not force:
        raise ProfileError(
            f"Profile '{name}' already exists. Re-run with force to overwrite.",
            exit_code=1,
        )

    now = _now_iso()
    raw_by_norm = {}
    for raw in ordered_raw:
        raw_by_norm.setdefault(normalize_path(raw, cwd), raw)
    overlays = []
    for norm in paths:
        c_name, c_version = cached.get(raw_by_norm.get(norm), (None, None))
        overlays.append(_overlay_entry(norm, c_name, c_version))
    entry = {"overlays": overlays, "created_at": now, "updated_at": now}
    store.setdefault("profiles", {})[name] = entry
    save_store_atomic(store, store_path)
    return _summary(name, entry, store_path)


def main() -> None:
    """Read-only CLI entry point: ``--list`` / ``--inspect`` / ``--resolve``.

    There is intentionally NO ``--create``/``--edit``/``--delete``/``--save``
    verb: this CLI is structurally read-only. Exit 0 on success,
    the carried ``ProfileError.exit_code`` (1 unknown/corrupt, 3 read I/O) on
    failure; a ``ProfileError`` is printed to stderr as a single line and never
    surfaces a Python stack trace.
    """
    parser = argparse.ArgumentParser(
        prog="profiles.py",
        description="Read-only overlay-profile inspection (no mutation verbs).",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List profile names")
    group.add_argument("--inspect", metavar="NAME", help="Inspect one profile")
    group.add_argument(
        "--resolve",
        metavar="NAME",
        help="Print ordered paths + stale list as JSON (composer.py helper)",
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text", help="Output format"
    )
    args = parser.parse_args()

    try:
        if args.list:
            names = list_profiles()
            if args.format == "json":
                print(json.dumps({"profiles": names}))
            elif names:
                print("\n".join(names))
            else:
                print("No profiles are defined yet.")
        elif args.inspect is not None:
            result = inspect_profile(args.inspect)
            if args.format == "json":
                print(json.dumps(result, indent=2))
            else:
                print(f"Profile '{result['name']}':")
                for record in result["overlays"]:
                    if record["stale"]:
                        print(f"  {record['path']} -- {record['annotation']}")
                    else:
                        print(
                            f"  {record['path']} -- {record['resolved_name']} "
                            f"v{record['resolved_version']}"
                        )
        else:
            result = resolve_profile(args.resolve)
            if result.unknown:
                err = _unknown_profile_error(
                    args.resolve, list_profiles()
                )
                raise ProfileError(err["json"]["message"], exit_code=1)
            print(
                json.dumps(
                    {
                        "ordered_paths": result.ordered_paths,
                        "stale": [
                            {"path": s.path, "reason": s.reason}
                            for s in result.stale
                        ],
                    }
                )
            )
    except ProfileError as exc:
        if args.format == "json":
            print(json.dumps({"status": "error", "message": exc.message}))
        else:
            print(exc.message, file=sys.stderr)
        sys.exit(exc.exit_code)


if __name__ == "__main__":
    main()
