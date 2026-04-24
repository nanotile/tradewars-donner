# Tradewars

## Introduction

A battle arena for 4 LLM agents (the "traders") to compete in a simulated equity day-trading environment over a 1-hour time limit. Developed with Claude Code.

## Rules

- Game length: 1 hour, wall-clock.
- Each trader starts with $1,000,000.
- Fractional shares allowed. No short selling.
- No commission, no bid/offer spread, **no slippage** — any order, regardless of size or ticker liquidity, fills entirely at the latest Massive quote.
- Assume the US market is open (or after-hours, which Massive supports). No special handling for closed markets in v1.
- At the end of the hour, the arena auto-liquidates all open positions at the latest Massive quote so each trader ends in 100% cash. Traders are told this in their initial prompt.

## Traders

### Models (all via OpenAI Agents SDK)

The OpenAI Agents SDK has first-class LLM abstractions and a LiteLLM-based fallback. We use the SDK's native integrations uniformly for all four models, falling back to LiteLLM only if needed.

| # | Trader | Model | Reasoning |
|---|--------|-------|-----------|
| 1 | Claude | `claude-opus-4-7` (Anthropic) | max |
| 2 | OpenAI | `gpt-5.4` (OpenAI) | xhigh |
| 3 | Kimi | `moonshot/kimi-k2.6` (OpenRouter) | highest |
| 4 | DeepSeek | `deepseek/deepseek-v4-pro` (OpenRouter) | highest |

Note: DeepSeek V4 Pro was released today (2026-04-24) and may still be provisioning providers on OpenRouter. Handle provider-unavailable gracefully.

Reasoning-effort passthrough differs by provider. We'll use the Agents SDK's model settings / extra-body mechanism per model. If the SDK native path can't express a given provider's knob, we fall back to LiteLLM for that trader only.

**Target maximum reasoning per provider:**
- Claude Opus 4.7 → Anthropic's new `"max"` thinking mode.
- GPT 5.4 → OpenAI's `"xhigh"` reasoning effort.
- Kimi K2.6 → OpenRouter's highest available for that model.
- DeepSeek V4 Pro → OpenRouter's highest available for that model.

Exact knob names and values will be verified against current provider docs during Phase 2 (see Build Phases). Anthropic also requires `max_tokens` — set per trader in config to a large value (e.g. 32k).

### MCP servers

- **Massive MCP** (official stdio server, formerly Polygon.io). Latest version is the short/simple one. Provides realtime + historic equity prices, news, technical and fundamental analysis. `MASSIVE_API_KEY` from `.env`.
- **Memory MCP** (official Anthropic `@modelcontextprotocol/server-memory` — knowledge-graph memory: entities, relations, observations). One stdio instance **per trader**, each with an isolated storage file at `backend/environment/memory/trader_{id}.json` (set via `MEMORY_FILE_PATH` env var). Memory files are **wiped on arena Start** (consistent with the account tables) and `.gitignore`d. This gives each model persistent state across decision cycles within a game — a natural complement to the compact rolling memory we inject into each cycle.
- **Playwright MCP — deferred.** Dropped from v1 to keep the Docker image slim for future fly.io deployment (Chromium-in-container is heavy and awkward on fly). Re-add later if web browsing proves valuable.

### Tools exposed to each trader

