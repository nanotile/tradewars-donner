# Tradewars

A battle arena where 4 LLM agents day-trade live US equities for a fixed
duration (default 12 minutes; configurable up to 4 hours). Each starts with
$1,000,000, sees realtime market data through MCP tools, and competes against
three rival agents running different frontier models. The winner is the
highest portfolio value once the clock auto-liquidates everyone to cash.

## Game rules

- Game length: configurable in the UI (default 12 min). Wall-clock.
- Each trader starts with **$1,000,000**.
- Fractional shares allowed. **No short selling.**
- No commission, no spread, **no slippage** — orders fill at the latest
  Massive (Polygon) quote.
- Assume the US market is open or in after-hours (Massive supports both).
- At the end of the game the arena auto-liquidates every open position at the
  then-current quote, so each trader is scored in cash. Traders are told this
  in their initial prompt.

## What was built

### Decision-cycle agent loop

- Each trader runs a **continuous async loop** of independent decision cycles.
  Cycle = one `Runner.run_streamed(...)` until the model emits `final_output`.
- Between cycles the harness sleeps `INTER_CYCLE_SLEEP_SECONDS` (10 s — the
  main cost throttle) and then starts a fresh `Runner.run_streamed`.
- **No conversation history accumulates across cycles.** Each cycle is bounded
  to a single `Runner.run` worth of tokens. This solves both context bloat
  over a long game and the SDK's "final_output ends the run" boundary in one
  pattern.
