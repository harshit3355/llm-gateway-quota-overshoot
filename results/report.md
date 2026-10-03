# Results

Run 2026-10-03T23:17:16+00:00 · LiteLLM ghcr.io/berriai/litellm@sha256:f63fb81b831b170ec16851e23c36ac5bf52ef106b271406429524a2ed730bbfd
Budget $0.001 per key, $0.0001 per call (10 calls). Idle gap 90s.

| Config | Scenario | Runs | Admitted calls (median, min–max) | Overshoot vs budget (median, max) | 200s after idle (median) |
|---|---|---|---|---|---|
| default | idle_gap | 3 | 10 (10–10) | +0%, +0% | 0 |
| default | burst | 5 | 10 (10–10) | +0%, +0% | – |
| batch3600 | idle_gap | 3 | 20 (20–20) | +100%, +100% | 10 |
| batch3600 | burst | 5 | 10 (10–10) | +0%, +0% | – |
| mitigated | idle_gap | 3 | 10 (10–10) | +0%, +0% | 0 |
| mitigated | burst | 5 | 10 (10–10) | +0%, +0% | – |

Overshoot = (admitted calls × cost − budget) / budget, counted at the mock upstream.

## What this result does not establish

- Real provider latency, streaming or token accounting; the upstream is a mock with fixed usage.
- Multi-replica behaviour; one gateway container was used.
- Gateways other than LiteLLM.
