# ADR-0005: One decision engine; the agent layer joins as a registered prediction family

Date: 2026-10-08
Status: Accepted (direction); the gates that decide how far it goes are in
`docs/specs/v2/openalpha-cn-v2-p7-decision-chain.md` §5 and may be revised there.

## Context

The repository carries two decision chains (measured 2026-10-08 at `1d7b4a3`):

- **Chain one** (`shortlist run` → `run_cycle` per security → optional `deliberate` →
  `portfolio construct`). It is not automatic: `shortlist run` never calls `run_cycle`
  (`shortlist_view.py:518`), the agents' output does not reach ranking, admission or weights
  (`portfolio_policy.py:508-530` reads only `subject`, `rank` and `score`), no shipped path calls a
  model, and the event evidence it reasons over (limit-up, broken board, consecutive board, theme,
  disclosure, capital flow) has no shipped producer.
- **Chain two** (`scripts/daily_selection.py`, P6). It ranks with a registered score source,
  rebalances by its own rule and registers a prediction before the next session opens. Its
  one pre-registered holdout did not pass.

The two chains share execution primitives and the stored factor tiers, and duplicate scoring, factor
orientation and `MarketBar` assembly. The duplication is in code, not in daily compute: chain one is
not run daily. The real gap is that the agent layer has neither a production input nor an output
that reaches a position.

Two further measurements shape the decision. The agent layer has never been tested as a selection
strategy, so nothing is known about whether it adds anything. And a portfolio-level excess return is
close to untestable on a horizon of a few years (an information ratio of 0.5 needs about sixteen
years to reach t = 2), whereas a prediction-level measurement (rank IC, decile spread, incremental IC
over the factor score) is testable in months.

## Decision

1. **Chain two is the single decision engine.** Positions, rebalancing and the forward registration
   come from it. The two chains are not merged by moving chain two's logic into chain one.
2. **The agent layer enters as a registered prediction family** (`AlphaModelDeclaration.family =
   "agent_*"`, free-string, the precedent being `strategy_<kind>` in
   `strategy_registration.py:219-245`): one finite `score` or one `abstention` per security, stamped
   by the store's own clock, `standing = forward`. It does not trade and does not change a position
   until a gate in the P7 specification says so.
3. **The agent layer is evaluated at the prediction level, forward only.** Its value is the
   incremental (partial) IC over the factor score at short horizons. A language-model signal is never
   replayed or backtested over history, because the model may have seen the period after the one it
   is asked about; a replay or backtest that meets one is refused by name.
4. **Model calls are stored, not re-run.** Calls use temperature 0 and every raw response is stored
   content-addressed together with the model id and a prompt-version hash; a replay reads the stored
   response. Byte-identical re-calls are not claimed.
5. **Order of work is Step 0 and plan C in parallel, then plan A (veto layer), then plan B (evidence
   as model features)**, with a gate before each later stage. The order and the gates are revisable
   only through the revision log in the P7 specification.

## Consequences

- No second engine, no second registration path. Chain one's ranking and portfolio commands stay in
  place and are not extended.
- The strategy specification and the prediction family set change; these are contract changes and
  go through the migration mechanism (`V2-P0B-004`), batched into one window (hard rule 3).
- Anything that changes `src/` or `scripts/daily_selection.py` invalidates the current registration's
  code binding. P7 therefore re-registers once, after the code changes land (`V2-P7-006`), and the
  daily registration is paused until then.
- The agent layer's rule-based weights are hand-set and unvalidated; they are measured like any
  other signal and are given no credit in advance.
- If the gate after plan C finds no incremental information, plan A and plan B are not built, and
  this ADR is revised to say so.
