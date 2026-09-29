#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/install-common.sh"

TOOL_NAME="OpenCode"
DEFAULT_TARGET_DIR="$HOME/.config/opencode/skills"
PROJECT_SUBDIR=".opencode/skills"

run_hydrotune_installer "$@"

