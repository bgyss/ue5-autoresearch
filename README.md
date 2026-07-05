# ue5-autoresearch

Applying **Karpathy-style autonomous research loops** to **Unreal Engine 5 graphics
optimization** — an agent that iteratively proposes rendering-config changes, benchmarks
them, keeps what's faster (without wrecking visual quality), and repeats.

## The idea in one paragraph

Karpathy's [`autoresearch`](https://github.com/karpathy/autoresearch) reduced "AI doing
research" to a tight, boring, measurable loop: a human writes goals in `program.md`, an
agent edits a single mutable file (`train.py`), runs a time-boxed experiment, reads one
number back, and keeps the change only if the number improved — forever. We apply the
exact same shape to UE5: the mutable "genome" is `config/candidate.cvars` (rendering
console variables / scalability groups), the "experiment" is a deterministic benchmark
flythrough that emits a CSV of frame times plus a screenshot, and the "one number" is a
fitness score that rewards lower frame time while penalizing visual-quality loss.

## Mapping onto autoresearch

| autoresearch | this project | trust level |
| --- | --- | --- |
| `program.md` (human-edited rules) | `program.md` | human-authored goal |
| `train.py` (mutable "genome") | `config/candidate.cvars` | **the only file the loop edits** |
| `prepare.py` (fixed evaluator) | `harness/evaluate.py` | **read-only to the loop** (no reward hacking) |
| `val_bpb` (one number) | `fitness` = frame-time reward + quality penalty | — |
| git branch hill-climb | same (`autoresearch/<date>` branch, revert on regression) | — |
| `results.tsv` (untracked, append-only) | same | — |

## Project state

M2 (scripted loop + mock demo mode) is complete. **M3 work will resume when GPT-5.6 is
released (expected later this week, July 2026).**

The deterministic evaluator (`harness/evaluate.py`) works end-to-end on an Apple
Silicon Mac with UE 5.x installed, and there is a **mock demo mode** that simulates the
benchmark so the whole loop can be tried on any machine with just Python. Two loop
drivers are available:

- **Script-driven:** `python harness/propose.py` calls a local LLM (llama.cpp, Ollama,
  or any OpenAI-compatible server) to edit `config/candidate.cvars`, runs the evaluator,
  and keeps the commit only if p95 improved by >2% over the current best.
- **Agent-driven (the autoresearch way):** open the workspace in a coding agent (Claude
  Code, etc.) and point it at `program.md`; it edits `config/candidate.cvars`, runs
  `python harness/evaluate.py`, keeps/discards per `program.md`, and repeats.

## Layout

```text
ue5-autoresearch/
├── program.md                 # in-loop goal (agent reads this)
├── config/
│   ├── candidate.cvars        # THE GENOME — only file the loop edits
│   └── baseline.cvars         # frozen Epic/High reference
├── harness/
│   ├── evaluate.py            # fixed evaluator (read-only to the loop)
│   ├── run_ue.py              # launch UE5 headless benchmark
│   ├── mock_ue.py             # simulated benchmark for demo mode (no UE5 needed)
│   ├── parse_csv.py           # tolerant UE CSV parser
│   ├── quality.py             # SSIM gate + FLIP-like perceptual term
│   ├── propose.py             # scripted local-LLM loop driver
│   └── allowed_cvars.txt      # anti-reward-hacking allow-list
├── reference/
│   └── ref_pose01.png         # max-quality screenshot at the fixed pose
├── results.tsv                # experiment log (untracked — append-only, survives resets)
└── ue_project/                # small UE5 test project
```

## Setup (nix + mise + uv)

The project is managed with **nix** (system deps), **mise** (tool versions), and **uv**
(Python packages), but plain `uv` (or `pip install -r requirements.txt` in a venv) is
enough. If you have nix + direnv enabled:

```bash
direnv allow
# this runs: nix develop, mise install, uv sync, and venv activation
```

Or manually:

```bash
nix develop
mise install
uv sync
source .venv/bin/activate
```

After activation, the canonical commands are:

```bash
uv run python harness/evaluate.py          # run one evaluation
uv run python harness/propose.py --iterations 50   # scripted local-LLM loop
mise run lint                              # ruff check
mise run fmt                               # ruff format
```

