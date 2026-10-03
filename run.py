"""Measure how far a LiteLLM virtual key overshoots its max_budget.

Usage: python3 run.py [config ...]   (needs docker + docker-compose; defaults to all configs)
Each config gets a fresh stack. Ground truth is the mock upstream's own hit counter.
Writes results/results.json and results/report.md.
"""
import datetime
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GATEWAY, MOCK, MASTER = "http://127.0.0.1:4000", "http://127.0.0.1:8080", "sk-local-only"
COST, BUDGET = 0.0001, 0.001  # per admitted call; budget allows 10 calls
IDLE = int(os.environ.get("IDLE_SECONDS", 90))  # > LiteLLM's 60s default counter TTL
RUNS = {"idle_gap": 3, "burst": 5}
CONFIGS = ["default", "batch3600", "mitigated"]


def http(url, body=None, key=MASTER, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}


def compose(config, *args):
    r = subprocess.run(["docker-compose", *args], cwd=ROOT, env={**os.environ, "CONFIG": config},
                       capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"docker-compose {' '.join(args)} failed ({config}):\n{r.stderr[-2000:]}")


def start(config):
    compose(config, "down", "-v")
    compose(config, "up", "-d")
    deadline = time.monotonic() + 600  # first boot runs Prisma migrations, which can take minutes
    while time.monotonic() < deadline:
        try:
            if http(f"{GATEWAY}/health/liveliness", timeout=3)[0] == 200:
                return
        except OSError:
            pass
        time.sleep(1)
    sys.exit(f"{config}: gateway did not become healthy")


def new_key():
    status, body = http(f"{GATEWAY}/key/generate", {"max_budget": BUDGET, "models": ["gpt-test"]})
    assert status == 200, f"key/generate returned {status}"
    return body["key"]


def call(key):
    return http(f"{GATEWAY}/v1/chat/completions",
                {"model": "gpt-test", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]}, key)[0]


def paced(key, n):
    codes = []
    for _ in range(n):
        codes.append(call(key))
        time.sleep(1)
    return codes


def idle_gap(key):
    before = paced(key, 15)
    time.sleep(IDLE)
    return {"before_idle": before, "after_idle": paced(key, 10)}


def burst(key):
    with ThreadPoolExecutor(40) as pool:
        return {"concurrent": list(pool.map(lambda _: call(key), range(40)))}


def run(config, scenario, i):
    http(f"{MOCK}/reset", {})
    key = new_key()
    phases = globals()[scenario](key)
    admitted = http(f"{MOCK}/hits")[1]["hits"]
    ok = sum(c == 200 for codes in phases.values() for c in codes)
    return {"config": config, "scenario": scenario, "run": i,
            "codes": {p: dict(Counter(map(str, c))) for p, c in phases.items()},
            "admitted": admitted, "gateway_200s": ok, "spend": round(admitted * COST, 6),
            "overshoot": round((admitted * COST - BUDGET) / BUDGET, 3)}


def image_digests():
    out = subprocess.run(["docker-compose", "config", "--images"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    return {img: subprocess.run(["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", img],
                                capture_output=True, text=True).stdout.strip() for img in out}


def report(results, manifest):
    lines = ["# Results", "",
             f"Run {manifest['run_at_utc']} · LiteLLM {manifest['images'].get('ghcr.io/berriai/litellm:v1.103.2', 'v1.103.2')}",
             f"Budget ${BUDGET} per key, ${COST} per call (10 calls). Idle gap {IDLE}s.", "",
             "| Config | Scenario | Runs | Admitted calls (median, min–max) | Overshoot vs budget (median, max) | 200s after idle (median) |",
             "|---|---|---|---|---|---|"]
    for config in manifest["configs"]:
        for scenario in RUNS:
            rs = [r for r in results if r["config"] == config and r["scenario"] == scenario]
            if not rs:
                continue
            adm = [r["admitted"] for r in rs]
            ovs = [r["overshoot"] for r in rs]
            after = [r["codes"].get("after_idle", {}).get("200", 0) for r in rs]
            lines.append(f"| {config} | {scenario} | {len(rs)} | {statistics.median(adm)} ({min(adm)}–{max(adm)}) | "
                         f"{statistics.median(ovs):+.0%}, {max(ovs):+.0%} | "
                         f"{statistics.median(after) if scenario == 'idle_gap' else '–'} |")
    lines += ["", "Overshoot = (admitted calls × cost − budget) / budget, counted at the mock upstream.", "",
              "## What this result does not establish", "",
              "- Real provider latency, streaming or token accounting; the upstream is a mock with fixed usage.",
              "- Multi-replica behaviour; one gateway container was used.",
              "- Gateways other than LiteLLM.", ""]
    return "\n".join(lines)


def main():
    configs = sys.argv[1:] or CONFIGS
    results = []
    for config in configs:
        start(config)
        for scenario, n in RUNS.items():
            for i in range(n):
                results.append(run(config, scenario, i))
                print(json.dumps(results[-1]), flush=True)
        compose(config, "down", "-v")
    manifest = {"run_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "configs": configs, "images": image_digests(), "idle_seconds": IDLE,
                "budget": BUDGET, "cost_per_call": COST, "runs": RUNS}
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / "results.json").write_text(json.dumps({"manifest": manifest, "runs": results}, indent=2) + "\n")
    (out / "report.md").write_text(report(results, manifest))
    print((out / "report.md").read_text())
    mismatched = [r for r in results if r["admitted"] != r["gateway_200s"]]
    if mismatched:
        sys.exit(f"{len(mismatched)} runs where upstream hits != gateway 200s (retries or lost responses)")


if __name__ == "__main__":
    main()
