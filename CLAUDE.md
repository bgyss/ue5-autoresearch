# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

The UE5 project is under `ue_project/`, the fixed evaluator lives in `harness/`
(read-only to the loop), and the mutable "genome" is `config/candidate.cvars`.
`results.tsv` is the append-only experiment log and is deliberately **untracked** by git.
`program.md` is the in-loop goal.

Run the loop:

- **Script-driven:** `python harness/propose.py --iterations 50` (uses an
  OpenAI-compatible local LLM server such as `llama-server` on `localhost:8080/v1` or
  Ollama on `localhost:11434/v1`).
- **Agent-driven:** point Claude Code at `program.md` and have it edit only
  `config/candidate.cvars`, run `python harness/evaluate.py`, keep/discard per
  `program.md`, and repeat.
- **Demo mode (no UE5):** set `UE5AR_MOCK=1` (or pass `--mock`) to simulate the
  benchmark via `harness/mock_ue.py`.

Do not invent new build/test commands beyond what's already in the workspace; the main
verification commands are `python harness/evaluate.py` and `python harness/propose.py`.

## Version control: jujutsu, not git

This repo is driven with **jujutsu (`jj`)**, colocated with git (`.jj/` and `.git/` both
present). Use `jj`, not raw `git`:
- `jj status` — working-copy changes. The working copy is *already a change*; there is no
  staging step.
- `jj describe -m "..."` — set the commit message on the current change (this is how you
  "commit").
- `jj new` — start a fresh working-copy change on top of the current one.
- `jj log` — history.

End commit descriptions with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## What this project is

Applies **Karpathy's [`autoresearch`](https://github.com/karpathy/autoresearch) loop** to
**Unreal Engine 5 rendering optimization**. An LLM proposes rendering console-variable
configs; a fixed evaluator benchmarks each one deterministically and scores it on frame time
*and* visual quality; the loop keeps configs that are faster without looking worse — git
hill-climbing, forever.

The design maps file-for-file onto autoresearch (see `docs/03-poc-design.md`):

| autoresearch | this project | trust level |
|---|---|---|
| `program.md` (human-edited rules) | `program.md` | human-authored goal |
| `train.py` (mutable "genome") | `config/candidate.cvars` | **the only file the loop edits** |
| `prepare.py` (fixed evaluator) | `harness/evaluate.py` | **read-only to the loop** (no reward hacking) |
| `val_bpb` (one number) | `fitness` = frame-time reward + quality penalty | — |
| git branch hill-climb | same | keep on improvement, `jj`/`git` revert on regression |

## Two invariants that must survive implementation

These are the load-bearing design decisions — preserve them in any code you write:

1. **The evaluator is the hard part and must not be gameable.** The proposer may edit *only*
   `config/candidate.cvars`; `harness/` (which defines the score) is off-limits to the loop.
   Fitness is *constrained*: a config is rejected outright if visual quality drops below a
   floor (SSIM gate + NVIDIA FLIP), so "turn everything off" is a losing move, never a win.
   See `docs/03-poc-design.md` §"The evaluator".

2. **Measure frame time in milliseconds, percentiles (p50/p95/p99) — never raw FPS
   averages.** Benchmarks must be deterministic (`-benchmark -deterministic`, scripted
   camera flythrough) with warmup frames discarded and a noise gate (e.g. require >2% p95
   improvement before "keep"), because a thermally-throttled laptop is noisy.

## Platform constraints (Apple Silicon Mac)

The target is a local Mac. These shape the design — don't design around features that don't
exist here:
- **M1 does not support Nanite** (and per Epic, won't) — build test scenes without it.
  Lumen is **software-ray-traced only** on all Apple Silicon.
- TSR is expensive on Apple Silicon, which makes `r.AntiAliasingMethod` / `r.TSR.*` unusually
  high-leverage knobs.
- The proposer LLM runs locally via any OpenAI-compatible endpoint (llama.cpp on
  `localhost:8080/v1`, Ollama on `localhost:11434/v1`); **no cloud API is required** on a
  16 GB+ Mac. Base M1/8 GB is tight — see `docs/02-mac-m1-feasibility.md` for the hybrid
  fallback.

## Docs (read in order)

1. `docs/01-prior-art-survey.md` — landscape: autoresearch, AlphaEvolve, OpenEvolve, AI
   Scientist, RL autotuning; and the UE5 headless-benchmark + cvar/scalability substrate.
2. `docs/02-mac-m1-feasibility.md` — can it run locally on M1; hardware tiers; determinism.
3. `docs/03-poc-design.md` — the toy architecture, evaluator/objective, and M0→M4 build order.
4. `docs/04-goal-prompt.md` — §1 build prompt (scaffold the toy), §2 the in-loop `program.md`.
