"""Visual-quality comparison: SSIM gate + a FLIP-like perceptual difference.

No skimage/opencv/NVIDIA-FLIP dependency in this environment (only numpy,
scipy, and Pillow are available) — SSIM is implemented directly (it's ~20
lines), and `flip_diff` is a deliberately-labeled *approximation* of NVIDIA
FLIP's perceptual difference (spatial-frequency-weighted CIELAB delta),
not the reference implementation. If the real `flip_evaluator` package is
ever installed, swap it in behind the same `flip_diff(a, b) -> float in
[0, 1]` signature; nothing else in evaluate.py needs to change.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter


def load_image(path: str | Path) -> np.ndarray:
    """Load an image as float32 RGB in [0, 1]."""
    img = Image.open(path).convert("RGB")
    return np.asarray(img, dtype=np.float32) / 255.0


def _match_size(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if a.shape == b.shape:
        return a, b
    h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
    return a[:h, :w], b[:h, :w]


def _luminance(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


def ssim(a: np.ndarray, b: np.ndarray, sigma: float = 1.5) -> float:
    """Mean structural similarity (Wang et al. 2004), single-scale,
    computed on luminance with a Gaussian window (via gaussian_filter,
    equivalent to but simpler than an explicit sliding window).

    Returns a scalar in roughly [-1, 1]; 1.0 == identical images.
    """
    a, b = _match_size(a, b)
    x, y = _luminance(a), _luminance(b)

    c1, c2 = (0.01 * 1.0) ** 2, (0.03 * 1.0) ** 2

    mu_x = gaussian_filter(x, sigma)
    mu_y = gaussian_filter(y, sigma)
    mu_x2, mu_y2, mu_xy = mu_x * mu_x, mu_y * mu_y, mu_x * mu_y

    sigma_x2 = gaussian_filter(x * x, sigma) - mu_x2
    sigma_y2 = gaussian_filter(y * y, sigma) - mu_y2
    sigma_xy = gaussian_filter(x * y, sigma) - mu_xy

    numerator = (2 * mu_xy + c1) * (2 * sigma_xy + c2)
    denominator = (mu_x2 + mu_y2 + c1) * (sigma_x2 + sigma_y2 + c2)
    ssim_map = numerator / denominator
    return float(ssim_map.mean())


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB [0,1] -> CIELAB, vectorized, no scipy/skimage color module needed."""
    srgb = np.clip(rgb, 0, 1)
    linear = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)

    m = np.array(
        [
            [0.4124564, 0.3575761, 0.1804375],
            [0.2126729, 0.7151522, 0.0721750],
            [0.0193339, 0.1191920, 0.9503041],
        ]
    )
    xyz = linear @ m.T
    white = np.array([0.95047, 1.0, 1.08883])
    xyz = xyz / white

    delta = 6 / 29
    f = np.where(xyz > delta**3, np.cbrt(xyz), xyz / (3 * delta**2) + 4 / 29)

    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def flip_diff(a: np.ndarray, b: np.ndarray, sigma: float = 1.2) -> float:
    """Approximate perceptual difference in [0, 1] (lower = more similar).

    Approximation of NVIDIA FLIP's intent, not its algorithm: convert to
    CIELAB (perceptually-uniform-ish), mildly blur both images to emulate
    the human contrast-sensitivity falloff at high spatial frequency FLIP
    models explicitly, take the per-pixel Euclidean Lab distance, and
    normalize by a fixed max-plausible-delta so the result is roughly
    comparable to FLIP's [0, 1] scale. This is a placeholder good enough to
    make the constrained objective meaningful; replace with the real
    `flip_evaluator` package for a publishable result.
    """
    a, b = _match_size(a, b)
    lab_a = gaussian_filter(_rgb_to_lab(a), (sigma, sigma, 0))
    lab_b = gaussian_filter(_rgb_to_lab(b), (sigma, sigma, 0))

    delta = np.linalg.norm(lab_a - lab_b, axis=-1)
    max_plausible_delta = 100.0  # ~max Lab distance seen between very different natural images
    return float(np.clip(delta.mean() / max_plausible_delta, 0.0, 1.0))


def compare(candidate_path: str | Path, reference_path: str | Path) -> dict[str, float]:
    """Load both images and return {"ssim": ..., "flip": ...}."""
    ref = load_image(reference_path)
    cand = load_image(candidate_path)
    return {"ssim": ssim(cand, ref), "flip": flip_diff(cand, ref)}
