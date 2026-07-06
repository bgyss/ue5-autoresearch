<!-- markdownlint-disable MD041 MD018 -->
<!-- LinkedIn post — copy from the line below the horizontal rule -->
---

What if an AI agent could tune an Unreal Engine 5 scene the way a researcher tunes a model - propose a change, run the experiment, keep it only if the numbers actually improve?

I built a small open-source harness around that idea, inspired by Karpathy's `autoresearch`.

The loop is intentionally boring:

- `program.md` defines the goal and keep/discard rules
- `config/candidate.cvars` is the only mutable "genome"
- a deterministic UE5 flythrough measures p95 frame time and captures a reference image
- the evaluator rewards lower GPU frame time, but penalizes visual quality loss with SSIM + perceptual diff
- if the candidate clears the improvement gate, it stays; otherwise it gets reverted

So the agent cannot win by turning everything off. It has to find settings that are faster without obviously looking worse.

It runs two ways:

1. `harness/propose.py` can drive the loop with any local OpenAI-compatible LLM server, including llama.cpp or Ollama.
2. A coding agent can own the loop directly by following `program.md`.

There is also a mock mode (`UE5AR_MOCK=1`) that swaps Unreal for a noisy analytic cost model, so you can try the full loop with just Python.

The part I find most interesting: the LLM is not the source of truth. The evaluator is. That feels like the useful pattern for a lot of agentic engineering work - give the model a narrow search space, a real measurement harness, and a ruthless keep/discard gate.

Repo: **ue5-autoresearch**

If you are working on autonomous optimization loops, local LLMs, or UE rendering, I would be curious to compare notes.

#UnrealEngine #GameDev #MachineLearning #AI #GraphicsOptimization #LocalLLM #OpenSource
