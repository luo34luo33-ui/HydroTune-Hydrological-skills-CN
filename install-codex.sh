#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/install-common.sh"

TOOL_NAME="Codex"
DEFAULT_TARGET_DIR="$HOME/.agents/skills"
PROJECT_SUBDIR=".agents/skills"

run_hydrotune_installer "$@"

