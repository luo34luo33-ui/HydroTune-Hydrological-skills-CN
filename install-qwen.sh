#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/install-common.sh"

TOOL_NAME="Qwen Code"
DEFAULT_TARGET_DIR="$HOME/.qwen/skills"
PROJECT_SUBDIR=".qwen/skills"

run_hydrotune_installer "$@"
