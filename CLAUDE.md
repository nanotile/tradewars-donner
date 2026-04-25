# Tradewars

A battle arena where 4 LLM agents day-trade live US equities for a fixed duration. Each trader starts with $1,000,000, sees realtime market data through the Massive (Polygon) MCP, can persist notes via a per-trader Memory MCP, and competes against three rivals running different frontier models. The clock auto-liquidates everyone at the end and the highest portfolio value wins.

Built with the OpenAI Agents SDK (Python, async), FastAPI + SSE, a Vite + vanilla-TS frontend with uPlot charts, and packaged as a single multi-stage Docker image.

## Running locally

**Dev (fast inner loop):**
```bash
# terminal 1 — backend
uv run uvicorn --factory backend.api.app:create_app --port 8000

# terminal 2 — frontend (vite proxies /arena/* to :8000)
cd frontend && npm run dev
# → http://localhost:5173
```

**Container (single port, prod-like):**
```bash
./scripts/start_mac.sh     # builds image, runs with --env-file .env
# → http://localhost:8000
./scripts/stop_mac.sh
```

**Tests:**
```bash
uv run pytest                 # 82 unit tests (~4 s)
uv run pytest -m integration  # opt-in 90 s real arena via gpt-oss-120b
```

The integration test is gated behind the `integration` marker so it doesn't fire on every `pytest` run — it spawns real MCP subprocesses and hits OpenRouter + Massive.

**Required env vars** (in `.env` at repo root): `MASSIVE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`.

## Repo layout

```
backend/
  environment/   accounts.py (SQLite), prices.py (Massive REST)
  traders/       templates.py (system prompt), models.py (provider factory),
                 mcp_servers.py (MCP factories), tools.py (get_state/trade),
                 trader.py (decision-cycle loop)
  arena/         arena.py (lifecycle), config.json (model catalog + presets)
  api/           app.py (FastAPI: /arena/start|stop|tick|stream|config)
  test/          pytest suite + opt-in integration test
frontend/
  src/           api.ts, state.ts, theme.ts, topbar.ts, chart.ts (uPlot),
                 heatmap.ts, log.ts, panel.ts, main.ts, styles.css
  index.html     sidebar (clock + duration + slot pickers + presets +
                 start/stop) + 2×2 panels grid
  vite.config.ts dev proxy to :8000
scripts/         start_mac.sh, stop_mac.sh
Dockerfile       multi-stage (node frontend → python+uv runtime)
```

## Core architecture

### Decision-cycle loop (per trader)

Every trader runs a **continuous async loop of independent decision cycles**. One cycle = one `Runner.run_streamed(...)` until the model emits `final_output`. After each cycle, sleep `INTER_CYCLE_SLEEP_SECONDS` (10 s — the main cost throttle), then start a fresh `Runner.run_streamed`. **No conversation history accumulates across cycles** — each cycle is bounded to one run's worth of tokens. Per-cycle context is just the static system prompt + a one-line user message ("decision cycle N. Previous rationale: …. Take your next action."). Solves both context bloat over a long game and the SDK's "final_output ends the run" boundary in one pattern.

