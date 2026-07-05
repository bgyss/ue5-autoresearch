<!-- markdownlint-disable MD041 MD018 -->
<!-- LinkedIn post — copy from the line below the horizontal rule -->
---

I've been experimenting with applying Karpathy's autoresearch framework to a domain I care about: **Unreal Engine 5 graphics optimization**.

The idea is straightforward. Karpathy's autoresearch reduces "AI doing research" to a tight, boring, measurable loop: a human writes goals in `program.md`, an agent edits a single mutable file, runs a time-boxed experiment, reads one number back, and keeps the change only if that number improved — forever.

I applied the exact same shape to UE5 rendering:

- The **"genome"** is `config/candidate.cvars` — a set of console variables controlling shadow quality, screen percentage, global illumination, anti-aliasing, and more
- The **"experiment"** is a deterministic benchmark flythrough that emits frame-time CSVs and a screenshot
- The **"one number"** is a fitness score that rewards lower GPU frame time while penalizing visual quality loss (SSIM + perceptual diff)
- The **loop** proposes a new config, benchmarks it, keeps the commit if p95 improved by >2% over the current best, and reverts otherwise — classic git branch hill-climbing

Two modes to run it:

1. **Script-driven:** `harness/propose.py` calls a local LLM (llama.cpp, Ollama, or any OpenAI-compatible server) to edit the cvars file, runs the evaluator, and commits or reverts automatically.
2. **Agent-driven (the autoresearch way):** point Claude Code (or any coding agent) at `program.md` and let it own the loop directly.

The whole thing runs locally on Apple Silicon — sequential LLM proposal then UE5 benchmark, so you're never running both at full tilt simultaneously.

There's also a **mock demo mode** (`UE5AR_MOCK=1`) that swaps the real benchmark for an analytic cost model with realistic noise — so anyone can try the full autoresearch loop with just Python, no Unreal Engine installation required.

The hardest part isn't the loop. It's the evaluator — measuring "faster without looking worse" honestly. That's true in ML research too, and it's the core insight AlphaEvolve's authors kept emphasizing.

Repo is on GitHub: **ue5-autoresearch**

If you're interested in autonomous optimization loops, local LLMs, or UE5 rendering, happy to chat.

#UnrealEngine #GameDev #MachineLearning #AI #GraphicsOptimization #LocalLLM #OpenSource
