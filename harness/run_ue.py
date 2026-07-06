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
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
UE_ROOT = os.environ.get("UE_ROOT", "/Users/Shared/Epic Games/UE_5.7")
EDITOR_CMD = str(Path(UE_ROOT) / "Engine" / "Binaries" / "Mac" / "UnrealEditor-Cmd")
UPROJECT = str(REPO_ROOT / "ue_project" / "UE5AutoResearch.uproject")
MAP = "/Game/Maps/BenchmarkScene"

CSV_CAPTURE_FRAMES = 3000
RESOLUTION = (1920, 1080)
# HighResShot requested via ExecCmds fires at map-load (frame ~0) and captures
# a black frame before streaming/lighting settle. r.HighResScreenshotDelay
# (UnrealClient.cpp:1551) defers the actual capture by N frames after the
# request, so the shot lands ~frame 300 with the scene fully rendered.
SCREENSHOT_DELAY_FRAMES = 300
SCREENSHOT_FRAME_HINT = "HighResShot 1920x1080"

# M4: 3 reference poses (start/middle/end of the deterministic flythrough),
# expressed as the frame the shot lands on. Verified empirically
# (harness/_m4_shot_test): queuing multiple delayed HighResShot requests in
# ONE ExecCmds string produces ZERO screenshots — the requests clobber each
# other before any fires. Only the single-shot path is reliable, so poses 2
# and 3 come from separate short "pose runs" that exit right after their
# shot. The flythrough is deterministic (-benchmark -deterministic -fps=60),
# so frame index == pose regardless of which run captures it.
POSE_DELAYS = (SCREENSHOT_DELAY_FRAMES, 1500, 2700)
POSE_RUN_EXTRA_FRAMES = 120  # frames past the shot before CsvProfile exits us


def build_exec_cmds(cvars_exec_cmds: str, frames: int,
                    screenshot_delay: int = SCREENSHOT_DELAY_FRAMES) -> str:
    """cvars_exec_cmds is a comma-separated KEY=VALUE list (already validated
    by evaluate.py). Append the fixed screenshot command and the CSV capture
    trigger; nothing here is agent-controlled beyond the cvar values
    themselves.

    CSV capture MUST start via ExecCmds (post-startup), not via the
    -csvcaptureframes command-line flag: on Mac/Metal that flag begins the
    capture before the RHI is initialized and trips
    `Assertion failed: GRHIGlobals.IsRHIInitialized` (RenderUtils.cpp:1789).
    Verified by flag bisection in M0 (harness/_bisect_*.log)."""
    parts = [p for p in cvars_exec_cmds.split(",") if p]
    parts.append(f"r.HighResScreenshotDelay {screenshot_delay}")
    parts.append(SCREENSHOT_FRAME_HINT)
    parts.append(f"CsvProfile Frames={frames}")
    return ",".join(parts)


def run(exec_cmds: str, out_dir: Path, frames: int = CSV_CAPTURE_FRAMES,
        timeout: int = 900, pose_delays: tuple[int, ...] = POSE_DELAYS) -> None:
    """Full benchmark: one CSV run (which also captures pose 1) plus one short
    pose-only run per remaining pose. Produces in out_dir:
        benchmark.csv, screenshot_pose01.png .. screenshot_pose0N.png,
        screenshot.png (= pose 1, kept for backward compatibility).
    """
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Main run: CSV capture + pose 1 screenshot.
    _launch_once(exec_cmds, out_dir, frames, pose_delays[0], timeout,
                 log_stem="", collect_csv=True)
    shutil.copy(out_dir / "screenshot_pose01.png", out_dir / "screenshot.png")

    # Pose runs: same deterministic flythrough, shot at a later frame, exit
    # shortly after. No CSV is kept from these (their capture is too short
    # to be comparable); they exist only for the screenshot.
    for i, delay in enumerate(pose_delays[1:], start=2):
        _launch_once(
            exec_cmds, out_dir, delay + POSE_RUN_EXTRA_FRAMES, delay, timeout,
            log_stem=f"_pose{i:02d}", collect_csv=False, pose_index=i,
        )


