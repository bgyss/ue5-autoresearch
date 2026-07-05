#!/usr/bin/env python3
"""Script-driven autoresearch loop (M2).

Calls a local LLM via an OpenAI-compatible endpoint (e.g. llama.cpp server or
llamabarn) to propose the next `config/candidate.cvars`, runs the fixed
evaluator, and keeps the commit only if p95 GPU frame time improved by more
than the noise gate over the current best.

    python harness/propose.py --iterations 50

Environment:
    LLM_MODEL       model name (default: empty; llama.cpp single-model server
                    ignores this field, Ollama and llamabarn require it)
    LLM_BASE_URL    OpenAI-compatible endpoint (default:
                    http://localhost:8080/v1 for llama.cpp; for Ollama use
                    http://localhost:11434/v1)
    LLM_API_KEY     optional bearer token (default: empty)
    UE5AR_MOCK      set to 1 (or pass --mock) to simulate the UE5 benchmark
                    so the loop can be demoed without Unreal installed

The proposer may only edit config/candidate.cvars; everything under harness/ is
read-only by policy. Release the LLM's GPU memory before each UE run by
stopping or unloading the model server externally.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
PROGRAM_MD = REPO_ROOT / "program.md"
CANDIDATE_CVARS = REPO_ROOT / "config" / "candidate.cvars"
BASELINE_CVARS = REPO_ROOT / "config" / "baseline.cvars"
ALLOWED_CVARS = REPO_ROOT / "harness" / "allowed_cvars.txt"
EVALUATE_PY = REPO_ROOT / "harness" / "evaluate.py"


def results_tsv() -> Path:
    """Match evaluate.py: mock runs log to results.mock.tsv so demo output
    never contaminates the real benchmark history."""
    if os.environ.get("UE5AR_MOCK", "") not in ("", "0"):
        return REPO_ROOT / "results.mock.tsv"
    return REPO_ROOT / "results.tsv"


DEFAULT_MODEL = os.environ.get("LLM_MODEL", "")
DEFAULT_BASE_URL = os.environ.get(
    "LLM_BASE_URL", "http://localhost:8080/v1"
)
DEFAULT_API_KEY = os.environ.get("LLM_API_KEY", "")

IMPROVEMENT_PCT = 2.0  # keep only if p95 improved by > this over incumbent
RESULTS_HISTORY = 8  # recent result rows to show the model
TIMEOUT = 120  # seconds for LLM response


class ProposeError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class ResultRow:
    timestamp: str
    p50_ms: float
    p95_ms: float
    p99_ms: float
    ssim: float
    flip: float
    fitness: float
    passed: bool


def load_program() -> str:
    return PROGRAM_MD.read_text()


def load_candidate() -> str:
    return CANDIDATE_CVARS.read_text()


def load_allowed_cvars() -> set[str]:
    allowed = set()
    for line in ALLOWED_CVARS.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            allowed.add(line)
    return allowed


def load_recent_results(n: int = RESULTS_HISTORY) -> list[ResultRow]:
    if not results_tsv().exists():
        return []
    rows: list[ResultRow] = []
    with results_tsv().open() as f:
        header = f.readline().strip().split("\t")
        for line in f:
            line = line.strip()
            if not line:
                continue
            values = line.split("\t")
            if len(values) != len(header):
                continue
            row_dict = dict(zip(header, values))
            try:
                rows.append(
                    ResultRow(
                        timestamp=row_dict.get("timestamp", ""),
                        p50_ms=float(row_dict.get("p50_ms", "nan")),
                        p95_ms=float(row_dict.get("p95_ms", "nan")),
                        p99_ms=float(row_dict.get("p99_ms", "nan")),
                        ssim=float(row_dict.get("ssim", "nan")),
                        flip=float(row_dict.get("flip", "nan")),
                        fitness=float(row_dict.get("fitness", "nan")),
                        passed=row_dict.get("passed", "False").lower()
                        == "true",
                    )
                )
            except ValueError:
                continue
    return rows[-n:]


def best_p95_so_far() -> float:
    """Best (lowest) p95_ms among passing configs in results.tsv.

    Returns +inf if no passing run exists, so the first passing run is a win.
    """
    best = float("inf")
    for row in load_recent_results(10_000):
        if row.passed and row.p95_ms > 0 and row.p95_ms < best:
            best = row.p95_ms
    return best


def parse_cvars_from_response(text: str, allowed: set[str]) -> dict[str, str]:
    """Extract KEY=VALUE lines from a markdown-wrapped model response.

    Raises ProposeError if no valid allowed cvars were found.
    """
    cvars: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        line = line.lstrip("-\n ")  # tolerate markdown bullet prefix
        if line.startswith("```"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.split("#", 1)[0].strip()
        if key not in allowed:
            continue
        cvars[key] = value
    if not cvars:
        raise ProposeError(
            f"model produced no allowed KEY=VALUE lines:\n{text[:2000]}"
        )
    return cvars


def render_candidate(cvars: dict[str, str]) -> str:
    """Render candidate.cvars preserving the current order; new keys at end."""
    current = load_candidate()
    order = []
    for line in current.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if "=" in stripped:
            order.append(stripped.partition("=")[0].strip())
    for key in cvars:
        if key not in order:
            order.append(key)
    lines = []
    for key in order:
        if key in cvars:
            lines.append(f"{key}={cvars[key]}")
    return "\n".join(lines) + "\n"


def build_prompt(
    program: str,
    candidate: str,
    allowed: list[str],
    recent: list[ResultRow],
) -> str:
    recent_text = ""
    if recent:
        recent_text += "\nRecent experiments (newest last):\n\n"
        recent_text += (
            "timestamp\tp50_ms\tp95_ms\tp99_ms\tssim\t"
            "flip\tfitness\tpassed\n"
        )
        for row in recent:
            recent_text += (
                f"{row.timestamp}\t{row.p50_ms}\t{row.p95_ms}\t"
                f"{row.p99_ms}\t{row.ssim}\t{row.flip}\t"
                f"{row.fitness}\t{row.passed}\n"
            )
        recent_text += f"\nCurrent best passing p95_ms: {best_p95_so_far()}\n"
    else:
        recent_text = "\nNo experiments yet.\n"

    prompt = (
        "You are optimizing a UE5 rendering config. Follow the program.md "
        "below exactly.\n\n"
        "=== program.md ===\n"
        f"{program}\n"
        "=== end program.md ===\n\n"
        "=== allowed cvars ===\n"
        + "\n".join(allowed)
        + "\n=== end allowed cvars ===\n\n"
        "=== current candidate.cvars ===\n"
        f"{candidate}\n"
        "=== end candidate.cvars ===\n"
        f"{recent_text}\n"
        "Your task: propose the next candidate.cvars. Output ONLY the new "
        "file contents, one KEY=VALUE line per cvar. Do not explain your "
        "reasoning. Do not add markdown. All keys must be in the allow-list "
        "above. Change only a small, coherent set of cvars per experiment so "
        "the result is attributable.\n"
    )
    return prompt


def chat_completion(
    base_url: str, api_key: str, model: str, prompt: str
) -> str:
    """Call an OpenAI-compatible chat endpoint.

    Works with llama.cpp server, llamabarn, or any other OpenAI-compatible
    local LLM server.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload: dict[str, Any] = {
        "messages": [
            {
                "role": "system",
                "content": "You are a UE5 rendering optimization assistant.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.6,
        "stream": False,
    }
    if model:
        payload["model"] = model
    data = json.dumps(payload).encode("utf-8")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url, data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            response = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise ProposeError(f"LLM HTTP {e.code}: {body[:500]}") from e
    except urllib.error.URLError as e:
        raise ProposeError(f"LLM connection failed: {e.reason}") from e

    if "error" in response:
        raise ProposeError(f"LLM returned error: {response['error']}")
    try:
        return response["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as e:
        raise ProposeError(
            f"unexpected LLM response shape: {response}"
        ) from e


def git_rev_parse_head() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def git_has_changes() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


def git_commit(message: str) -> None:
    # Only commit the file the loop is allowed to change; do not pull in
    # other workspace files (logs, CSVs, etc.) that may be untracked.
    subprocess.run(
        ["git", "add", str(CANDIDATE_CVARS)], cwd=REPO_ROOT, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", message], cwd=REPO_ROOT, check=True
    )


def git_reset_to(rev: str) -> None:
    subprocess.run(["git", "reset", "--hard", rev], cwd=REPO_ROOT, check=True)


def run_evaluate() -> tuple[bool, dict[str, Any]]:
    """Run the fixed evaluator. Returns (ok, row_data)."""
    result = subprocess.run(
        [sys.executable, str(EVALUATE_PY)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    ok = result.returncode == 0
    output = result.stdout + result.stderr
    # Try to parse the summary line from stdout.
    metrics: dict[str, Any] = {}
    match = re.search(
        r"p50=(?P<p50>[\d.]+)ms\s+"
        r"p95=(?P<p95>[\d.]+)ms\s+"
        r"p99=(?P<p99>[\d.]+)ms\s+"
        r"ssim=(?P<ssim>[\d.]+)\s+"
        r"flip=(?P<flip>[\d.]+)\s+"
        r"fitness=(?P<fitness>[\d.-]+|inf|-inf)\s+"
        r"passed=(?P<passed>True|False)",
        output,
    )
    if match:
        metrics = {
            "p50_ms": float(match.group("p50")),
            "p95_ms": float(match.group("p95")),
            "p99_ms": float(match.group("p99")),
            "ssim": float(match.group("ssim")),
            "flip": float(match.group("flip")),
            "fitness": match.group("fitness"),
            "passed": match.group("passed") == "True",
        }
    return ok, metrics


def run_one_iteration(
    model: str,
    base_url: str,
    api_key: str,
    dry_run: bool,
    allowed: set[str],
) -> dict[str, Any]:
    """Single propose -> evaluate -> keep/discard cycle."""
    baseline = git_rev_parse_head()
    program = load_program()
    candidate = load_candidate()
    recent = load_recent_results()

    prompt = build_prompt(program, candidate, sorted(allowed), recent)
    response = chat_completion(base_url, api_key, model, prompt)
    new_cvars = parse_cvars_from_response(response, allowed)
    new_text = render_candidate(new_cvars)

    if dry_run:
        print("--- proposed candidate.cvars ---")
        print(new_text)
        return {
            "action": "dry_run",
            "baseline": baseline,
            "p95_ms": None,
            "passed": None,
        }

    CANDIDATE_CVARS.write_text(new_text)
    if not git_has_changes():
        print("model produced identical candidate.cvars; skipping.")
        return {
            "action": "no_change",
            "baseline": baseline,
            "p95_ms": None,
            "passed": None,
        }

    git_commit("exp: scripted LLM proposal")
    ok, metrics = run_evaluate()
    passed = metrics.get("passed", False)
    p95_ms = metrics.get("p95_ms", float("inf"))
    action = "unknown"

    if not ok:
        print("evaluate.py failed; resetting.")
        git_reset_to(baseline)
        action = "reset_failed"
    elif not passed:
        print(
            f"rejected: p95={p95_ms}ms ssim={metrics.get('ssim')} "
            f"flip={metrics.get('flip')}; resetting."
        )
        git_reset_to(baseline)
        action = "reset_rejected"
    else:
        best = best_p95_so_far()
        # best includes the row just appended by evaluate.py.
        if best == p95_ms:
            print(f"KEEP p95={p95_ms}ms (first passing result).")
            action = "keep"
        elif p95_ms < best * (1 - IMPROVEMENT_PCT / 100):
            print(f"KEEP p95={p95_ms}ms (>{IMPROVEMENT_PCT}% improvement).")
            action = "keep"
        else:
            print(
                f"RESET p95={p95_ms}ms not >{IMPROVEMENT_PCT}% better "
                f"than {best}ms; resetting."
            )
            git_reset_to(baseline)
            action = "reset_noise"

    return {
        "action": action,
        "baseline": baseline,
        "p95_ms": p95_ms,
        "passed": passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--iterations",
        type=int,
        default=50,
        help="number of propose/evaluate cycles",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, help="LLM model name (optional)"
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=(
            "OpenAI-compatible endpoint "
            "(llama.cpp default: http://localhost:8080/v1)"
        ),
    )
    parser.add_argument(
        "--api-key", default=DEFAULT_API_KEY, help="optional bearer token"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="propose one candidate.cvars without running UE",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="demo mode: evaluate with the simulated benchmark instead of "
        "launching UE5 (same as UE5AR_MOCK=1)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=5.0,
        help="seconds between iterations for thermal recovery",
    )
    args = parser.parse_args()

    if args.mock:
        os.environ["UE5AR_MOCK"] = "1"  # inherited by the evaluate.py subprocess

    allowed = load_allowed_cvars()
    if not allowed:
        sys.exit(f"allow-list empty: {ALLOWED_CVARS}")

    if args.dry_run:
        run_one_iteration(
            args.model,
            args.base_url,
            args.api_key,
            dry_run=True,
            allowed=allowed,
        )
        return

    for i in range(args.iterations):
        print(f"\n=== iteration {i + 1}/{args.iterations} ===")
        try:
            run_one_iteration(
                args.model,
                args.base_url,
                args.api_key,
                dry_run=False,
                allowed=allowed,
            )
        except ProposeError as e:
            print(f"propose error: {e}", file=sys.stderr)
        except subprocess.CalledProcessError as e:
            print(f"git/evaluate error: {e}", file=sys.stderr)
        except Exception as e:
            print(f"unexpected error: {e}", file=sys.stderr)

        if i < args.iterations - 1:
            time.sleep(args.delay)

    print("\n=== final results ===")
    if results_tsv().exists():
        print(results_tsv().read_text())
    best_p95 = best_p95_so_far()
    print(f"\nBest passing p95_ms: {best_p95}")
    if best_p95 != float("inf"):
        print("\n=== best diff vs baseline.cvars ===")
        subprocess.run(
            ["diff", "-u", str(BASELINE_CVARS), str(CANDIDATE_CVARS)],
            cwd=REPO_ROOT,
            check=False,
        )


if __name__ == "__main__":
    main()
