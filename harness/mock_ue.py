"""Mock benchmark backend for demo mode (no Unreal Engine required).

Activated by ``UE5AR_MOCK=1`` in the environment (or ``evaluate.py --mock``).
Instead of launching UnrealEditor-Cmd, this simulates the whole
benchmark+screenshot pipeline with a simple analytic cost model:

- Each cvar contributes GPU frame-time cost (ms) and perceptual-quality
  degradation relative to the frozen Epic/High baseline.
- Per-frame times are sampled around that cost with config-seeded jitter plus
  a small amount of *unseeded* run-to-run noise, so the >2% noise gate in the
  keep/discard rule is exercised realistically.
- Quality comes back as (ssim, flip) derived from the same degradations, so
  "turn everything off" trips the quality floor and is rejected — exactly the
  failure mode the real evaluator guards against.

The numbers are loosely calibrated to an M1 Mac at 1080p. This is a toy for
demoing the loop end-to-end on any machine; it is NOT a substitute for the
real benchmark.
"""
from __future__ import annotations

import hashlib
import random

# Frame-time model -----------------------------------------------------------

BASE_MS = 3.0  # fixed cost independent of any tunable cvar
RESOLUTION_MS = 8.0  # resolution-bound cost at r.ScreenPercentage=100

# Per-level cost (ms) for scalability groups at level 3 (Epic); scales
# linearly with level and is clamped at level 4 (cinematic ~= 4/3 of Epic).
SG_COST_MS = {
    "sg.ShadowQuality": 2.0,
    "sg.GlobalIlluminationQuality": 2.5,
    "sg.ReflectionQuality": 1.5,
    "sg.PostProcessQuality": 1.0,
    "sg.TextureQuality": 0.3,
    "sg.EffectsQuality": 0.8,
    "sg.FoliageQuality": 0.8,
    "sg.ViewDistanceQuality": 0.6,
    "sg.AntiAliasingQuality": 0.5,
}

# Perceptual damage (fraction of "clearly worse") when a group is dropped
# from Epic (3) to Low (0). Scales linearly with how far below 3 it is.
SG_QUALITY_DAMAGE = {
    "sg.ShadowQuality": 0.015,
    "sg.GlobalIlluminationQuality": 0.020,
    "sg.ReflectionQuality": 0.010,
    "sg.PostProcessQuality": 0.008,
    "sg.TextureQuality": 0.012,
    "sg.EffectsQuality": 0.005,
    "sg.FoliageQuality": 0.006,
    "sg.ViewDistanceQuality": 0.010,
    "sg.AntiAliasingQuality": 0.008,
}

# r.AntiAliasingMethod: 0=None 1=FXAA 2=TAA 3=MSAA 4=TSR
AA_COST_MS = {0: 0.0, 1: 0.15, 2: 0.5, 3: 1.2, 4: 2.2}
AA_DAMAGE = {0: 0.030, 1: 0.018, 2: 0.0, 3: 0.004, 4: 0.0}


def _f(cvars: dict[str, str], key: str, default: float) -> float:
    try:
        return float(cvars.get(key, default))
    except ValueError:
        return default


def _model(cvars: dict[str, str]) -> tuple[float, float]:
    """Return (mean_gpu_ms, damage) for a validated cvars dict."""
    sp = min(200.0, max(10.0, _f(cvars, "r.ScreenPercentage", 100.0)))
    ms = BASE_MS + RESOLUTION_MS * (sp / 100.0) ** 2

    # Below ~85% screen percentage the upscale visibly softens the image.
    damage = max(0.0, (85.0 - sp) / 85.0) ** 1.5 * 0.25

    for key, cost in SG_COST_MS.items():
        level = min(4.0, max(0.0, _f(cvars, key, 3.0)))
        ms += cost * (level / 3.0)
        damage += SG_QUALITY_DAMAGE[key] * max(0.0, (3.0 - level) / 3.0)

    aa = int(_f(cvars, "r.AntiAliasingMethod", 2.0))
    ms += AA_COST_MS.get(aa, 0.5)
    damage += AA_DAMAGE.get(aa, 0.0)

    shadow_res = min(4096.0, max(128.0, _f(cvars, "r.Shadow.MaxResolution", 2048.0)))
    ms += 0.8 * (shadow_res / 2048.0)
    damage += 0.010 * max(0.0, (2048.0 - shadow_res) / 2048.0)

    lumen_gi = min(4.0, max(0.0, _f(cvars, "r.Lumen.GlobalIllumination.Quality", 3.0)))
    lumen_refl = min(4.0, max(0.0, _f(cvars, "r.Lumen.Reflections.Quality", 3.0)))
    ms += 1.2 * (lumen_gi / 3.0) + 0.8 * (lumen_refl / 3.0)
    damage += 0.012 * max(0.0, (3.0 - lumen_gi) / 3.0)
    damage += 0.008 * max(0.0, (3.0 - lumen_refl) / 3.0)

    view_dist = min(2.0, max(0.1, _f(cvars, "r.ViewDistanceScale", 1.0)))
    ms += 0.6 * view_dist
    damage += 0.015 * max(0.0, 1.0 - view_dist)

    tsr_hist = _f(cvars, "r.TSR.History.ScreenPercentage", 100.0)
    if aa == 4 and tsr_hist < 100.0:
        ms -= 0.6 * (100.0 - max(50.0, tsr_hist)) / 50.0
        damage += 0.006 * (100.0 - max(50.0, tsr_hist)) / 50.0

    for key, cost, dmg in (
        ("r.MotionBlurQuality", 0.15, 0.001),
        ("r.DepthOfFieldQuality", 0.25, 0.002),
        ("r.BloomQuality", 0.15, 0.002),
        ("r.Tonemapper.Quality", 0.10, 0.001),
    ):
        level = min(5.0, max(0.0, _f(cvars, key, 4.0)))
        ms += cost * (level / 4.0)
        damage += dmg * max(0.0, (4.0 - level) / 4.0)

    return max(1.0, ms), min(1.0, damage)


def simulate(cvars: dict[str, str], frames: int = 600) -> tuple[dict, dict]:
    """Simulate one benchmark run.

    Returns (frame_stats, quality_stats) shaped exactly like the outputs of
    parse_csv.frame_time_percentiles() and quality.compare().
    """
    mean_ms, damage = _model(cvars)

    # Config-seeded jitter (deterministic per config) + run-to-run noise.
    seed = int.from_bytes(
        hashlib.sha256(repr(sorted(cvars.items())).encode()).digest()[:8], "big"
    )
    rng = random.Random(seed)
    run_noise = random.Random()  # intentionally unseeded: real benches are noisy
    drift = 1.0 + run_noise.gauss(0.0, 0.008)  # ~0.8% run-to-run variance

    samples = []
    for _ in range(frames):
        frame = mean_ms * drift * (1.0 + rng.gauss(0.0, 0.06))
        if rng.random() < 0.02:  # occasional hitchy frame
            frame *= 1.0 + rng.random() * 0.8
        samples.append(max(0.5, frame))
    samples.sort()

    def pct(p: float) -> float:
        k = max(0, min(len(samples) - 1, round(p / 100 * (len(samples) - 1))))
        return samples[k]

    frame_stats = {
        "p50_ms": pct(50),
        "p95_ms": pct(95),
        "p99_ms": pct(99),
        "n_frames": float(frames),
    }
    quality_stats = {
        "ssim": max(0.0, 1.0 - damage),
        "flip": min(1.0, damage * 2.5),
    }
    return frame_stats, quality_stats
