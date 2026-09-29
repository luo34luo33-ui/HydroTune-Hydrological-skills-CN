#!/usr/bin/env bash

# Shared Bash entry point for HydroTune-skills installers.
set -euo pipefail

run_hydrotune_installer() {
    local python_cmd="${HYDROTUNE_PYTHON:-}"
    if [[ -z "$python_cmd" ]]; then
        if command -v python3 >/dev/null 2>&1; then
            python_cmd="python3"
        elif command -v python >/dev/null 2>&1; then
            python_cmd="python"
        else
            echo "Error: Python 3.10+ is required." >&2
            return 127
        fi
    fi

    "$python_cmd" "$SCRIPT_DIR/scripts/install_skills.py" \
        --repo-root "$SCRIPT_DIR" \
        --tool-name "$TOOL_NAME" \
        --default-target "$DEFAULT_TARGET_DIR" \
        --project-subdir "$PROJECT_SUBDIR" \
        "$@"
}

