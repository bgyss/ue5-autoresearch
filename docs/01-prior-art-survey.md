# 01 — Prior-Art Survey

*What already exists, split into two halves: (A) the autonomous-research/optimization-loop
techniques we'd borrow the *shape* from, and (B) the UE5 profiling/scalability machinery
we'd borrow the *substrate* from.*

---

## A. Autonomous research / self-improvement loops

### A1. Karpathy's `autoresearch` — the direct inspiration

Released early March 2026 as a deliberately tiny open-source repo
([github.com/karpathy/autoresearch](https://github.com/karpathy/autoresearch)). It turns
an ML workflow into "one GPU, one file, one metric." The design is what we're copying:

- **Three files, three trust levels.**
  - `program.md` — human-authored "rules of the game": what the agent may change, how to
    run an experiment, how to parse the metric, keep-vs-discard semantics, and a
    "NEVER STOP" directive. Karpathy calls it a "super-lightweight skill."
  - `train.py` — the *only mutable file* (the "genome"). The agent edits architecture,
    hyperparameters, the training loop — anything here is fair game.
  - `prepare.py` — the **fixed evaluator**, read-only by policy. It defines the metric
    (`evaluate_bpb`) and the data. Pinning it here stops the agent from redefining its own
    score (reward hacking).
- **The loop** (hill-climbing with revert): branch → run baseline → *modify only
  `train.py`* → commit → `uv run train.py` → parse `val_bpb` (lower is better) → append to
  `results.tsv` → if improved, keep the commit; else `git reset` to last good state →
  repeat.
- **Time-boxed experiments.** Each run trains for a fixed ~5 min of *training* wall-clock
  (`TIME_BUDGET = 300`, counted only after warmup so compile time doesn't count). This
  makes runs directly comparable regardless of what changed — but ties results to the
  specific machine.
- **Memory lives in git + logs**, not the agent's context window: `results.tsv` is the
  full trace (including discards), commit history is the chain of kept improvements, the
  final diff is the best config.
- **Results:** ~700 experiments in 2 days on a single GPU from one markdown prompt and
  ~630 lines of code; ~20 real optimizations discovered.

Sources: [repo](https://github.com/karpathy/autoresearch) ·
[Kingy AI mechanics writeup](https://kingy.ai/ai/autoresearch-karpathys-minimal-agent-loop-for-autonomous-llm-experimentation/) ·
[Fortune](https://fortune.com/2026/03/17/andrej-karpathy-loop-autonomous-ai-agents-future/) ·
["Karpathy Loop" explainer](https://www.mindstudio.ai/blog/recursive-self-improvement-karpathy-loop)

**Why it maps cleanly to UE5:** it's model-agnostic ("your Claude/Codex or whatever"),
single-mutable-file, single-scalar-metric, git-for-memory. Swap `train.py`→a cvar config,
`prepare.py`→a benchmark harness, `val_bpb`→a frame-time/quality fitness. Done.

**Two known risks it surfaces (relevant to us):**
- *Indirect prompt injection* — the agent reads `run.log` back into context; a mutated
  file could print instructions. Our logs come from UE5 + our own parser, but the CSV/log
  is still attacker-adjacent text read by an LLM overnight. Keep the agent sandboxed and
  permission-restricted.
- *Evaluator integrity* — the agent must not be able to edit the scorer or it will game it.

### A2. DeepMind **AlphaEvolve** — the heavyweight cousin

A Gemini-powered evolutionary coding agent (May 2025) that evolves *code* (not just
parameters) against an automated evaluator, using an ensemble of LLMs (fast model for
breadth, strong model for depth) and a **MAP-Elites / island-model program database** to
maintain diversity. It produced real wins: data-center scheduling, a Verilog rewrite
shipped into a TPU, faster training kernels, and improvements on open math problems.

The single most important lesson for us, stated by its authors:

> **"The new bottleneck is evaluator design."** Success hinges on a reliable, automatable
> evaluation function.

That is *the* thing to get right for UE5 (see [`03-poc-design.md`](03-poc-design.md)).

Sources: [DeepMind blog](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) ·
[Wikipedia](https://en.wikipedia.org/wiki/AlphaEvolve) ·
[Techopedia overview](https://www.techopedia.com/deepmind-alphaevolve-ai-code-evolution)

### A3. **OpenEvolve** — open-source AlphaEvolve, and it runs local models

[OpenEvolve](https://huggingface.co/blog/codelion/openevolve)
([codelion/openevolve](https://github.com/codelion/openevolve)) reimplements AlphaEvolve:
prompt sampler + LLM ensemble + evaluator pool + program database, async controller. Two
features matter for us:

- **Local-LLM friendly.** It uses the OpenAI SDK, so any OpenAI-compatible endpoint works
  — point `api_base` at **Ollama** or a local inference server. Author notes: low latency
  matters (many generations), and a fast-model-mostly + occasional-strong-model ensemble
  works best. Local inference speed directly caps iteration count.
- **Artifacts side-channel.** Evaluators can hand build errors / profiling results back to
  the LLM as feedback for the next generation — directly analogous to feeding UE5's CSV
  stats back to the model.

Related: [CodeEvolve](https://arxiv.org/pdf/2510.14150) (another open evolutionary coding
agent). **Decision point:** OpenEvolve is a ready-made engine we could drive with a custom
UE5 evaluator instead of writing the loop ourselves — heavier but more capable than the
autoresearch-style hand-rolled loop. See design doc for the recommendation (start
hand-rolled, graduate to OpenEvolve if the search space warrants it).

### A4. Sakana **AI Scientist** (v1/v2) — the full research lifecycle

[AI Scientist](https://sakana.ai/ai-scientist/) /
[v2](https://github.com/sakanaai/ai-scientist-v2) automates idea → literature search →
code → experiments → figures → paper → automated peer review, with a VLM feedback loop on
figures in v2. Model-agnostic (add a model in `llm.py`), but they **caution against models
weaker than GPT-4 level** in the loop, and stress **sandboxed execution** (it runs
LLM-written code). More than we need for a config-tuning toy, but it's the reference for
"how far this pattern goes," and its safety guidance applies. Sakana has since spun up a
dedicated [Recursive Self-Improvement lab](https://sakana.ai/rsi-lab/).

### A5. RL / ML auto-tuning of rendering & GPU settings (the non-LLM lineage)

Before LLMs, this was an RL / search problem, and some of it targets *exactly* our metric:

- **RL GPU-compiler heuristics to raise frame rates.** Off-policy deep RL (Q-learning +
  CI) learns compiler heuristics that "maximize the expected frame rate improvements across
  a set of graphics benchmarks," generalizing across a benchmark suite.
  ([arxiv 2111.12055](https://arxiv.org/pdf/2111.12055))
- **Deep RL for rendering *quality* parameters**, using an image-similarity metric as the
  reward — i.e., pick render settings that maximize similarity to a reference. This is the
  same quality-as-reward idea we'll use as a *constraint*.
- Broader **ML/DL/RL autotuning** of GPU kernels and compiler options is a mature field;
  the recurring finding is that "compiler/engine defaults are not optimal per-scenario,"
  which is precisely why per-scene UE5 tuning has headroom.

**Gap in the literature:** none of these is an *LLM-driven, closed-loop tuner for a
production game engine's user-facing rendering settings, running locally.* That's the niche
this toy would occupy. (Search did not surface an existing "UE5 + LLM autoresearch"
project — this appears to be reasonably novel as a hobby-scale artifact.)

---

## B. The UE5 substrate we'd build on

### B1. Headless / command-line benchmarking (the "experiment runner")

UE5 ships everything needed to run a deterministic, unattended benchmark and dump a CSV —
no custom C++ required. Canonical invocation (from Intel's UE optimization guide, adapted):

```
<Project>-Mac-Test.app -benchmark -deterministic -fps=60 -unattended \
  -noscreenmessages -novsync -csvcaptureframes=12000 -csvGpuStats \
  -ExitAfterCsvProfiling -resx=2560 -resy=1440 -forceres -windowed \
  -ExecCmds="<apply our cvars here>"
```

Key flags:
- `-benchmark -deterministic` — fixed timestep + fixed random seed → repeatable runs.
- `-fps=60` sets the fixed timestep; `-benchmarkseconds`/`-csvcaptureframes` control length
  (note: `-benchmarkseconds` counts *timesteps*, not wall-clock — 211s = 211×60 frames).
- `-unattended -noscreenmessages -novsync` — no prompts, no overlays, no vsync cap.
- `-csvGpuStats` (or console `r.gpuCsvStatsEnabled 1`) adds GPU timings to the CSV.
- `-ExitAfterCsvProfiling` — auto-quit when capture completes (essential for a loop).

In-session equivalents: `csvprofile start` / `csvprofile frames=N` / `csvprofile stop`,
and `t.maxfps 0`, `r.Vsync 0`.

Sources: [Intel: UE optimization / profiling fundamentals](https://www.intel.com/content/www/us/en/developer/articles/technical/unreal-engine-optimization-profiling-fundamentals.html) ·
[Epic: CSV Profiler docs](https://dev.epicgames.com/documentation/en-us/unreal-engine/introduction-to-performance-profiling-and-configuration-in-unreal-engine) ·
[AMD GPUOpen: UE performance guide](https://gpuopen.com/learn/unreal-engine-performance-guide/)

**Measure in milliseconds, not FPS.** Epic and every profiling guide stress this: relative
improvements in FPS are misleading; `FrameTime_ms = 1000 / FPS`. Use **percentiles**
(mean, 95th, 99th) to screen out one-off hitches.

**CSV parsing gotcha:** UE's CSV export has *variable column counts* (~372–376), which makes
naïve `pandas.read_csv` silently drop up to ~90% of rows as "malformed." Either use Epic's
own `PerfReportTool` or a tolerant parser like
[ue-performance-analyzer](https://github.com/vanthunder/ue-performance-analyzer), which
normalizes row lengths line-by-line. **This will bite us if ignored.**

### B2. The optimization knobs (the "genome")

UE5 exposes hundreds of rendering console variables, grouped into **scalability groups**
(`sg.*`, levels 0=Low → 3=Epic → 4=Cinematic) that bundle many `r.*` cvars. The knobs that
matter most (and that work on M1) include:

| Group / cvar | Effect | FPS lever | Quality risk |
|---|---|---|---|
| `r.ScreenPercentage` (50–100) | internal render resolution scale | **huge** | high (blurring) |
| `sg.ShadowQuality`, `r.Shadow.MaxResolution` | dynamic shadow detail | high | medium |
| `sg.GlobalIlluminationQuality` | Lumen GI (software on Mac) | high | medium |
| `sg.ReflectionQuality` | Lumen/SSR reflections | medium | medium |
| `sg.PostProcessQuality`, `r.BloomQuality`, `r.MotionBlurQuality`, `r.DepthOfFieldQuality` | post FX | medium | low–med |
| `sg.EffectsQuality` | particles/translucency | medium | low |
| `sg.FoliageQuality`, `r.ViewDistanceScale` | foliage/draw distance | scene-dependent | medium |
| `sg.TextureQuality`, `r.Streaming.PoolSize` | texture res/streaming | low–med | low |
| `r.AntiAliasingMethod`, `r.TSR.*` | AA method/quality | **notable on Apple Silicon** | med |
| `r.VolumetricFog`, `r.VolumetricCloud` | volumetrics | high when present | high |

Scalability groups can be redefined per-level in `.ini` via `[ShadowQuality@3]`-style
sections, and applied at runtime through `GameUserSettings`, `DefaultEngine.ini`, or
`-ExecCmds`. Note: editor scalability settings do **not** carry into a packaged build —
they must be set at runtime.

Sources: [Epic: Scalability Reference](https://dev.epicgames.com/documentation/en-us/unreal-engine/scalability-reference-for-unreal-engine) ·
[Epic: Lumen Performance Guide](https://dev.epicgames.com/documentation/en-us/unreal-engine/lumen-performance-guide-for-unreal-engine) ·
[Epic: Console Variables Reference](https://dev.epicgames.com/documentation/unreal-engine/unreal-engine-console-variables-reference)

Apple Silicon's costly TSR means `r.AntiAliasingMethod` and `r.TSR.*` are unusually
impactful knobs on M1 — a nice bit of platform-specific search headroom.

### B3. Measuring visual quality (the "don't cheat" half of the metric)

To stop the agent from "optimizing" by turning everything off, fitness must include a
visual-quality term. Capture a screenshot at a **fixed camera pose** (console `HighResShot
1920x1080`) for both a max-quality **reference** and each candidate, then compare:

- **FLIP** (NVIDIA) — purpose-built for rendered-image differences, high spatial precision,
  ships C++/NumPy/PyTorch; lower = better.
  ([NVIDIA FLIP](https://developer.nvidia.com/blog/flip-a-difference-evaluator-for-alternating-images/))
- **SSIM** — cheap, structural; good enough as a first-pass floor.
- **LPIPS** — learned perceptual distance, aligns well with humans (standard in NeRF/3DGS
  work), heavier (needs a small VGG/Alex net).

Recommendation: **SSIM as a cheap gate + FLIP as the perceptual term.** LPIPS optional if
SSIM/FLIP prove too lenient. (Comparison background:
[FLIP vs SSIM vs LPIPS](https://eureka.patsnap.com/article/perceptual-metrics-face-off-lpips-vs-ssim-vs-psnr).)

---

## C. Synthesis — where this leaves us

1. The **loop pattern is solved and open-source** (autoresearch = minimal; OpenEvolve =
   full). We don't need to invent it; we adopt autoresearch's shape.
2. The **UE5 substrate is fully scriptable and headless-capable** — CSV frame-time capture
   and screenshot capture both work from the command line.
3. The **novel, hard, and interesting 20%** is the **evaluator**: a deterministic benchmark
   + an honest fitness that trades frame-time against perceptual quality without being
   gameable. Everything else is plumbing.
4. **M1 is a real constraint** but not a blocker — see [`02-mac-m1-feasibility.md`](02-mac-m1-feasibility.md).