Python dependencies are declared in `pyproject.toml`. `requirements.txt` is kept for
backwards compatibility with plain pip installs.

## Quick start

### Demo mode — no Unreal Engine required

`UE5AR_MOCK=1` (or `--mock`) swaps the UE5 benchmark for a simulated one
(`harness/mock_ue.py`): an analytic cost model over the same cvars, with realistic
run-to-run noise and a quality floor that rejects "turn everything off". The rest of
the pipeline — validation, fitness, results log, git hill-climbing — is identical;
mock runs log to `results.mock.tsv` so they never contaminate real benchmark history.

```bash
uv run python harness/evaluate.py --mock                 # one simulated evaluation
uv run python harness/propose.py --mock --iterations 5   # full loop w/ a local LLM
```

### The real thing — requirements

- macOS on Apple Silicon with **Unreal Engine 5.x** installed. The harness looks for
  the editor at `/Users/Shared/Epic Games/UE_5.7`; override with `UE_ROOT`.
- The bundled `ue_project/` test scene (no Nanite — unsupported on Apple Silicon).

```bash
uv run python harness/evaluate.py    # one real benchmark (~2-5 min)
```

### Script-driven loop (local LLM)

Start a local OpenAI-compatible LLM server — llama.cpp (`llama-server`, provided by the
nix shell), Ollama, llamabarn, etc. — then:

```bash
uv run python harness/propose.py --iterations 50
```

The default endpoint is `http://localhost:8080/v1` (llama.cpp). For Ollama:

```bash
LLM_BASE_URL=http://localhost:11434/v1 LLM_MODEL=llama3.1 \
  uv run python harness/propose.py --iterations 50
```

Use `LLM_BASE_URL`, `LLM_MODEL`, and `LLM_API_KEY` to point at any other server. On a
RAM-constrained machine, release the LLM's GPU memory before each UE5 benchmark by
stopping or unloading the model server externally.

### Agent-driven loop (the autoresearch way)

Open the workspace in a coding agent and tell it:

> Follow `program.md` and optimize `config/candidate.cvars`. Run `python
> harness/evaluate.py` after each change, read the new row in `results.tsv`, keep the
> commit only if p95 improved by >2% while passing the SSIM/FLIP gate, and repeat.

Or, from the Claude Code CLI:

```bash
claude
# then: "Follow program.md and run the optimization loop."
```

As in autoresearch, `program.md` is the file the *human* iterates on between sessions;
the agent iterates on `config/candidate.cvars`.

## Documents (read in order)

1. [`docs/01-prior-art-survey.md`](docs/01-prior-art-survey.md) — comprehensive survey of
   what already exists: autonomous research/evolution loops (autoresearch, AlphaEvolve,
   OpenEvolve, AI Scientist) and the UE5 profiling/scalability tooling we'd build on.
2. [`docs/02-mac-m1-feasibility.md`](docs/02-mac-m1-feasibility.md) — can this actually
   run locally on an Apple Silicon Mac? UE5-on-M1 constraints, local-LLM options, and the
   RAM/GPU-contention reality. **Read this to judge viability.**
3. [`docs/03-poc-design.md`](docs/03-poc-design.md) — the proposed toy architecture,
   file-for-file mapped onto autoresearch, plus the all-important evaluator/objective
   design.
4. [`docs/04-goal-prompt.md`](docs/04-goal-prompt.md) — the deliverable you asked for: a
   **build prompt** to hand a coding agent to scaffold the toy, and the **`program.md`**
   the optimization loop itself will run on.

## TL;DR viability

- **Feasible on M1**, with caveats. The loop is *sequential* (LLM proposes, then UE5
  benchmarks), so you don't need to run both at full tilt simultaneously.
- **Avoid Nanite** on M1 (unsupported and won't be); Lumen runs software-only. There is
  still a rich, real optimization landscape (screen percentage, shadows, post-process,
  GI/reflection quality, effects, foliage, view distance, AA method).
- The **hard part is the evaluator**, not the loop — exactly as AlphaEvolve's authors
  warn. Most of the design doc is about measuring "faster without looking worse" honestly.
