# 02 — Can this run locally on a Mac (M1)?

**Short answer: yes, with three caveats you must design around.** This doc is the one to
read when deciding whether to greenlight the toy.

The optimization loop is inherently **sequential** — the LLM proposes a config, *then* UE5
benchmarks it, *then* the LLM reads the result. The two heavy workloads (LLM inference and
UE5 rendering) rarely need to peak at the same instant, which is what makes a single Mac
viable.

---

## Caveat 1 — UE5 on M1 has hard feature gaps (design the scene around them)

Native Apple Silicon support has existed since **UE 5.2** (universal binary via the Epic
launcher; the editor auto-selects the arm64 slice). But on **M1 specifically**:

- **Nanite is not supported on M1 and — per Epic's Feb 2025 progress report — will not be.**
  M1 lacks the image-atomic / forward-progress guarantees Nanite needs. (M2+ can enable it
  via the SM6 renderer on macOS 15+.) → **Build the test scene with classic static meshes +
  LODs, not Nanite.**
- **Lumen runs software-ray-tracing only** on all Apple Silicon (no hardware RT on macOS).
  Software Lumen works and is still a meaningful, tunable cost — good for us — just lower
  quality than HW RT.
- **TSR (Temporal Super Resolution) is unusually expensive** on Apple Silicon. Annoying for
  a shipping game; *useful* for us, because it makes `r.AntiAliasingMethod` / `r.TSR.*` a
  high-leverage knob to explore.
- Groom/hair strands unsupported; Quixel Nanite assets fall back to non-Nanite.

**Net:** avoid Nanite; keep software Lumen (or even baked lighting for a v0); the knobs in
[`01-prior-art-survey.md` §B2](01-prior-art-survey.md) still give a rich search space. A
plain Third-Person or a modest Marketplace environment template, de-Nanited, is ideal.

Sources: [UE 5.2 Apple Silicon blog](https://www.unrealengine.com/en-US/tech-blog/unreal-engine-5-2-brings-native-support-for-apple-silicon-and-other-developments-for-macos) ·
[macOS feature-parity progress report (Feb 2025)](https://www.unrealengine.com/tech-blog/bringing-unreal-engine-on-macos-up-to-feature-parity-with-windowsprogress-report)

---

## Caveat 2 — RAM/GPU contention (the base-M1-8GB problem)

Both UE5 and a local LLM want unified memory and the Metal GPU. How tight this is depends
entirely on your Mac's RAM:

| Mac | Local LLM that fits | Realistic loop feel |
|---|---|---|
| **M1, 8 GB** | tiny only (Qwen ~3–4B / Gemma E2B, Q4) | painful — UE5 + any model contends for 8 GB; expect swapping. Use a **remote/API LLM** or a bigger Mac. |
| **M1/M2, 16 GB** | Qwen3 ~8–9B or Gemma E4B (Q4) | OK. Unload the LLM during the UE5 run to hand the GPU back. |
| **M1 Pro/Max or M2/M3+, 24–32 GB** | **Qwen3-Coder 30B-A3B (MoE, ~17 GB Q4)** — the current sweet spot | comfortable. This is the target tier. |
| **32–64 GB** | Qwen3-Coder 30B at Q6/Q8, or dense 27B | ideal. |

Key facts:
- **llama.cpp** (`llama-server`) exposes an OpenAI-compatible endpoint at
  `localhost:8080/v1` — drop-in for autoresearch-style loops or OpenEvolve. Start here,
  or use **llamabarn** if you have a specific local-server setup.
- **MLX** (mlx-community models) is faster on Apple Silicon if you need more tok/s later.
- The proposer doesn't need to be a genius — it's editing a ~30-line cvar list guided by
  numeric feedback, not writing a kernel. A **7–9B coder model is plenty** for v0; the 30B
  MoE just makes suggestions smarter and the loop converge faster.
- **Mitigation for contention:** stop or unload the LLM server before each UE5 benchmark
  so the model releases GPU/RAM, then reload it after. Costs a few seconds of reload per
  iteration — negligible next to a ~1–2 min benchmark.

Sources: [Best local LLMs on Apple Silicon](https://apxml.com/posts/best-local-llms-apple-silicon-mac) ·
[Best coding LLMs, Apple Silicon 24GB](https://willitrunai.com/blog/best-local-coding-llms-apple-silicon-24gb)

**If you're on a base M1/8GB:** the *cleanest* path is a **hybrid** — local UE5 benchmark,
but the *proposer* is a cheap remote model (Claude Haiku / a small API call) or a tiny
local model. You asked to avoid drawing on a Claude plan / the API; that's fully achievable
on a 16 GB+ machine, just not comfortably on 8 GB. This is the single biggest hardware
question — worth confirming which Mac you have before building.

---

## Caveat 3 — Benchmark determinism & wall-clock budget

Karpathy's loop leans on runs being *comparable*. For UE5:

- Use `-benchmark -deterministic` (fixed timestep + seed) and a **scripted camera
  flythrough** (a Level Sequence that auto-plays on `BeginPlay`), so every run traverses an
  identical path. Capture over a fixed frame count with `-csvcaptureframes=N`.
- Expect **run-to-run frame-time noise** on a thermally-throttling laptop. Counter it:
  (a) discard the first N warmup frames, (b) compare **median / 95th-percentile** frame
  time, not mean, (c) optionally require an improvement to clear a **noise threshold**
  (e.g. >2%) before "keep," (d) insert a short cooldown between runs.
- A single benchmark of ~2–4k frames at fixed 60 Hz is ~1–2 min wall-clock plus engine
  startup (~10–30 s cold). Budget **~2–3 min per iteration** end-to-end → ~20–30
  experiments/hour → a few hundred overnight. Comparable to autoresearch's throughput.

---

## Feasibility verdict

| Question | Verdict |
|---|---|
| Can UE5 run + benchmark headless on Apple Silicon? | **Yes** (5.2+). |
| Rich enough tuning landscape on M1 (no Nanite)? | **Yes** — screen %, shadows, software Lumen, post, effects, foliage, AA/TSR. |
| Fully local LLM proposer, no cloud? | **Yes on 16 GB+**, tight on 8 GB (use hybrid there). |
| Deterministic, comparable benchmarks? | **Yes**, with warmup discard + percentile comparison + noise gate. |
| Is the loop itself hard? | **No** — it's ~a few hundred lines. |
| Is the *evaluator* hard? | **Yes** — that's the real work. See [`03-poc-design.md`](03-poc-design.md). |

**Recommendation:** greenlight a v0 on a **16 GB+ Apple Silicon Mac**. If you only have a
base M1/8 GB, either (a) accept a tiny local proposer model, or (b) run the proposer via a
cheap API and keep only the benchmark local — decide based on how strict "no cloud" is.