- Per-cycle context is: shared system prompt (static for the whole game) +
  one user message ("decision cycle N. Previous rationale: …. Take your next
  action."). The agent itself can call `get_state` and the Memory MCP to pull
  whatever else it needs.
- All 4 trader tasks run concurrently via `asyncio.gather`; they share an
  arena-level `stop_event` that they check between cycles. Errors mid-cycle
  log + emit an `error` event + back off 2 s + continue (DeepSeek 429s and
  similar transient failures don't kill a trader).

### Tools exposed to each trader

Two function tools (carried via the SDK's `RunContextWrapper.context`):

- `get_state()` → time elapsed/remaining, own cash, own holdings (with
  per-position avg cost / current price / market value / unrealized P&L),
  total portfolio value, total P&L, **and just the total portfolio value of
  each rival** — no rival holdings leaked.
- `trade(ticker, quantity)` → fills synchronously at the live Massive quote.
  Positive = buy, negative = sell, fractional allowed, no shorting. Returns
  a structured success/error dict.

Plus two MCP servers, **per trader** (so each gets its own subprocess + state):

- **Massive MCP** (`mcp_massive`, installed via `uv tool install`). Three
  composable tools — `search_endpoints`, `call_api`, `query_data` — covering
  prices, news, technicals, fundamentals.
- **Memory MCP** (`@modelcontextprotocol/server-memory` via `npx`). Knowledge
  graph (entities/relations/observations). Per-trader `MEMORY_FILE_PATH`,
  wiped on every game Start.

The system prompt encourages — but does not require — the model to use the
Memory MCP for cross-cycle notes. The system-curated rolling rationale (last
cycle's `final_output`) is always injected; the model-curated memory MCP is
pulled in when the model chooses.

### Model line-up & catalog

`backend/arena/config.json` is a **catalog** of every model the UI can offer
plus two named **presets**. The user composes a per-slot line-up in the
sidebar; presets snap all four slots at once. Models we support today:

| Model | Provider | Reasoning options |
|---|---|---|
| Claude Opus 4.7 | Anthropic (LiteLLM) | `max` |
| Claude Haiku 4.5 | Anthropic (LiteLLM) | `low` |
| GPT 5.5 | OpenAI native | `xhigh` / `low` |
| GPT 5.4-mini | OpenAI native | `none` |
| Gemini 3.1 Pro Preview | Google AI Studio (LiteLLM) | `32k` / `low` |
| Gemini 3.1 Flash-Lite Preview | Google AI Studio (LiteLLM) | `low` |
| Kimi K2.6 | OpenRouter | `xhigh` / `low` |
| DeepSeek V4 Pro | DeepSeek native (OpenAI-compat) | `max` |
| DeepSeek V4 Flash | DeepSeek native (OpenAI-compat) | `off` |

**Presets:**
- **Eco** (UI default) — Haiku / GPT-mini / Flash-Lite / DeepSeek Flash
- **Max** — Opus / GPT-5.5 (xhigh) / Gemini Pro (32k thinking) / DeepSeek V4 Pro

All 4 traders share the **identical system prompt** — the contest is pure
model-vs-model, no persona differentiation.

### Trader identity

Traders are identified by `<display_name> (<reasoning_label>)` (e.g.
`Claude Opus 4.7 (max)`). When two slots resolve to the same label, the
second one gets ` #2`, third `#3`, etc. — so an all-Kimi face-off is well-defined.
This id flows everywhere: DB rows, memory file (sanitised for filesystem),
SSE events, `games.final_results`, and the panel header in the UI.

### Provider routing

5 routes selected by `TraderConfig.provider`:

- `openai` → plain model id, `ModelSettings.reasoning=Reasoning(effort=...)`,
  or omitted entirely when `effort="none"`.
- `anthropic` → `LitellmModel`. Branches on config: `{effort: "..."}` →
  adaptive thinking + `output_config.effort` (Opus 4.7 only); `{budget_tokens: N}`
  → legacy `enabled` thinking (Haiku/Sonnet 4.x). Also includes a top-level
  `cache_control: {type: "ephemeral"}` (Anthropic 2026 automatic caching).
- `google` → `LitellmModel` with the `gemini/` prefix. Prefers explicit
  `thinking: {type: "enabled", budget_tokens: N}`; falls back to LiteLLM's
  unified `reasoning_effort` knob.
- `deepseek` → OpenAI-compat client at `https://api.deepseek.com`. Reasoning
  knobs (`reasoning_effort`, `thinking`) go into `extra_body`.
- `openrouter` → OpenAI-compat client at `https://openrouter.ai/api/v1` with
  `extra_body={"reasoning": {"effort": "..."}}`.

### Cost controls

Burn-rate management is layered:

- `max_tokens = 64_000` per trader so xhigh/max thinking has room without truncating.
- `MAX_TURNS_PER_CYCLE = 200` — reasoning models on a Massive deep-dive can hit 40+ turns easily.
- `INTER_CYCLE_SLEEP_SECONDS = 10.0` — caps cycles to ~6/min/trader. Without it a 1-hour Opus-max game racks up *hundreds* of cycles.
- **Prompt caching enabled** for Anthropic via the top-level `cache_control` field. OpenAI Responses API caches automatically. Gemini's CachedContent is a separate API and is skipped for now.
- The system prompt is **byte-identical across all cycles within a game** (computed once at Agent construction), maximising cache hit rate.

### Architecture & lifecycle

```
backend/
  environment/   accounts.py (SQLite), prices.py (Massive REST)
  traders/       templates.py (system prompt), models.py (provider factory),
                 mcp_servers.py (MCP factories), tools.py (get_state/trade),
                 trader.py (decision-cycle loop)
  arena/         arena.py (lifecycle), config.json (model catalog + presets)
  api/           app.py (FastAPI: /arena/start|stop|tick|stream|config)
  test/          82 unit tests + 1 opt-in integration test
frontend/
  src/           api.ts, state.ts, theme.ts, topbar.ts, chart.ts (uPlot),
                 heatmap.ts, log.ts, panel.ts, main.ts, styles.css
  index.html     sidebar (clock + duration + 4 slot pickers + presets +
                 start/stop) + 2×2 panels grid
  vite.config.ts dev proxy to :8000
scripts/         start_mac.sh, stop_mac.sh
Dockerfile       multi-stage (node frontend → python+uv runtime)
```

**Arena lifecycle:**
- `start()` wipes the working accounts tables + memory files, creates fresh
  $1M traders, launches 4 trader tasks via `asyncio.gather`, spawns an
  auto-end timer task.
- `tick()` refreshes prices for all held tickers (across all 4 traders) and
  returns a snapshot.
- `end()` is **always-liquidate**, idempotent under an `asyncio.Lock`. Manual
  Stop and the auto-end timer share the same code path; whichever fires first
  wins, the other is a no-op. Liquidation falls back to the last tick price
  cache if Massive hiccups.
- Game history persisted to a `games` table — keyed by trader id with final P&L.

**API endpoints (FastAPI on :8000):**
- `GET /arena/config` — catalog + presets + duration/max_tokens
- `POST /arena/start` — accepts `{duration_seconds?, selections?}`; defaults to `max` preset
- `POST /arena/stop` — final snapshot (idempotent)
- `POST /arena/tick` — current snapshot (UI heartbeat at 1 Hz)
- `GET /arena/stream` — SSE channel of `TraderEvent`s (cycle_start/end, tool_called, tool_output, message, error, liquidation)

**Frontend (Vite + vanilla TS, no framework):**
- Dark mode default, light toggle (sun/moon SVG icon, no emoji).
- Sidebar: brand, MM:SS clock, duration input, 4 slot pickers (model dropdown
  + reasoning button row), Eco/Max preset buttons, Start/Stop, theme toggle.
- 2×2 panels grid. Each panel: trader header (`Model Name (effort)` in bold
  white/black, big centered $value below), uPlot line chart (X=minutes,
  Y=portfolio value with auto-pad), holdings heatmap (tile size = market
  value, color = P&L, flashes green/red on price tick), bounded SSE log.
- 1 Hz `/arena/tick` driving prices + chart + heatmap; SSE driving the log.
- Color palette `#ecad0a` / `#209dd7` / `#753991` + greys; no gradients.

### Containerisation

Single multi-stage `Dockerfile`:
1. `node:22-alpine` builds the static frontend.
2. `python:3.14-slim-trixie` runs the backend with `uv` + Node 22 (for the
   Memory MCP via `npx`) + `mcp_massive` installed from git.
3. FastAPI mounts `frontend_dist/` at `/` after the `/arena/*` routes register.
4. Listens on `0.0.0.0:8000`.

Secrets are passed via `--env-file .env` at run time, never baked in.
`scripts/start_mac.sh` and `stop_mac.sh` wrap `docker build` + `docker run`.

### Tests

- **82 unit tests** across accounts, prices, tools, models, arena lifecycle,
  arena config, API routes. Run by default in ~4 s.
- **1 integration test** (opt-in via `pytest -m integration`) — 90 s real
  arena with 4 traders on the cheap `openai/gpt-oss-120b` model, real MCPs,
  real Massive. Validates concurrent lifecycle + liquidation + game history +
  event flow end-to-end.

## Key technical decisions

- **OpenAI Agents SDK as the unifying agent framework.** Native paths for
  OpenAI; `LitellmModel` extension for Anthropic + Google; OpenAI-compat
  clients for OpenRouter + DeepSeek.
- **MCP servers are per-trader.** Isolated subprocesses, isolated memory
  files, no cross-trader leakage even at the OS level.
- **Runtime trader id is human-readable** (`"Claude Opus 4.7 (max)"`).
  Disambiguated with `#N` suffix when the same model+effort appears in
  multiple slots. Memory files use a sanitised version of the same id.
- **UI drives the cadence.** The frontend ticks `/arena/tick` at 1 Hz; the
  backend has no scheduler beyond the per-trader async loops + the auto-end
  timer.
- **Liquidation is always-on at game end** (manual Stop and auto-end behave
  identically) and falls back to the last cached price if Massive returns no
  quote for a held ticker.

## Deferred / future

- Playwright MCP integration for web browsing (dropped to keep the Docker
  image slim for fly.io deployment).
- fly.io deployment (Dockerfile is ready, just needs a `fly.toml`).
- Past-games view in the UI reading from the `games` history table.
- Richer MCP sub-tool tracing in the trader log.
- Auto-detect and recover from a long client-disconnect (currently the game
  runs to its scheduled end whether or not the UI is watching).
