# 03 — POC Design

*The proposed toy, mapped file-for-file onto Karpathy's `autoresearch`, with the evaluator
(the actually-hard part) specified in detail.*

---

## Structure — autoresearch, retargeted

| autoresearch | ue5-autoresearch | role / trust level |
|---|---|---|
| `program.md` | `program.md` | human-authored rules of the game (the *in-loop* goal prompt). See [`04-goal-prompt.md`](04-goal-prompt.md). |
| `train.py` (mutable genome) | `config/candidate.cvars` | **the only file the agent edits** — a flat list of `r.*` / `sg.*` = value lines. |
| `prepare.py` (fixed evaluator) | `harness/evaluate.py` | **read-only by policy** — runs the benchmark, computes fitness. The agent must not edit this. |
| `val_bpb` (the one number) | `fitness` (scalar) | frame-time reward with a visual-quality penalty. |
| `results.tsv` | `results.tsv` | full experiment trace incl. discards. |
| git branch hill-climb | git branch hill-climb | keep on improvement, `git reset` on regression. |

Why a **cvar list** instead of editing engine code (as autoresearch edits `train.py`)?
Because it's (a) sufficient for the "optimize graphics settings" goal, (b) far safer to run
unattended (bounded edit surface — no arbitrary code), and (c) reversible instantly. If we
later want the agent to edit richer things (per-level `[ShadowQuality@3]` .ini sections,
device profiles, material quality switches), the same loop extends to those files.

```
ue5-autoresearch/
├── program.md                 # in-loop goal (human-edited)
├── config/
│   ├── candidate.cvars        # THE GENOME — agent edits only this
│   └── baseline.cvars         # frozen Epic/High baseline for reference
├── harness/
│   ├── evaluate.py            # fixed evaluator: run benchmark -> fitness (read-only)
│   ├── run_ue.py              # launches the packaged build / editor -game with our flags
│   ├── parse_csv.py           # tolerant UE CSV parser (handles variable columns)
│   ├── quality.py             # SSIM gate + FLIP perceptual term vs reference screenshot
│   └── propose.py             # calls local LLM (Ollama) to edit candidate.cvars  [v0 driver]
├── reference/
│   └── ref_pose01.png         # max-quality screenshot at the fixed camera pose
├── results.tsv                # experiment log (memory)
└── ue_project/                # the small UE5 test project (de-Nanited scene + auto-fly sequence)
```

Two ways to drive it (both fine):
- **(a) Agent-driven** (closest to autoresearch): point a coding agent at `program.md`; it
  edits `config/candidate.cvars`, runs `harness/evaluate.py`, reads the number, keeps or
  resets, repeats. No `propose.py` needed.
- **(b) Script-driven** (`propose.py`): a ~150-line Python loop that calls an
  OpenAI-compatible local LLM endpoint (e.g. llama.cpp server or llamabarn) for the next
  edit and enforces keep/discard itself. More reproducible; better for "run it 300 times
  overnight" without an agent harness.

Recommend building **(b)** first (deterministic, no agent-harness dependency), then trying
**(a)** for the "point Claude/Codex at it" demo.

---

## The evaluator (`harness/evaluate.py`) — the crux

> AlphaEvolve's authors: *"the new bottleneck is evaluator design."* This section is 80% of
> the real work.

### Step 1 — apply the candidate config
Read `config/candidate.cvars`, pass to UE5 via `-ExecCmds="r.X=1,sg.Y=2,..."` (or write an
`ini` fragment the project loads on startup). Validate each line against an allow-list of
known cvars — reject unknown/garbage lines so a hallucinated cvar can't silently no-op.

### Step 2 — run the deterministic benchmark
Launch a packaged **Test** build (or editor in `-game` mode for a quicker start):
```
<Project>-Mac-Test.app/Contents/MacOS/<Project> \
  -benchmark -deterministic -fps=60 -unattended -noscreenmessages -novsync \
  -csvcaptureframes=3000 -csvGpuStats -ExitAfterCsvProfiling \
  -resx=1920 -resy=1080 -forceres -windowed \
  -ExecCmds="<candidate cvars>; HighResShot 1920x1080"
```
A Level Sequence auto-plays a fixed camera flythrough on `BeginPlay`; `HighResShot` fires at
a pinned frame to produce the comparison screenshot. Engine exits itself.

