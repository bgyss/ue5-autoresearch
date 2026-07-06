# Announcement post drafts

Draft copy for announcing the project publicly. These are **drafts only** — review,
edit voice/links, and post manually. Update the repo URL if it changes:
https://github.com/bgyss/ue5-autoresearch

---

## Reddit

### r/unrealengine (long-form)

**Title:** I built an autonomous multi-agent loop that optimizes a UE5 scene's
graphics settings against a real frame-time budget — open source

**Body:**

I've been experimenting with letting LLM agents run structured optimization
experiments against Unreal Engine 5, and it's grown into a full open-source
harness I wanted to share.

**What it does**

- A fleet of agents, each owning one "facet" of scene quality (shadows, GI/Lumen,
  AA/upscaling, geometry/Nanite, post-processing, etc.)
- Each agent proposes cvar/settings changes, runs an instrumented benchmark pass
  (`-deterministic`, fixed camera track, CSV GPU stats), and submits results to a
  shared append-only ledger
- A keep/discard gate scores every candidate on frame time *and* perceptual
  quality (SSIM/FLIP against a golden reference) — so agents can't "win" by making
  the game ugly
- Everything is reproducible: every result row records the exact config, content
  hash, worker class, and image digest that produced it

**What I learned**

- Determinism is the whole ballgame. Run-to-run variance eats small wins, so the
  harness does warmup passes, locked GPU clocks, and median-of-N captures
- Cloud GPUs ≠ consumer GPUs. There's a whole section in the docs about parity
  configs, calibrated PC-tier profiles, and (open research) using downclocked
  RDNA2 cards as PS5/Series X proxies
- Agents are surprisingly good at boring, systematic parameter sweeps — the value
  is the harness that keeps them honest, not the model being clever

Repo + docs: <REPO_URL>

Happy to answer questions about the benchmark harness, the keep/discard gating,
or the multi-agent coordination. Feedback very welcome — especially from anyone
who's done serious UE profiling and can poke holes in the methodology.

---

### r/MachineLearning or r/LocalLLaMA (shorter, ML angle)

**Title:** [P] Multi-agent autonomous optimization loop for real-time rendering:
LLM agents tune UE5 graphics settings under a frame-budget + perceptual-quality
gate

**Body:**

Open-sourced a harness where multiple LLM agents each own one facet of a UE5
scene's render settings and run closed-loop experiments: propose config → run
deterministic instrumented benchmark → score on p95 frame time + SSIM/FLIP vs a
golden reference → keep/discard → append to a shared results ledger.

Interesting bits from an ML systems perspective:

- The reward signal is real hardware telemetry, not a simulator — so the harness
  invests heavily in determinism (locked clocks, warmups, median-of-N, parity
  configs across worker classes)
- Agents share a ledger but act independently; the gate, not the agents, is the
  authority on what merges
- Open research direction: a cross-hardware transfer model that learns how config
  deltas transfer between GPU classes (cloud A10G → console-proxy RDNA2 → Apple
  Silicon), with promotion criteria based on held-out prediction error

Repo: <REPO_URL>

---

## X / Twitter

### Thread version

**1/**
I let a fleet of AI agents loose on an Unreal Engine 5 scene with one rule: make
it faster without making it uglier.

Each agent owns one facet — shadows, Lumen, upscaling, Nanite — and runs real
benchmarks on real GPUs. Open source. 🧵

**2/**
The core loop: agent proposes a settings change → deterministic instrumented
benchmark run → gate scores it on p95 frame time AND perceptual quality
(SSIM/FLIP vs a golden frame) → keep or discard → result lands in a shared
ledger every agent can read.

**3/**
The hard part isn't the AI — it's determinism. Locked GPU clocks, warmup passes,
median-of-3 captures, parity configs so a cloud A10G and a Mac produce comparable
rows. If your noise floor is bigger than your wins, you're optimizing nothing.

**4/**
Fun open research question in the docs: can a downclocked RX 6700 stand in for a
PS5? (RDNA2, ~matching CUs and clocks.) Not perfectly — but "close, with a
measured error bar" is all a frame-budget gate needs.

**5/**
Repo + full docs (goal prompts, benchmark harness, cloud worker setup, stretch
goals): <REPO_URL>

Feedback welcome, especially from rendering engineers who want to poke holes in
the methodology.

### Single-post version

I open-sourced an autonomous multi-agent loop that optimizes UE5 graphics
settings against a real frame-time budget — with a perceptual-quality gate so
agents can't cheat by making the game ugly. Deterministic benchmarks, shared
results ledger, cloud + local GPU workers.

<REPO_URL>

---

## Posting checklist

- [ ] Replace `<REPO_URL>` with the final public repo URL
- [ ] Make the repo public and confirm README renders (badges, demo GIF/video)
- [ ] Record a short capture (before/after or agent-loop timelapse) — posts with
      media perform far better on both platforms
- [ ] Check subreddit self-promo rules (r/unrealengine allows project posts;
      r/MachineLearning requires the `[P]` tag)
- [ ] Post Reddit first, then link the thread from X for cross-traffic
