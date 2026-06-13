#!/usr/bin/env bash
set -euo pipefail

# System2 installer for Claude Code
# Usage: curl -fsSL https://deliberatecode.github.io/system2/install.sh | bash

RED='\033[0;31m'
GREEN='\033[0;32m'
BOLD='\033[1m'
RESET='\033[0m'

err()  { printf "${RED}error:${RESET} %s\n" "$1" >&2; exit 1; }
info() { printf "${GREEN}=>${RESET} %s\n" "$1"; }

command -v claude >/dev/null 2>&1 || err "Claude Code CLI not found. Install it first: https://code.claude.com/docs/en/setup"

version=$(claude --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)
if [ -n "$version" ]; then
  info "Claude Code v${version} detected"
fi

info "Adding System2 marketplace..."
claude plugin marketplace add DeliberateCode/System2 \
  || err "Failed to add marketplace. Check your network connection and GitHub access."

info "Installing System2 plugin..."
claude plugin install system2@system2-marketplace --scope user \
  || err "Failed to install. Run 'claude plugin marketplace update system2-marketplace' and retry."

printf "\n${GREEN}${BOLD}System2 installed successfully!${RESET}\n"
printf "  Run ${BOLD}claude${RESET} in any project to get started.\n\n"