- `get_state()` → time elapsed, time remaining, own total portfolio value, own cash, own holdings with per-position P&L, own total P&L, and **only the total portfolio value** of each rival (to stoke competition without leaking rivals' holdings).
- `trade(ticker, quantity)` → positive = buy, negative = sell. Fills synchronously at the current Massive quote.

### Agent loop

- Each trader runs a **continuous async loop** (not tick-driven). LLMs spend most of their time thinking, so a tight loop is fine.
- All 4 loops run concurrently via `asyncio.gather`.
- The arena holds a cancellation signal; loops check it between iterations and on clock end.
- On error mid-loop: log it, short sleep, continue. Don't kill the trader.
- **No per-iteration safety cap**, but Anthropic's API requires `max_tokens` — we set it per-model in the trader config (sensible default, large enough not to bite).

### Decision-cycle architecture (solves context growth + `final_output`)

A naive "one giant conversation" approach hits two problems: (a) context grows unboundedly over an hour of tool calls, (b) the Agents SDK ends a run when the LLM produces a non-tool `final_output`. We handle both with an **episodic decision-cycle loop**:

- One **decision cycle** = one `Runner.run()`. Inside the cycle, the SDK handles the tool-call → tool-result → next-turn loop automatically until the LLM emits a `final_output` (its decision rationale).
- Between cycles, we **start a fresh `Runner.run()`** — so history does not accumulate across cycles. Context is bounded to one cycle's worth of tool calls.
- We maintain a compact **rolling memory** per trader, kept outside the Agent, containing:
  - Recent trades (ticker, qty, price, timestamp) — last ~20.
  - Previous cycle's `final_output` rationale (1–2 sentences).
  - Current full `get_state()` snapshot.
- In addition, each trader has a **persistent memory MCP** (see MCP servers above) for richer state the model curates itself — hypotheses, watchlists, conviction notes, observations about rivals. The rolling memory is *system-curated* and always injected; the MCP memory is *model-curated* and pulled in only when the model chooses. The two don't overlap.
- Each cycle's input is: system prompt (static) + rolling memory (compact) + "it is now HH:MM:SS into the hour, decide your next move."
- `final_output` is **expected and fine** — it naturally closes one cycle. The outer loop immediately starts the next cycle.
- Between cycles, a small configurable delay (default 0s — cycles are already slow due to reasoning).

This keeps each cycle's context small, makes `final_output` a feature not a bug, and exposes natural per-cycle event boundaries for the SSE stream.

### Prompting

- System prompt explains: simulation nature, rivals, rules, $1M start, 1-hour clock, fractional shares allowed, no shorting, auto-liquidation at end, available tools, and MCP servers.
- Prompt **mentions the memory MCP as a useful tool** for tracking state, hypotheses, and observations across the 1-hour timeline — but does not mandate how to use it.
- Prompts live in `backend/traders/templates.py`.

## Use of OpenAI Agents SDK

- Idiomatic SDK patterns per the official OpenAI docs.
- MCP servers via the SDK's async context manager.
- `trader_id` carried in the Agents SDK `ctx` object, used by `get_state()` and `trade()`.
- Fully async.
- Stream every tool call and decision out of the loop (see Streaming below).

### Streaming + traces (Issue 3)

The Agents SDK exposes tool-call events. For MCP sub-tool calls, the per-tool granularity depends on SDK version. Approach:
1. Investigate whether the current SDK surfaces MCP sub-tool calls cleanly.
2. If yes: stream them into the trader log with tool name + args summary.
3. If no: fall back to "MCP server call" granularity for v1. Deeper per-tool tracing via the SDK's custom-traces API is a future enhancement.

## Architecture

```
tradewars/
├── backend/
│   ├── environment/    # accounts DB, Massive REST price lookups
│   ├── traders/        # mcp_servers.py, trader.py, templates.py
│   ├── arena/          # arena lifecycle, trader config, start/stop
│   ├── api/            # FastAPI app (SSE endpoint + tick endpoint)
│   └── test/           # pytest suite
├── frontend/           # Vite vanilla TS
├── scripts/            # start_mac.sh, stop_mac.sh
├── Dockerfile
├── .env
└── PLAN.md
```

### `backend/environment`

- Accounts persisted in a local SQLite DB keyed by `trader_id`. DB file lives in this directory and is `.gitignore`d.
- `accounts.py` to be rewritten from scratch (ignore the reference implementation present in this directory).
- Working account tables are **wiped on every arena Start** — no cross-session portfolio state in v1.
- A separate append-only **`games` history table** records each completed game: start time, end time, duration, and final per-trader P&L. Not wiped — supports a future "past games" view.
- Massive REST client for price lookups (used by `trade()` execution and by the per-second tick price refresh). `MASSIVE_API_KEY` from `.env`. No rate-limit concerns — pro plan is effectively unlimited.

### `backend/traders`

- `mcp_servers.py` — factories for Massive + Playwright stdio MCP servers.
- `trader.py` — single-trader agent loop.
- `templates.py` — system/user prompt templates.

### `backend/arena`

- Arena lifecycle: `start()` resets the working account tables, launches 4 trader tasks via `asyncio.gather`, starts the 1-hour clock. `end()` cancels trader loops, **always liquidates** all open positions at current Massive quotes (manual Stop and auto 60:00 end behave identically), then records final P&Ls to the `games` history table.
- If Massive cannot return a quote for a held ticker at liquidation (API error, halted symbol), fall back to the **last price observed by the tick loop** for that ticker. Persist a short price cache in memory keyed by ticker, updated every tick.
- Trader configuration lives in **`backend/arena/config.json`**: display name, model id, provider, reasoning effort, `max_tokens`. Names ("Claude", "GPT", "Kimi", "DeepSeek") drive UI labels and log identity. **All 4 traders share the identical system prompt** — the contest is pure model-vs-model, no persona differentiation.
- Holds the per-trader event streams consumed by the SSE endpoint.

### `backend/api`

- FastAPI.
- `POST /arena/start` — reset + start.
- `POST /arena/stop` — manual end.
- `POST /arena/tick` — UI heartbeat: refresh all portfolio prices from Massive and return current PnL snapshot for all 4 traders.
- `GET /arena/stream` — **SSE** channel pushing trader events (tool calls, decisions, trades, errors) from all 4 loops.

### `backend/test`

- Rigorous pytest coverage.
- Tests may use the real OpenRouter API (model `openai/gpt-oss-120b`, cheap) and real Massive API with live keys from `.env`.

### `frontend` (Vite + vanilla TS)

The UI drives the process via 1 Hz ticks so the backend doesn't need a long-running scheduler. **Dark mode is the default**; a toggle switches to light.

Uses a small chart library (**uPlot**) rather than hand-rolled SVG — stays lightweight, keeps the TS minimal.

**Top bar:**
- Start button (resets portfolios, begins the hour).
- Stop button (manual end; auto-end at 60:00).
- Large countdown clock.
- Dark / light mode switch.

**Body — 2×2 grid, one panel per trader:**
- Large portfolio-value display, green if P&L ≥ 0 else red.
- Line chart of portfolio value over the hour, populating left-to-right. Downsample to keep point count bounded (~300 points over the hour, i.e. one point per ~12s averaged from 1 Hz ticks).
- Key facts: cash, P&L.
- Heatmap of current holdings — tile size = position value, color = per-position P&L. **On every tick, each ticker whose price changed flashes briefly bright green (up) or bright red (down), then decays back to its baseline color.** This gives the UI the "live trading floor" pulse.
- Bounded log trace (e.g. last 100 entries) of tool calls and decisions, fed by SSE.

**Tick loop:** UI sends `POST /arena/tick` every 1 second. Tick refreshes prices for all unique tickers across all 4 portfolios and returns the updated snapshot; UI appends to each chart and redraws.

**Streaming:** UI opens a single SSE connection to `/arena/stream` on Start; appends each event to the correct trader's log panel.

### Color scheme

- Accent yellow: `#ecad0a`
- Blue primary: `#209dd7`
- Purple secondary: `#753991` (submit buttons)
- Elegant greys otherwise. Avoid gradient overuse and other LLM-aesthetic tells. No emojis anywhere.

### `scripts`

- `start_mac.sh` — build + run the Docker container.
- `stop_mac.sh` — stop the container.
- Single `Dockerfile` at the project root. Statically compiled frontend served at `/`; backend is a uv project.

## Environment variables (`.env`)

- `MASSIVE_API_KEY` — Massive / Polygon.
- `OPENAI_API_KEY` — GPT 5.4.
- `ANTHROPIC_API_KEY` — Claude Opus 4.7.
- `OPENROUTER_API_KEY` — Kimi K2.6 and DeepSeek V4 Pro.

## Build phases

Each phase must validate before moving to the next — small, incremental steps.

### Phase 1 — Preliminaries ✅ complete
- `backend/environment/accounts.py` — SQLite-backed store with `accounts` / `holdings` / `trades` / `games` tables. Fractional shares, no shorting, blended avg cost on buys, per-trader isolation, `reset_working_state()` wipes traders but preserves games history.
- `backend/environment/prices.py` — thin wrapper over the official `massive` Python client. Sync + async (`asyncio.to_thread`) variants. Pulls `MASSIVE_API_KEY` from `.env`.
- `backend/test/test_accounts.py` (20 tests, in-memory SQLite).
- `backend/test/test_prices.py` (6 tests against the live Massive API).
- `pyproject.toml` pytest config (`asyncio_mode = "auto"`, testpaths).
- `.gitignore` excludes `backend/environment/*.sqlite*` and `backend/environment/memory/`.
- **26/26 tests green.** Confirmed live prices flowing from Massive.

### Phase 2 — OpenAI Agents SDK prototyping ✅ mostly complete
- Single-trader standalone prototype at `backend/traders/prototype.py` — one decision cycle, both MCPs wired via `MCPServerStdio` async context.
- **MCP stdio commands confirmed:**
  - Massive: `mcp_massive` (installed via `uv tool install "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.9.1"`). Env: `MASSIVE_API_KEY`. Three composable tools: `search_endpoints`, `call_api`, `query_data`.
  - Memory: `npx -y @modelcontextprotocol/server-memory`. Env: `MEMORY_FILE_PATH` pointing to the per-trader JSONL file.
  - `client_session_timeout_seconds=60` needed on first Massive start (it indexes the OpenAPI spec from llms-full.txt).
- **All four agent capabilities exercised live:**
  - (a) prices — AAPL snapshot via `call_api` + `query_data`
  - (b) news — NVDA headlines via `/v2/reference/news`
  - (c) technicals — 20-day SMA via `/v1/indicators/sma/{ticker}`
  - (c) fundamentals — market cap via ticker overview + `stocks/financials/v1/ratios` is discoverable
  - (d) memory — `create_entities` → `add_observations` → `read_graph` round-trip, and observations persist across cycles (the second run read back the first run's note).
- **Streaming confirmed:** `Runner.run_streamed(...).stream_events()` surfaces `run_item_stream_event` with `tool_called`, `tool_output`, and `message_output_created` — sufficient for the future SSE trader log.
- **`set_tracing_disabled(True)`** at prototype startup — we aren't using OpenAI's tracing backend, so suppress the noisy 401s.
- **Reasoning-effort passthrough (via OpenRouter):** all 4 models routed through the same OpenAI-compatible client at `https://openrouter.ai/api/v1`. Config is uniform: `ModelSettings(extra_body={"reasoning": {"effort": "xhigh"}, "include_reasoning": True})`. OpenRouter maps `xhigh` to ~95% of `max_tokens` as reasoning budget; this is our "max reasoning per provider" setting.
  - Claude Opus 4.7 (`anthropic/claude-opus-4-7`): ✅ extended thinking activated (455 reasoning tokens, answered the primality test correctly).
  - GPT 5.4 (`openai/gpt-5.4`): ✅ 16,000 reasoning tokens — hit the `max_tokens=16000` ceiling.
  - Kimi K2.6 (`moonshotai/kimi-k2.6`): ✅ 9,478 reasoning tokens — also hit the ceiling on completion.
  - DeepSeek V4 Pro (`deepseek/deepseek-v4-pro`): ⚠️ reachable but upstream-rate-limited (429) — expected, released today. Monitor until providers come online; no code changes needed.
- **`max_tokens` default:** `64_000` across all traders to avoid truncation at xhigh reasoning (xhigh reserves ~95% as thinking budget). Tunable per-trader in `config.json`.
- **Note:** `thinking: {type: "enabled", ...}` (Anthropic's native field) does NOT pass through OpenRouter's OpenAI-compat layer. Use the unified `reasoning: {effort: ...}` form.

### Phase 3 — Game tools ✅ complete
- `backend/traders/tools.py` — `TraderContext` dataclass (trader_id, accounts, prices, clock, rival_ids) carried through `RunContextWrapper.context`. Plain-async `get_state_impl` / `trade_impl` keep the logic directly testable; `@function_tool`-wrapped `get_state` / `trade` are the agent-facing surface.
- `get_state` returns time elapsed/remaining, cash, holdings with per-position avg_cost/current_price/market_value/unrealized_pnl, total portfolio value, total P&L, and each rival's total portfolio value (only the total — no rival holdings leaked).
- `trade(ticker, quantity)` fills synchronously at the current Massive quote. Positive buys, negative sells, fractional allowed, no shorting. Returns a structured `{success, ticker, quantity, price, side, cash_after}` on success or `{success: False, error}` on insufficient cash / oversell / zero quantity.
- `backend/test/test_tools.py` — 12 tests with a `FakePrices` stub (no network).
- `backend/traders/prototype_tools.py` — integration prototype: real Agent, live Massive prices, in-memory DB, post-run assertions confirm trades actually mutated state.
- Test suite: 38/38 green.

### Phase 4 — Trader + arena
- `backend/traders/trader.py` — single-trader decision-cycle loop (runs until arena signals end).
- `backend/traders/mcp_servers.py` — per-trader MCP factories.
- `backend/traders/templates.py` — shared system prompt.
- `backend/arena/arena.py` — lifecycle (start/tick/end), `asyncio.gather` over 4 traders, clock, liquidation, per-trader event streams.
- `backend/arena/config.json` — trader names + models + reasoning + max_tokens.

### Phase 5 — Integration test ✅ complete
- `backend/test/test_arena_integration.py` — 90s real arena, 4 traders on `openai/gpt-oss-120b` via OpenRouter, live Massive prices, real MCPs. Opt-in via the `integration` pytest marker (`uv run pytest -m integration`) so the default suite stays fast.
- Passed in 95s on the first run: 4 concurrent trader tasks, mid-arena tick snapshots, end-of-game liquidation to cash, exactly 1 row in `games` history, events from all 4 traders flowing through the arena queue (including `cycle_start`).

### Phase 6 — API layer
- FastAPI app: `POST /arena/start`, `POST /arena/stop`, `POST /arena/tick`, `GET /arena/stream` (SSE).
- Smoke-test via `curl` — start an arena, observe SSE, tick, stop.

### Phase 7 — Frontend
- Vite + vanilla TS, uPlot for charts.
- Dark-default with light toggle.
- 2×2 trader panels with value / chart / key facts / holdings heatmap / log trace.
- SSE client for live logs; 1 Hz tick client for price/PnL refresh.
- Drive a real arena from the UI end-to-end.

### Phase 8 — Docker
- Single `Dockerfile` at project root. Multi-stage: build the static frontend, install the uv-managed backend, serve frontend at `/` and backend routes from the same FastAPI app.
- Run the full arena inside the container locally, verify parity with Phase 7.

### Phase 9 — Scripts + polish
- `scripts/start_mac.sh`, `scripts/stop_mac.sh`.
- Final end-to-end test of a full 1-hour arena via the container.
- **Delete throwaway probe scripts** from Phases 2–3: `backend/traders/prototype.py`, `backend/traders/reasoning_probe.py`, `backend/traders/native_probe.py`, `backend/traders/prototype_tools.py`. Their lessons are already captured in CLAUDE.md and PLAN.md.

### Deferred (future phases)
- Playwright MCP integration for web browsing.
- fly.io deployment.
- Past-games view in UI reading from the `games` history table.
- Richer MCP sub-tool tracing in the trader log.

## Open questions

None currently — all prior questions resolved into the sections above. Add here as they arise.