def _launch_once(exec_cmds: str, out_dir: Path, frames: int, screenshot_delay: int,
                 timeout: int, log_stem: str, collect_csv: bool,
                 pose_index: int = 1) -> None:
    # ExecCmds-triggered CsvProfile ignores -csvoutputdirectory and always
    # writes to the project's Saved/Profiling/CSV; clear stale files so
    # _collect_output can't pick up a CSV from a previous run. Same for
    # screenshots, which UE also names automatically.
    csv_dir = REPO_ROOT / "ue_project" / "Saved" / "Profiling" / "CSV"
    if csv_dir.exists():
        for stale in csv_dir.glob("*.csv"):
            stale.unlink()
    shots_dir = REPO_ROOT / "ue_project" / "Saved" / "Screenshots"
    if shots_dir.exists():
        for stale in shots_dir.rglob("*.png"):
            stale.unlink()

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
        "-csvGpuStats",
        "-ExitAfterCsvProfiling",
        f"-resx={RESOLUTION[0]}",
        f"-resy={RESOLUTION[1]}",
        "-forceres",
        "-windowed",
        f"-ExecCmds={build_exec_cmds(exec_cmds, frames, screenshot_delay)}",
        "-log",
        # Keep the full UE log alongside the run outputs; stdout only carries
        # the UnrealTraceServer fork chatter, not the engine log.
        f"-abslog={out_dir / ('ue' + log_stem + '.log')}",
        # Disable the UE trace system entirely. Without this, UE auto-spawns
        # a detached "UnrealTraceServer" grandchild process that inherits our
        # stdout/stderr pipe fds. That grandchild keeps the write end of the
        # pipes open even after UnrealEditor-Cmd itself exits, so
        # subprocess.run(..., capture_output=True) below never sees EOF and
        # hangs forever (observed as a zombie UnrealEditor-Cmd + orphaned
        # UnrealTraceServer still holding the pipes). See M0 diagnosis.
        "-trace=",
    ]

    # Write UE output straight to a file instead of PIPEs: even with -trace=,
    # any detached grandchild inheriting a pipe fd would stall
    # communicate()-style reads forever. A plain file has no EOF problem and
    # lets us tail progress while the benchmark runs.
    log_path = out_dir / f"run{log_stem}.log"
    with open(log_path, "w") as log_f:
        try:
            result = subprocess.run(
                cmd, stdout=log_f, stderr=subprocess.STDOUT, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"UnrealEditor-Cmd timed out after {timeout}s; see {log_path}"
            )
    if result.returncode != 0:
        raise RuntimeError(
            f"UnrealEditor-Cmd exited {result.returncode}; see {log_path}"
        )

    _collect_output(csv_dir, out_dir, collect_csv, pose_index)


def _collect_output(csv_dir: Path, out_dir: Path, collect_csv: bool,
                    pose_index: int) -> None:
    """UE writes the CSV and screenshot to its own Saved/ subdirs with
    auto-generated names; find the newest of each and copy them to the
    stable filenames evaluate.py expects."""
    if collect_csv:
        csvs = sorted(csv_dir.rglob("*.csv"), key=lambda p: p.stat().st_mtime)
        if not csvs:
            raise RuntimeError(f"no .csv produced under {csv_dir}")
        shutil.copy(csvs[-1], out_dir / "benchmark.csv")

    saved_screenshots = REPO_ROOT / "ue_project" / "Saved" / "Screenshots"
    shots = sorted(saved_screenshots.rglob("*.png"), key=lambda p: p.stat().st_mtime) \
        if saved_screenshots.exists() else []
    if not shots:
        raise RuntimeError(f"no screenshot .png produced under {saved_screenshots}")
    shutil.copy(shots[-1], out_dir / f"screenshot_pose{pose_index:02d}.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exec-cmds", required=True, help="comma-separated KEY=VALUE cvars")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--frames", type=int, default=CSV_CAPTURE_FRAMES)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    run(args.exec_cmds, Path(args.out_dir), frames=args.frames, timeout=args.timeout)


if __name__ == "__main__":
    main()
