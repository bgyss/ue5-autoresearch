#!/bin/bash
# One-time headless scene build for M0. Not part of the evaluate loop.
# Runs ue_project/Content/Python/build_scene.py inside the UE5 Editor via -run=pythonscript.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UE_ROOT="${UE_ROOT:-/Users/Shared/Epic Games/UE_5.7}"
EDITOR_CMD="$UE_ROOT/Engine/Binaries/Mac/UnrealEditor-Cmd"
UPROJECT="$REPO_ROOT/ue_project/UE5AutoResearch.uproject"
SCRIPT="$REPO_ROOT/ue_project/Content/Python/build_scene.py"

"$EDITOR_CMD" "$UPROJECT" -run=pythonscript -script="$SCRIPT" -stdout -unattended -nosplash -log
