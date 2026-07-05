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
REFERENCE_IMAGE = REPO_ROOT / "reference" / "ref_pose01.png"
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
    screenshot_path = out_dir / "screenshot.png"
    if not csv_path.exists():
        raise RuntimeError(f"run_ue.py did not produce {csv_path}")
    if not screenshot_path.exists():
        raise RuntimeError(f"run_ue.py did not produce {screenshot_path}")
    return csv_path, screenshot_path


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
    ]
    out = results_path()
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
    else:
        exec_cmds = cvars_to_exec_cmds(cvars)
        out_dir = REPO_ROOT / "harness" / "_last_run"
        out_dir.mkdir(parents=True, exist_ok=True)
        csv_path, screenshot_path = run_benchmark(exec_cmds, out_dir)
        frame_stats = parse_csv.frame_time_percentiles(
            csv_path, warmup_frames=WARMUP_FRAMES
        )
        quality_stats = quality.compare(screenshot_path, REFERENCE_IMAGE)

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
