# llm-gateway-quota-overshoot

A small, reproducible harness that measures how far an LLM gateway lets a virtual key **spend past its budget**. It runs the gateway against a deterministic mock upstream, so the true spend is known exactly.

The first target is **LiteLLM proxy v1.103.2**, including [BerriAI/litellm#43732](https://github.com/BerriAI/litellm/issues/43732): a key that has hit `max_budget` is admitted again after about a minute idle, until the batch writer flushes spend to the database.

## Who this is for

Platform teams that put LiteLLM, or another gateway, in front of model providers and rely on per-key budgets as a cost control. This matters most if they have raised `proxy_batch_write_at` to cut database writes.

## Result

From [`results/report.md`](results/report.md). Each key has a $0.001 budget and each call costs exactly $0.0001, so the budget allows 10 calls. Admitted calls are counted at the mock upstream.

| LiteLLM config | Idle gap: 15 paced calls, 90 s idle, 10 more (3 runs) | Burst: 40 concurrent calls (5 runs) |
|---|---|---|
| default (`proxy_batch_write_at` 10 s) | 10 admitted, **+0%** | 10 admitted, +0% |
| `proxy_batch_write_at: 3600` | **20 admitted, +100%** in every run; all 10 post-idle calls admitted | 10 admitted, +0% |
| `default_redis_ttl: 3600` + `proxy_batch_write_at: 60` (workaround from the issue) | 10 admitted, +0% | 10 admitted, +0% |

What this shows:
- **#43732 reproduces.** With an hourly flush, a blocked key gets its whole budget again after a 90-second idle gap, and would keep doing so after every gap until the next flush.
- **The reported workaround removes it** in this setup.
- **Concurrency is not the problem here.** 40 simultaneous requests never got past the budget.

## How it works

```
run.py ──HTTP──▶ LiteLLM proxy (pinned image) ──▶ mock upstream (fixed usage, counts hits)
                    │            │
                 Postgres      Redis
```

- `mock/server.py` is an OpenAI-compatible stub. Every completion reports 10 output tokens, and `/hits` returns how many completions it served (the ground truth).
- `configs/*.yaml` are the three LiteLLM configs above. Model cost is set explicitly (`output_cost_per_token: 0.00001`), so results don't depend on LiteLLM's price map.
- `run.py` starts a fresh stack per config, creates a budgeted key per run, runs the scenarios, and checks that upstream hits equal gateway 200s. It writes `results/results.json` (per run, with image digests) and `results/report.md`.

## Quick start

Needs Docker, `docker-compose` v2 and Python 3 (standard library only). No API keys and no cost.

```bash
python3 run.py                 # all configs, about 45 minutes (each idle-gap run waits 90 s)
python3 run.py batch3600       # one config
IDLE_SECONDS=120 python3 run.py
```

The first LiteLLM start runs database migrations and can take several minutes on a slow machine. Postgres runs on tmpfs because the database is throwaway.

## Failure and security model

- Everything binds to `127.0.0.1`. The credentials in `compose.yaml` (Postgres `llm/llm`, master key `sk-local-only`) are throwaway values for these local containers only. Never reuse them.
- `run.py` exits non-zero if the gateway never becomes healthy, if `docker-compose` fails, or if upstream hits and gateway 200s disagree (which would mean hidden retries or lost responses).

## Limitations

- One gateway replica. Multi-replica counter sync is not measured.
- A mock upstream with fixed usage: no streaming, no real latency, no provider-side token accounting.
- LiteLLM only so far. Kong's `rate-limiting` (Kong/kong#15018) and Agent Router quota policies (#2460) are candidate next targets.
- Three repeats per idle-gap config and five per burst config. The results were identical across repeats, but that is a small sample.

## Lineage

- The LiteLLM report is [BerriAI/litellm#43732](https://github.com/BerriAI/litellm/issues/43732). Related: #34238 (end-user counters).
- [FailureAtlas (arXiv 2607.17525)](https://arxiv.org/abs/2607.17525) lists leaking rate-limit counters as a governance failure class in multi-provider LLM serving.

## License

Apache-2.0.