All 4 traders run concurrently via `asyncio.gather` with a shared `stop_event`. Mid-cycle exceptions log + emit an `error` event + back off 2 s + continue (DeepSeek 429s and similar transient failures don't kill the trader).

### Tools per trader

Two function tools (carried via `RunContextWrapper.context`):
- `get_state()` — own cash + holdings (with per-position avg cost, current price, market value, unrealized P&L) + total portfolio value + total P&L + time remaining + each rival's total portfolio value (only the total — no rival holdings leaked).
- `trade(ticker, quantity)` — fills synchronously at the live Massive quote. Fractional, no shorting.

Plus two MCP servers, **per trader** (isolated subprocesses + storage):
- **Massive MCP** — `mcp_massive` binary, three composable tools (`search_endpoints`, `call_api`, `query_data`) covering prices, news, technicals, fundamentals.
- **Memory MCP** — `npx -y @modelcontextprotocol/server-memory`, knowledge graph (entities/relations/observations), per-trader JSONL file wiped on every Start.

### Slot configuration & model catalog

The sidebar lets the user pick a model + reasoning effort per slot. Behaviour:

- **No slot label** — the dropdown showing the picked model IS the slot's identity.
- **Per-model reasoning options** — every model declares 1 or 2 reasoning options. Models with two show two buttons; models with one show that single button **disabled** (info, not interactive).
- **Reasoning labels stay technical** (`xhigh`, `max`, `low`, `none`, `off`, `32k`) — not normalised to "high/low" so users see exactly which knob is firing.
- **Duplicate models allowed.** When two slots resolve to the same `<display_name> (<reasoning_label>)`, the second gets ` #2`, third `#3`, etc.
- **Trader id (DB key, memory file, game history) = `<display_name> (<reasoning_label>)`** with that disambiguator, e.g. `Claude Opus 4.7 (max)` → memory file `trader_Claude_Opus_4.7_max.jsonl`.
- **Presets** — Eco / Max buttons fully **overwrite** all four slot selections. Eco is the UI default on first load.

`backend/arena/config.json` is a **catalog** of every model the UI can offer plus named **presets**:

```json
{
  "duration_seconds": 720,
  "max_tokens": 64000,
  "models": {
    "claude-opus-4-7": {
      "display_name": "Claude Opus 4.7",
      "provider": "anthropic",
      "model": "anthropic/claude-opus-4-7",
      "reasoning_options": [
        {"label": "max", "reasoning": {"effort": "max"}}
      ]
    },
    "gpt-5-5": {
      "display_name": "GPT 5.5",
      "provider": "openai",
      "model": "gpt-5.5",
      "reasoning_options": [
        {"label": "xhigh", "reasoning": {"effort": "xhigh"}},
        {"label": "low",   "reasoning": {"effort": "low"}}
      ]
    }
  },
  "presets": {
    "max": [{"model_id": "claude-opus-4-7", "reasoning_label": "max"}, ...],
    "eco": [...]
  }
}
```

`ArenaConfig.from_selections([{model_id, reasoning_label}, ...])` resolves selections to `TraderConfig`s with the disambiguated id. `GET /arena/config` returns the full catalog so the sidebar can populate dropdowns. `POST /arena/start` accepts `selections` (4 in slot order) or falls back to the `max` preset.

## Provider routing

| Provider | `build_model` returns | Reasoning knob channel | Config shape |
|---|---|---|---|
| `openai`     | plain model-id string        | `ModelSettings.reasoning=Reasoning(effort=...)`, omitted entirely when `effort="none"` | `{"effort": "xhigh"\|"high"\|...\|"none"}` |
| `anthropic`  | `LitellmModel`               | `extra_args={"thinking":{"type":"adaptive"},"output_config":{"effort":...},"cache_control":{"type":"ephemeral"}}` for Opus 4.7; `{"thinking":{"type":"enabled","budget_tokens":N}, "cache_control":...}` for Haiku/Sonnet | `{"effort": "..."}` (Opus, adaptive form) **or** `{"effort": "...", "budget_tokens": N}` (Haiku — `effort` is for the UI label, `budget_tokens` drives the API) |
| `google`     | `LitellmModel` (`gemini/` prefix) | `extra_args={"thinking":{"type":"enabled","budget_tokens":N}}` if `budget_tokens` is set, else `extra_args={"reasoning_effort":...}` | `{"budget_tokens": N}` **or** `{"effort": "..."}` |
| `deepseek`   | `OpenAIChatCompletionsModel` at `https://api.deepseek.com` | `extra_body={"reasoning_effort":...,"thinking":{"type":"enabled"\|"disabled"}}` (either or both) | `{"effort": "max", "thinking": {"type":"enabled"}}` for Pro, `{"thinking": {"type":"disabled"}}` for Flash |
| `openrouter` | `OpenAIChatCompletionsModel` at `https://openrouter.ai/api/v1` | `extra_body={"reasoning":{"effort":...}}` | `{"effort": "xhigh"\|"high"\|...}` |

### Critical reasoning-passthrough gotchas

- **Opus 4.7 and Haiku 4.5 use DIFFERENT thinking interfaces.** Opus 4.7 only accepts the new `thinking:{type:"adaptive"}` + `output_config.effort`. Haiku 4.5 (and Sonnet 4.x) still use the legacy `thinking:{type:"enabled", budget_tokens:N}`. Cross-sending either shape to the wrong model produces a clear API error. We branch on config shape: `{budget_tokens}` → enabled form; `{effort}` → adaptive form.
- **LiteLLM 1.83 blocks `effort="max"` for Opus 4.7** via a stale "4.6-only" validator. We monkey-patch `AnthropicConfig._is_opus_4_6_model` to accept 4.7 too. Drop the patch when LiteLLM 1.84+ ships.
- **`extra_args` vs `extra_body`** for LiteLLM-routed providers (Anthropic, Google): use `extra_args` (top-level kwargs to `litellm.acompletion`). `extra_body` would wrap them inside the request body and Anthropic rejects it.
- **Omit `temperature` entirely when Anthropic thinking is enabled** — anything other than the default (1.0) errors out.
- **OpenAI `effort="none"`** is a valid API value but isn't in the SDK's `Reasoning.effort` Literal on openai-python ≤ 2.x. We omit the `reasoning` field entirely in that case (the model's own default applies).

## Cost controls

- `max_tokens = 64_000` per trader so xhigh/max thinking has room without truncating.
- `MAX_TURNS_PER_CYCLE = 200` — reasoning models on a Massive deep-dive can hit 40+ turns.
- `INTER_CYCLE_SLEEP_SECONDS = 10.0` — caps cycles to ~6/min/trader. Without this an Opus-max game racks up serious spend in minutes.
- **Anthropic prompt caching** enabled via the top-level `cache_control: {type: "ephemeral"}` field (Anthropic's 2026 automatic caching mode). LiteLLM forwards it through. Confirm cache is firing by inspecting `result.usage.cache_creation_input_tokens` / `cache_read_input_tokens`.
- **OpenAI prompt caching** is automatic for prompts ≥1024 tokens — already firing for GPT.
- **Gemini** caching uses Google's separate CachedContent API; not wired up. Skip for now — Gemini's per-token cost is low and Anthropic was the bigger lever.
- The system prompt is **byte-identical across all cycles within a game** (computed once at Agent construction in `Trader.run_until_stopped`) — maximum cache hit rate.

## OpenAI Agents SDK patterns

### MCP stdio servers

- Use `agents.mcp.MCPServerStdio` as an async context manager.
- `client_session_timeout_seconds=60` — Massive needs >5 s on first start (it indexes the OpenAPI spec).
- `cache_tools_list=True` — don't re-list tools each decision cycle (also keeps tool descriptions stable for prompt caching).
- Pass env via `params={"env": {**os.environ, ...}}` — the child process doesn't inherit automatically.

### Streaming events

`Runner.run_streamed(agent, input, max_turns=N).stream_events()` yields `run_item_stream_event`s with three useful names:
- `tool_called` → `event.item.raw_item.name` is the tool name; `.arguments` is the JSON.
- `tool_output` → `event.item.raw_item["output"]` (or `.output`) is what to render.
- `message_output_created` → final assistant message; text is in `event.item.raw_item.content[i].text`.

Default `max_turns=10` is too low once MCP servers are wired — bump to 30+ (we use 200).

### Tracing

`set_tracing_disabled(True)` at startup when not using OpenAI's tracing backend — suppresses noisy 401s if `OPENAI_API_KEY` is unset or invalid.

## Tool output formatting

SDK-level `event.item.raw_item` for `tool_output` events arrives in two shapes:
- **MCP tools**: `[{"type": "input_text", "text": "..."}]` — a list of content parts.
- **Function tools** (`get_state`, `trade`): a plain dict.

`backend/traders/trader.py::_format_output` flattens both: strips MCP wrappers to the inner text, JSON-serialises dicts. Doing `str(raw_out)` would emit Python repr noise (`[{'type': 'input_text', 'text': '...'}]`) which is unreadable in the UI.

`frontend/src/log.ts` further humanises tool *calls* (e.g. `trade({"ticker":"INTC","quantity":-100})` → `sell 100 INTC`; `call_api({path, params, store_as})` → `path · k=v k=v`; memory ops `create_entities`/`add_observations`/`read_graph` → `remember X` / `note on X` / `read memory`) and compacts tool *outputs* ("Stored N rows in X" → `stored N rows → X`; endpoint listings → `N endpoints found`; empty responses → `no data`).

## Container shape

`Dockerfile` is multi-stage:
1. `node:22-alpine` builds the static frontend (`npm run build` → `dist/`).
2. `python:3.14-slim-trixie` is the runtime. It pulls `uv` from the official image, installs Node 22 (for `npx -y @modelcontextprotocol/server-memory`), `uv sync --frozen` for backend deps, and `uv tool install mcp_massive` from git. The built frontend is copied to `/app/frontend_dist`; FastAPI mounts that at `/` after the `/arena/*` routes register (mount order matters — API routes take precedence).
3. Serves on `0.0.0.0:8000` via `uv run uvicorn --factory backend.api.app:create_app`.

`scripts/start_mac.sh` and `stop_mac.sh` are the wrappers. The start script reads `.env` and passes it to the container via `--env-file` — secrets are NEVER baked in (see `.dockerignore`).

## Frontend gotchas (uPlot + Vite)

Three things bit us hard when wiring the chart panels:

1. **`tsc -b` was emitting `.js` next to `.ts` in `src/`.** Vite's dev server serves whichever file matches the import URL (`/src/panel.js`); when stale `.js` exist, Vite serves THEM instead of compiling the live `.ts`. Symptom: code changes visibly do nothing in the browser even after restarts. Fix: `noEmit: true` in `frontend/tsconfig.json` and `.gitignore` for `frontend/src/*.js`. **Never check those in.**

2. **uPlot misbehaves if its host is not in the DOM at `new uPlot(...)` time.** Internal initial-draw goes down a code path that subsequently throws `s.stroke is not a function` on every redraw, the canvas stays at its initial size, and you get a panel with axes but no line. `TraderPanel` defers chart creation to a `mount()` method that `main.ts` calls AFTER `panelHost.append(panel.root)`.

3. **Don't mutate `series.stroke` to a string after init** to flip the line color (e.g. green/red on P&L sign). uPlot caches the value in a way that breaks subsequent renders. Use a stroke callback instead: `stroke: (u) => u.data[1].at(-1) >= initial ? "#3fbf7f" : "#e05560"`. Same `s.stroke is not a function` symptom.

## Plan & decisions

@PLAN.md
