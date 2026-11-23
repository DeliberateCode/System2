#!/usr/bin/env bash
# Verify the documented transitions against the actual extension/hook preview.
set -euo pipefail
: "${PI_PKG_ENTRY:?Set PI_PKG_ENTRY to the pinned Pi package dist/index.js}"
repo=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
preview=cd009dd4f6506eae45767a1becf49a444dddd09f
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
scratch=$(cd "$scratch" && pwd -P)
legacy="$scratch/legacy"
export HOME="$scratch/home"
export PI_CODING_AGENT_DIR="$scratch/pi/agent"
codex_home="$scratch/codex"
mkdir -p "$legacy" "$HOME" "$codex_home" "$scratch/pi"
git -C "$repo" archive "$preview" compiler distributions/codex distributions/pi | tar -x -C "$legacy"

node "$repo/compiler/evals/pi_loader_smoke.mjs" "$PI_PKG_ENTRY" "$repo" "$legacy" "$scratch/pi"

legacy_codex() {
  (cd "$legacy/compiler" && python3 -m system2_compiler.cli codex "$@" --codex-home "$codex_home")
}
original="$scratch/original-hooks.json"
printf '{ "hooks": {} }\n' > "$original"
cp "$original" "$codex_home/hooks.json"
legacy_codex init --force
state="$codex_home/system2/system2-install.json"
test -f "$state"
cp "$codex_home/hooks.json" "$scratch/installed-hooks.json"
legacy_codex uninstall --dry-run
cmp "$scratch/installed-hooks.json" "$codex_home/hooks.json"
test -f "$state"

# A developer who edited the hooks must not lose those edits on uninstall.
printf '\n' >> "$codex_home/hooks.json"
cp "$codex_home/hooks.json" "$scratch/modified-hooks.json"
if legacy_codex uninstall; then
  echo 'Legacy uninstall should refuse modified hooks.json' >&2
  exit 1
fi
cmp "$scratch/modified-hooks.json" "$codex_home/hooks.json"
test -f "$state"

cp "$scratch/installed-hooks.json" "$codex_home/hooks.json"
legacy_codex uninstall
cmp "$original" "$codex_home/hooks.json"
test ! -e "$state"
for hook in system2-edit-guard.js system2-shell-guard.js system2-budget.js; do
  test ! -e "$codex_home/system2/hooks/$hook"
done
printf '%s\n' 'Codex preview cleanup: dry-run was non-mutating, modified hooks were preserved, and the original hooks.json was restored.'
