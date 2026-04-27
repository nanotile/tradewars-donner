# Tradewars

Four LLM agents day-trade live US equities against each other in a fixed-length arena. Each starts with $1,000,000 and trades through the same Massive (Polygon) market data. When the clock runs out the arena auto-liquidates everyone to cash and the highest portfolio value wins.

## Game rules

- Game length is configurable in the sidebar. Default is 12 minutes.
- Every trader starts with $1,000,000.
- Fractional shares allowed. No short selling.
- No commission, no spread, no slippage. Orders fill at the latest Massive quote.
- US market should be open or in after-hours when you start.
- At the end of the game any open positions are auto-sold at the then-current quote, so each trader is scored in cash.

## What the traders see

Each trader runs in its own decision-cycle loop and has access to:

- `get_state` and `trade` function tools for game state and execution.
- Massive MCP for prices, news, technicals and fundamentals.
- A per-trader Memory MCP (knowledge graph) that persists notes across cycles within the game.

The system prompt is identical for all four traders, so the contest is pure model versus model.

## Models you can pick

The sidebar lets you compose the line-up per slot, with two named presets you can snap to:

- **Eco** (default): Claude Haiku 4.5, GPT 5.4-mini, Gemini 3.1 Flash-Lite, DeepSeek V4 Flash. Cheap, fast, low or no reasoning.
- **Max**: Claude Opus 4.7 (max), GPT 5.5 (xhigh), Gemini 3.1 Pro Preview (32k thinking), DeepSeek V4 Pro (max). Expensive. Watch the bill on long games.

You can also mix any of the nine catalogued models freely, including duplicates if you want an all-Kimi shoot-out.

## Starting the server

Put your API keys in `.env` at the repo root (`MASSIVE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`).

### Container (recommended) - ask your Coding Agent to make a start script for your system

```bash
./scripts/start_mac.sh
```

That builds the Docker image and starts the app on http://localhost:8000. To stop:

```bash
./scripts/stop_mac.sh
```

### Local dev

Two terminals, hot reload on both sides:

```bash
# backend on :8000
uv run uvicorn --factory backend.api.app:create_app --port 8000

# frontend on :5173 with vite, proxying /arena/* to :8000
cd frontend && npm run dev
```

Open http://localhost:5173.

## Tests

```bash
uv run pytest                  # 82 unit tests, ~4 seconds
uv run pytest -m integration   # opt-in 90-second real arena
```

The integration test runs four cheap traders end-to-end against real Massive and OpenRouter. The default suite stays offline.

## How the UI works

Pick a duration, choose your line-up (or hit Eco / Max), press Start. Each panel shows the model name above its current portfolio value, a line chart that fills from left to right over the game, a heatmap of current holdings that flashes green or red on price ticks, and a log of every tool call and decision streamed live from that trader. Press Stop to end early, or wait for the clock to do it for you.

## Layout

See `CLAUDE.md` for the full technical reference, and `PLAN.md` for the narrative of what was built and why.
