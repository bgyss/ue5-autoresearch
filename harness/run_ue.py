#!/usr/bin/env python3
"""Launch the UE5 project headless for one deterministic benchmark pass and
produce benchmark.csv + screenshot.png in --out-dir. Called by evaluate.py;
also runnable by hand for M0-style manual checks.

    python harness/run_ue.py --exec-cmds "r.ScreenPercentage=100" --out-dir harness/_last_run

This wraps the editor in -game mode (see docs/03-poc-design.md risk note on
editor vs. packaged builds — start in editor mode, move to a packaged Test
build once packaging on Mac is sorted).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
UE_ROOT = os.environ.get("UE_ROOT", "/Users/Shared/Epic Games/UE_5.7")
EDITOR_CMD = str(Path(UE_ROOT) / "Engine" / "Binaries" / "Mac" / "UnrealEditor-Cmd")
UPROJECT = str(REPO_ROOT / "ue_project" / "UE5AutoResearch.uproject")
MAP = "/Game/Maps/BenchmarkScene"

CSV_CAPTURE_FRAMES = 3000
RESOLUTION = (1920, 1080)
SCREENSHOT_FRAME_HINT = "HighResShot 1920x1080"


def build_exec_cmds(cvars_exec_cmds: str) -> str:
    """cvars_exec_cmds is a comma-separated KEY=VALUE list (already validated
    by evaluate.py). Append the fixed screenshot command; nothing here is
    agent-controlled beyond the cvar values themselves."""
    parts = [p for p in cvars_exec_cmds.split(",") if p]
    parts.append(SCREENSHOT_FRAME_HINT)
    return ",".join(parts)


def run(exec_cmds: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_dir = out_dir / "csv_raw"
    csv_dir.mkdir(exist_ok=True)

    cmd = [
        EDITOR_CMD,
        UPROJECT,
        MAP,
        "-game",
        "-benchmark",
        "-deterministic",
        "-fps=60",
        "-unattended",
        "-nosplash",
        "-noscreenmessages",
        "-novsync",
        f"-csvcaptureframes={CSV_CAPTURE_FRAMES}",
        "-csvGpuStats",
        "-ExitAfterCsvProfiling",
        f"-resx={RESOLUTION[0]}",
        f"-resy={RESOLUTION[1]}",
        "-forceres",
        "-windowed",
        f"-ExecCmds={build_exec_cmds(exec_cmds)}",
        f"-csvoutputdirectory={csv_dir}",
        "-log",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=580)
    log_path = out_dir / "run.log"
    log_path.write_text(result.stdout + "\n" + result.stderr)
    if result.returncode != 0:
        raise RuntimeError(
            f"UnrealEditor-Cmd exited {result.returncode}; see {log_path}"
        )

    _collect_output(csv_dir, out_dir)


def _collect_output(csv_dir: Path, out_dir: Path) -> None:
    """UE writes the CSV and screenshot to its own Saved/ subdirs with
    auto-generated names; find the newest of each and copy them to the
    stable filenames evaluate.py expects."""
    csvs = sorted(csv_dir.rglob("*.csv"), key=lambda p: p.stat().st_mtime)
    if not csvs:
        raise RuntimeError(f"no .csv produced under {csv_dir}")
    shutil.copy(csvs[-1], out_dir / "benchmark.csv")

    saved_screenshots = REPO_ROOT / "ue_project" / "Saved" / "Screenshots"
    shots = sorted(saved_screenshots.rglob("*.png"), key=lambda p: p.stat().st_mtime) \
        if saved_screenshots.exists() else []
    if not shots:
        raise RuntimeError(f"no screenshot .png produced under {saved_screenshots}")
    shutil.copy(shots[-1], out_dir / "screenshot.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exec-cmds", required=True, help="comma-separated KEY=VALUE cvars")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    run(args.exec_cmds, Path(args.out_dir))


if __name__ == "__main__":
    main()
