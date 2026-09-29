#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/install-common.sh"

TOOL_NAME="Claude Code"
DEFAULT_TARGET_DIR="$HOME/.claude/skills"
PROJECT_SUBDIR=".claude/skills"

run_hydrotune_installer "$@"

