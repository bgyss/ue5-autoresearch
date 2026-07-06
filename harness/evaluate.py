#!/usr/bin/env python3
"""Fixed evaluator. Read-only to the loop — the proposer must never edit this
file (or anything else under harness/); it may only edit config/candidate.cvars.

    python harness/evaluate.py [--cvars config/candidate.cvars]

Pipeline (docs/03-poc-design.md, "The evaluator"):
  1. Load + validate candidate.cvars against harness/allowed_cvars.txt.
  2. Run the deterministic benchmark (harness/run_ue.py) -> CSV + screenshot.
  3. Parse frame-time percentiles from the CSV (parse_csv.py).
  4. Compare the screenshot to reference/ref_pose01.png (quality.py).
  5. Combine into one constrained fitness score; reject on quality-floor breach.
  6. Append a row to results.tsv.

Constrained objective (never redefine this from outside — see program.md):
    if ssim < SSIM_FLOOR or flip > FLIP_CEIL:
        fitness = -inf   # quality contract broken -> reject, no matter how fast
    else:
        fitness = -p95_ms - LAMBDA * flip
"""
from __future__ import annotations

import argparse
import datetime
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mock_ue  # noqa: E402
import parse_csv  # noqa: E402
import quality  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
ALLOWED_CVARS_PATH = REPO_ROOT / "harness" / "allowed_cvars.txt"
# M4: three reference poses (start/middle/end of the flythrough); quality is
# gated on the WORST pose so a config can't overfit a single easy viewpoint.
REFERENCE_IMAGES = tuple(
    REPO_ROOT / "reference" / f"ref_pose{i:02d}.png" for i in (1, 2, 3)
)
RESULTS_TSV = REPO_ROOT / "results.tsv"

# Quality floor. Tune these on the reference scene before turning the loop
# loose unattended (docs/03-poc-design.md, Step 5).
SSIM_FLOOR = 0.97
FLIP_CEIL = 0.10
LAMBDA = 50.0  # weight on flip as a tiebreaker among passing configs
WARMUP_FRAMES = 60


class InvalidCvarsError(ValueError):
    pass


def load_allowed_cvars(path: Path = ALLOWED_CVARS_PATH) -> set[str]:
    allowed = set()
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            allowed.add(line)
    return allowed


def parse_cvars_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE cvars file. Blank lines and #-comments are ignored."""
    cvars: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise InvalidCvarsError(f"{path}:{lineno}: not a KEY=VALUE line: {raw!r}")
        key, _, value = line.partition("=")
        cvars[key.strip()] = value.strip()
    return cvars


def validate_cvars(cvars: dict[str, str], allowed: set[str]) -> None:
    """Reject any key not on the allow-list. This is the anti-reward-hacking
    guardrail: a hallucinated or out-of-scope cvar must not silently pass
    through to the benchmark."""
    unknown = sorted(set(cvars) - allowed)
    if unknown:
        raise InvalidCvarsError(
            f"candidate.cvars sets cvar(s) not on the allow-list "
            f"({ALLOWED_CVARS_PATH.name}): {unknown}"
        )


def cvars_to_exec_cmds(cvars: dict[str, str]) -> str:
    """Render validated cvars as a single -ExecCmds string, plus the fixed
    screenshot command run_ue.py's caller appends at a pinned frame.

    NOTE: UE's exec path treats "key=value" as one unknown token and silently
    ignores it; console cvar sets must be space-separated ("key value").
    Verified in harness/_last_run/ue.log: all "=" commands no-op'd while the
    space-form commands (HighResShot, CsvProfile) in the same string ran."""
    return ",".join(f"{k} {v}" for k, v in cvars.items())


