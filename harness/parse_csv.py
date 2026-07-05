"""Tolerant parser for UE5's CSV profiler output (-csvGpuStats).

Do NOT use naive pandas.read_csv here: UE's CSV profiler writes a header row
of stat names, then one row per frame, but real captures routinely have a
different column count on some rows (extra "EVENTS" column appended only on
frames where an event fired, a trailing metadata line, blank lines, etc).
A strict fixed-width reader throws on the first ragged row. This parser
instead zips each row against the header positionally and drops/ignores
anything it can't line up, which is exactly what we want for a benchmark
where we only care about a couple of numeric stat columns.
"""
from __future__ import annotations

import csv
from pathlib import Path


def load_csv_stats(path: str | Path) -> dict[str, list[float]]:
    """Parse a UE CSV profiler file into {stat_name: [values...]}.

    - Header row defines the stat names (column order).
    - Any data row is zipped against the header positionally; extra columns
      are dropped, missing columns are skipped for that row (not padded with
      0, so percentiles aren't skewed).
    - Non-numeric cells (e.g. a stray "EVENTS" column, empty strings) are
      silently dropped for that (row, column) rather than aborting the file.
    """
    path = Path(path)
    rows = list(csv.reader(path.open("r", newline="")))
    if not rows:
        return {}

    header = [h.strip() for h in rows[0]]
    series: dict[str, list[float]] = {name: [] for name in header if name}

    for row in rows[1:]:
        # Skip only fully-empty rows. Real captures have an unnamed first
        # column whose cells are empty on every data row, so testing row[0]
        # alone would silently discard the entire file.
        if not row or not any(cell.strip() for cell in row):
            continue
        for name, cell in zip(header, row):
            if not name:
                continue
            try:
                series[name].append(float(cell))
            except (ValueError, TypeError):
                continue  # ragged/non-numeric cell for this row+column; skip
    return series


def find_stat(series: dict[str, list[float]], *candidates: str) -> list[float]:
    """Look up a stat by exact name, then case-insensitive substring match.

    UE stat names vary a bit by engine version/plugin config (e.g.
    "GPU/Total", "GPUTotal", "FrameTime_GPU"). Callers pass a few likely
    names in priority order; this returns the first series found, or [].
    """
    for name in candidates:
        if name in series:
            return series[name]
    lowered = {k.lower(): v for k, v in series.items()}
    for name in candidates:
        for key, values in lowered.items():
            if name.lower() in key:
                return values
    return []


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile, no numpy/scipy dependency, no interpolation
    surprises. pct in [0, 100]."""
    if not values:
        raise ValueError("no values to compute a percentile over")
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1))))
    return ordered[k]


def frame_time_percentiles(
    csv_path: str | Path, warmup_frames: int = 60
) -> dict[str, float]:
    """Return p50/p95/p99 for GPU frame time (ms), discarding warmup frames.

    UE's CSV frame-time stats are already in milliseconds.
    """
    series = load_csv_stats(csv_path)
    # NB: do not pass a bare "GPU" candidate — substring matching would hit
    # unrelated stats (e.g. "LevelStreaming/NumLevelsPendinGPUrge") and
    # silently report a zero-filled column as frame time.
    gpu_ms = find_stat(series, "GPU/Total", "GPUTotal", "FrameTime_GPU")
    if not gpu_ms:
        # Fall back to overall frame time if GPU-specific stat is absent
        # (e.g. -csvGpuStats wasn't picked up); better a number than a crash.
        gpu_ms = find_stat(series, "FrameTime", "Frametime")
    if not gpu_ms:
        raise ValueError(f"no frame-time stat found in {csv_path}; columns: {list(series)}")

    trimmed = gpu_ms[warmup_frames:] if len(gpu_ms) > warmup_frames else gpu_ms
    if not trimmed:
        raise ValueError(f"no frames left after discarding {warmup_frames} warmup frames")

    return {
        "p50_ms": percentile(trimmed, 50),
        "p95_ms": percentile(trimmed, 95),
        "p99_ms": percentile(trimmed, 99),
        "n_frames": float(len(trimmed)),
    }
