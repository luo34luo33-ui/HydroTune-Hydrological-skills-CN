#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/install-common.sh"

TOOL_NAME="CodeBuddy"
DEFAULT_TARGET_DIR="$HOME/.codebuddy/skills"
PROJECT_SUBDIR=".codebuddy/skills"

run_hydrotune_installer "$@"