### Step 3 — parse frame times (tolerantly)
Use `parse_csv.py` (line-by-line, normalize column counts — do **not** trust naïve pandas;
see the [CSV gotcha](01-prior-art-survey.md#b1)). Discard warmup frames. Compute
`p50_ms`, `p95_ms`, `p99_ms` for the **GPU** frame time (and total). Lower is better.

### Step 4 — measure visual quality vs. reference
`quality.py`: load `reference/ref_pose01.png` (captured once at max quality) and the
candidate's screenshot. Compute:
- `ssim` (fast structural gate),
- `flip` (NVIDIA perceptual difference; lower = more similar).

### Step 5 — combine into one honest fitness
The design must make "turn everything off" a *losing* move. Use a **constrained** objective:

```
if ssim < SSIM_FLOOR (e.g. 0.97)  or  flip > FLIP_CEIL:
    fitness = -infinity          # visual quality broke the contract -> reject
else:
    # primary: minimize p95 GPU frame time; secondary: small bonus for staying pretty
    fitness = -p95_ms  -  LAMBDA * flip
```
Equivalently, lexicographic: *"among configs that pass the quality floor, pick the lowest
p95 frame time."* The floor is what prevents reward-hacking; `LAMBDA` gently prefers the
prettier of two similarly-fast configs. Tune `SSIM_FLOOR`/`FLIP_CEIL` on the reference
scene before turning the loop loose.

### Step 6 — log and let the loop decide
Append `timestamp, git_sha, p50_ms, p95_ms, p99_ms, ssim, flip, fitness, passed` to
`results.tsv`. Keep the commit iff `fitness` beat the incumbent by more than the **noise
gate** (e.g. p95 improved >2%); else `git reset --hard` to the last good state.

---

## Objective variants (pick based on taste)

1. **Constrained frame-time (recommended v0):** maximize FPS subject to `ssim ≥ floor`.
   Simple, honest, one number.
2. **Pareto / MAP-Elites (v2, AlphaEvolve-style):** keep an archive of non-dominated
   (frame-time, quality) configs binned by quality level — gives a whole
   performance/quality *curve*, not one point. This is where graduating to
   **OpenEvolve** pays off (it has the program database + islands already).
3. **Target-FPS mode:** "hit 60 fps p95, then maximize quality" — flip primary/secondary.
   Arguably the most *useful* framing for a real game.

---

## What v0 deliberately excludes (keep it a toy)

- No Nanite (M1). No multi-scene generalization — optimize **one** scene first.
- No distributed runs, no LLM ensemble — one local model.
- Cvar list only — no editing C++, materials, or blueprints.
- One camera pose for quality (extend to 3–5 poses once the single-pose version works).

---

## Milestones (suggested build order)

1. **M0 — manual harness.** Package the de-Nanited scene; run the benchmark command by
   hand; get a CSV; parse `p95_ms`; capture a screenshot. *No LLM yet.* Prove the
   substrate is deterministic and the numbers are stable across identical runs.
2. **M1 — fitness.** Add `quality.py` (SSIM+FLIP) and the constrained `fitness`. Hand-edit
   `candidate.cvars` a few times; confirm fitness moves the right way (lower screen % →
   faster but SSIM drops; too low → rejected).
3. **M2 — the loop, scripted.** `propose.py` drives Ollama to edit the cvars; enforce
   keep/discard + git. Run 50 iterations; inspect `results.tsv` for real improvements.
4. **M3 — agent-driven demo.** Point a coding agent at `program.md`; let it run
   unattended. This is the shareable "Karpathy loop for UE5" artifact.
5. **M4 (optional) — Pareto/OpenEvolve** for a quality/perf curve and smarter search.

**Definition of done for the POC:** overnight, from an Epic/High baseline, the loop finds a
config that is *measurably* faster (e.g. ≥15% lower p95 GPU ms) while staying above the
SSIM floor — and `results.tsv` + git history tell the story of how it got there.

---

## Risks / open questions (flag before building)

- **Benchmark noise on a thermally-throttled laptop** may swamp small wins. Mitigation:
  percentiles + noise gate + cooldowns; consider plugging the Mac in and pinning fan/perf.
- **Quality metric gaming.** A single fixed pose can be gamed (agent tunes for one frame).
  Mitigate with multiple poses and by including the *whole* flythrough, not one shot, in a
  later version.
- **Editor `-game` vs packaged build.** Editor mode is faster to iterate but less
  deterministic/representative than a packaged Test build. Start in editor mode for M0–M2 if
  packaging on Mac is fiddly, then move to packaged for the real numbers.
- **8 GB M1** — see [`02-mac-m1-feasibility.md`](02-mac-m1-feasibility.md); may force a
  hybrid proposer.
- **Prompt-injection hygiene** — the LLM reads CSV/log text back; keep the agent sandboxed,
  permissions restricted, and never let it edit `harness/`.