def run_benchmark(exec_cmds: str, out_dir: Path) -> tuple[Path, Path]:
    """Invoke harness/run_ue.py, which launches the benchmark headless and
    returns the paths to the produced CSV and screenshot."""
    run_ue = REPO_ROOT / "harness" / "run_ue.py"
    result = subprocess.run(
        [
            sys.executable,
            str(run_ue),
            "--exec-cmds",
            exec_cmds,
            "--out-dir",
            str(out_dir),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"run_ue.py failed (exit {result.returncode}):\n"
            f"stdout:\n{result.stdout[-4000:]}\nstderr:\n{result.stderr[-4000:]}"
        )
    csv_path = out_dir / "benchmark.csv"
    if not csv_path.exists():
        raise RuntimeError(f"run_ue.py did not produce {csv_path}")
    shots = [out_dir / f"screenshot_pose{i:02d}.png"
             for i in range(1, len(REFERENCE_IMAGES) + 1)]
    for shot in shots:
        if not shot.exists():
            raise RuntimeError(f"run_ue.py did not produce {shot}")
    return csv_path, shots


def worst_pose_quality(screenshot_paths: list[Path]) -> dict:
    """Compare each captured pose against its reference and gate on the
    WORST pose: min ssim, max flip. Per-pose values are kept for logging so
    a regression on one viewpoint is visible in results.tsv."""
    per_pose = [
        quality.compare(shot, ref)
        for shot, ref in zip(screenshot_paths, REFERENCE_IMAGES, strict=True)
    ]
    stats = {
        "ssim": min(q["ssim"] for q in per_pose),
        "flip": max(q["flip"] for q in per_pose),
    }
    for i, q in enumerate(per_pose, start=1):
        stats[f"ssim_p{i}"] = q["ssim"]
        stats[f"flip_p{i}"] = q["flip"]
    return stats


def compute_fitness(p95_ms: float, ssim_score: float, flip_score: float) -> tuple[float, bool]:
    """Returns (fitness, passed). passed=False -> fitness is -inf."""
    if ssim_score < SSIM_FLOOR or flip_score > FLIP_CEIL:
        return float("-inf"), False
    return -p95_ms - LAMBDA * flip_score, True


def append_result_row(row: dict) -> None:
    header = [
        "timestamp",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "ssim",
        "flip",
        "fitness",
        "passed",
        *parse_csv.PASS_BUCKETS,
        *(f"ssim_p{i}" for i in range(1, len(REFERENCE_IMAGES) + 1)),
        *(f"flip_p{i}" for i in range(1, len(REFERENCE_IMAGES) + 1)),
    ]
    out = results_path()
    # M4 widened the schema; if an existing file has a different header,
    # rotate it aside rather than appending misaligned columns.
    if out.exists():
        with out.open() as f:
            existing_header = f.readline().rstrip("\n").split("\t")
        if existing_header != header:
            ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            out.rename(out.with_suffix(f".pre-m4-{ts}.tsv"))
    is_new = not out.exists()
    with out.open("a") as f:
        if is_new:
            f.write("\t".join(header) + "\n")
        f.write("\t".join(str(row[k]) for k in header) + "\n")


def results_path() -> Path:
    """Mock runs log to results.mock.tsv so demo output never contaminates
    the real benchmark history in results.tsv."""
    return REPO_ROOT / ("results.mock.tsv" if mock_enabled() else "results.tsv")


def mock_enabled() -> bool:
    """Demo mode: simulate the benchmark without UE5 (see harness/mock_ue.py).
    Enabled via UE5AR_MOCK=1 so it propagates through subprocesses
    (propose.py -> evaluate.py -> here) without extra plumbing."""
    return os.environ.get("UE5AR_MOCK", "") not in ("", "0")


def evaluate(cvars_path: Path) -> dict:
    allowed = load_allowed_cvars()
    cvars = parse_cvars_file(cvars_path)
    validate_cvars(cvars, allowed)

    if mock_enabled():
        frame_stats, quality_stats = mock_ue.simulate(cvars)
        pass_stats = {k: frame_stats[k] for k in parse_csv.PASS_BUCKETS}
    else:
        exec_cmds = cvars_to_exec_cmds(cvars)
        out_dir = REPO_ROOT / "harness" / "_last_run"
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path, screenshot_paths = run_benchmark(exec_cmds, out_dir)
        frame_stats = parse_csv.frame_time_percentiles(
            csv_path, warmup_frames=WARMUP_FRAMES
        )
        quality_stats = worst_pose_quality(screenshot_paths)
        pass_stats = parse_csv.per_pass_percentiles(
            csv_path, warmup_frames=WARMUP_FRAMES
        )

    fitness, passed = compute_fitness(
        frame_stats["p95_ms"], quality_stats["ssim"], quality_stats["flip"]
    )

    row = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "p50_ms": round(frame_stats["p50_ms"], 3),
        "p95_ms": round(frame_stats["p95_ms"], 3),
        "p99_ms": round(frame_stats["p99_ms"], 3),
        "ssim": round(quality_stats["ssim"], 5),
        "flip": round(quality_stats["flip"], 5),
        "fitness": fitness if fitness != float("-inf") else "-inf",
        "passed": passed,
    }
    for k in parse_csv.PASS_BUCKETS:
        row[k] = round(pass_stats[k], 3)
    for i in range(1, len(REFERENCE_IMAGES) + 1):
        row[f"ssim_p{i}"] = round(quality_stats.get(f"ssim_p{i}", quality_stats["ssim"]), 5)
        row[f"flip_p{i}"] = round(quality_stats.get(f"flip_p{i}", quality_stats["flip"]), 5)
    append_result_row(row)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cvars",
        default=str(REPO_ROOT / "config" / "candidate.cvars"),
        help="path to the cvars file to evaluate (default: config/candidate.cvars)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="demo mode: simulate the benchmark without launching UE5 "
        "(same as UE5AR_MOCK=1)",
    )
    args = parser.parse_args()
    if args.mock:
        os.environ["UE5AR_MOCK"] = "1"

    try:
        row = evaluate(Path(args.cvars))
    except InvalidCvarsError as e:
        print(f"REJECTED (invalid cvars): {e}", file=sys.stderr)
        sys.exit(1)

    print(
        f"p50={row['p50_ms']}ms p95={row['p95_ms']}ms p99={row['p99_ms']}ms "
        f"ssim={row['ssim']} flip={row['flip']} "
        f"fitness={row['fitness']} passed={row['passed']}"
    )
    sys.exit(0 if row["passed"] else 1)


if __name__ == "__main__":
    main()
