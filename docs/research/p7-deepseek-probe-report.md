# DeepSeek live probe (gated step L3, `V2-P7-012`) -- report

> Tooling output for the gated live step L3. Metadata and three answers to **fictional** contexts;
> nothing here is evidence about any security or about the model's research value. 候选 / candidate
> wording applies to anything drawn from it.

- Run: 2026-11-14 00:23 UTC (2026-11-13 19:23 Toronto; a Saturday in Beijing, so off-peak pricing).
- Authority: the user's go for L3, at most 5 DeepSeek requests.
- Requests sent: **3 of 5**. No retry was needed; nothing was refused.
- Code path: the shipped composition root `runtime.composition.llm_provider(DEEPSEEK_V4_1_FLASH,
  replay=False)` on main `8bfac68` (L2 merged), with a counting wrapper on the inner transport
  that refused a sixth request. The script is scratch tooling, not shipped:
  `~/openalpha-smoke/p7-deepseek-probe/l3_probe.py`, sha256
  `58f44c957207836d8e85ebe9dc3d1f5c6b85a9225dd4df080a920952998d7b07`.
- Credential: `DEEPSEEK_API_KEY` read by name from `.env` by the provider; the value was never
  printed. The scratch runtime directory holds no `Authorization`/`Bearer` string and no key.
- Scratch runtime: `~/openalpha-smoke/p7-deepseek-probe/runtime-2026-11-13/` -- 3 stored
  exchanges under `model_exchanges/` and 3 `model_usage` rows in `state.sqlite3`, one per request.

## Per request

| context | json valid | keys | blank content | finish_reason | `response.model` | `system_fingerprint` | prompt / completion tokens | cache hit | est. cost (USD) |
|---|---|---|---|---|---|---|---|---|---|
| 1 limit-up, sealed, sector breadth | yes | `abstain`, `confidence`, `direction`, `reason` | no | `stop` | `deepseek-flash` | `bc19ba97cbe016479903222bb1578db6` | 168 / 61 | 0 | 0.000062 |
| 2 five-day fall, net outflow | yes | same | no | `stop` | `deepseek-flash` | same | 149 / 53 | 0 | 0.000054 |
| 3 one uninformative sentence | yes | same | no | `stop` | `deepseek-flash` | same | 129 / 42 | 0 | 0.000045 |

Total: 446 prompt + 156 completion tokens, estimated USD 0.000161 at the configured off-peak prices.

Context 3 answered `abstain: true` with confidence 0.0: the model withheld a direction when the
material carried no information, as the system prompt asked. Contexts 1 and 2 answered `up` / `down`
at confidence 0.55. These are three answers to invented material, not a measurement of anything.

## What L3 settled

1. **The verified endpoint answers.** `deepseek-flash` at `https://api.deepseek.com` with
   `response_format={"type":"json_object"}` returned a JSON object on every request; no blank
   content, no parse failure, `finish_reason=stop` each time.
2. **Thinking is off.** No `completion_tokens_details.reasoning_tokens` and no `reasoning_content`
   came back, so the payload's `thinking: {"type": "disabled"}` took effect (and with it
   `temperature = 0`).
3. **Usage is reported and recorded.** Each response carried `usage` with
   `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`; the provider wrote one `model_usage` row
   per real request.
4. **`response.model` does not name the version behind the alias.** It echoed `deepseek-flash`,
   not `DeepSeek-V4.1-Flash-1017`. So `response.model` alone cannot show the alias moving during
   the forward window; **`system_fingerprint` is the field that can**. The protocol and runbook
   already store both per exchange; this report records that only the fingerprint is informative.
   Whether the fingerprint changes only on a model change (and not, for example, on a deployment
   change) is not established by three requests in one minute.

## Not settled

- Determinism at `temperature = 0` (no request was repeated; L3's cap is for the endpoint check).
- Behaviour on the evening's real prompt and context size (that is L4's dry week, still not run).
- Peak-hour pricing (the run was off-peak).
