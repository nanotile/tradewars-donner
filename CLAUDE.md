# Tradewars

See this for the plan:

@PLAN.md

## Working code strategies (confirmed in Phase 2)

### MCP stdio servers (via OpenAI Agents SDK)

- Use `agents.mcp.MCPServerStdio` as an async context manager.
- Pass `client_session_timeout_seconds=60` — Massive needs >5s on first start (indexes OpenAPI spec).
- Always pass `cache_tools_list=True` so we don't re-list tools each decision cycle.
- Pass env via `params={"env": {**os.environ, ...}}` — child process does not inherit automatically.

**Massive MCP:** install once with `uv tool install "mcp_massive @ git+https://github.com/massive-com/mcp_massive@v0.9.1"`. Invoke as `mcp_massive` with `MASSIVE_API_KEY` in env. Three composable tools (`search_endpoints`, `call_api`, `query_data`) — not one tool per endpoint.

**Memory MCP (Anthropic reference):** `npx -y @modelcontextprotocol/server-memory` with `MEMORY_FILE_PATH` env var pointing to a per-trader JSONL file. Nine tools: `create_entities`, `create_relations`, `add_observations`, `delete_entities`, `delete_observations`, `delete_relations`, `read_graph`, `search_nodes`, `open_nodes`. Note: `add_observations` fails silently if the entity doesn't exist — prompt traders to `create_entities` first.

### Streaming events

`Runner.run_streamed(agent, input, max_turns=N).stream_events()` yields three useful event types:
- `run_item_stream_event` with `name == "tool_called"` → `event.item.raw_item.name` is the tool name
- `run_item_stream_event` with `name == "tool_output"` → `event.item.raw_item["output"]` or `.output`
- `run_item_stream_event` with `name == "message_output_created"` → final assistant message; text is in `event.item.raw_item.content[i].text`

Default `max_turns=10` is too low once MCP servers are wired — bump to 30+.

### Tracing

`set_tracing_disabled(True)` at startup when we're not using OpenAI's tracing backend — suppresses noisy 401s if `OPENAI_API_KEY` is unset or invalid.

### Reasoning-effort passthrough: two working paths

**Path A — OpenRouter (unified, simplest):**
```python
from openai import AsyncOpenAI
from agents import OpenAIChatCompletionsModel, ModelSettings

client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
model = OpenAIChatCompletionsModel(model="anthropic/claude-opus-4-7", openai_client=client)

settings = ModelSettings(
    max_tokens=64_000,
    extra_body={"reasoning": {"effort": "xhigh"}, "include_reasoning": True},
)
```
OpenRouter's `reasoning.effort` accepts `minimal | low | medium | high | xhigh`. `xhigh` ≈ 95% of `max_tokens` as the thinking budget. Works uniformly for most frontier models (we use it for Kimi K2.6). **Do NOT pass Anthropic's native `thinking: {type: "enabled", ...}` field via OpenRouter — it's silently dropped.**

**Path B — native SDKs:**

GPT-5.5 native (uses OpenAI `reasoning.effort`):
```python
from openai.types.shared import Reasoning
settings = ModelSettings(
    reasoning=Reasoning(effort="xhigh"),
    max_tokens=64_000,
)
agent = Agent(..., model="gpt-5.5", model_settings=settings)
```
Gotcha: `effort="none"` (used for gpt-5.4-mini and smaller models) is a valid API value but is NOT in the SDK's `Reasoning.effort` Literal on openai-python ≤ 2.x. When we see `"none"` in our config we omit the `reasoning` field entirely — OpenAI's own default (no reasoning) applies.

Claude Opus 4.7 native (via `LitellmModel` extension, using Anthropic's **new** adaptive thinking API):
```python
from agents.extensions.models.litellm_model import LitellmModel
from litellm.llms.anthropic.chat.transformation import AnthropicConfig

# Monkey-patch: widen LiteLLM 1.83's Opus-4.6-only `effort="max"` gate to
# include Opus 4.7 too. The check is `_is_opus_4_6_model` (a @staticmethod)
# inside `AnthropicConfig.transform_request`. Let Anthropic be the truth.
def _is_opus_4_6_or_4_7(model: str) -> bool:
    m = model.lower()
    return any(v in m for v in (
        "opus-4-6", "opus_4_6", "opus-4.6", "opus_4.6",
        "opus-4-7", "opus_4_7", "opus-4.7", "opus_4.7",
    ))
AnthropicConfig._is_opus_4_6_model = staticmethod(_is_opus_4_6_or_4_7)

model = LitellmModel(model="anthropic/claude-opus-4-7", api_key=ANTHROPIC_API_KEY)
settings = ModelSettings(
    max_tokens=64_000,
    extra_args={
        "thinking": {"type": "adaptive"},        # NOT the old "enabled" form
        "output_config": {"effort": "max"},      # requires the monkey-patch above
    },
)
```
Critical gotchas:
- **Opus 4.7 and Haiku 4.5 use DIFFERENT thinking interfaces.** Only Opus 4.7 accepts the new `thinking:{type:"adaptive"}` form; Haiku 4.5 (and Sonnet 4.x) still use the legacy `thinking:{type:"enabled", budget_tokens:N}` form. Cross-sending either shape to the wrong model produces:
  - Haiku receives adaptive → *"adaptive thinking is not supported on this model"*
  - Opus 4.7 receives enabled → *"thinking.type.enabled is not supported for this model. Use thinking.type.adaptive and output_config.effort to control thinking behavior."*
  We branch on config shape: `{"budget_tokens": N}` → enabled form; `{"effort": "max"|...}` → adaptive form. Our Haiku eco config includes both (`{"effort": "low", "budget_tokens": 1024}`) — `effort` is kept purely for the UI label, `budget_tokens` drives the API call.
- **LiteLLM 1.83 blocks `effort="max"` for Opus 4.7 via a stale validator.** We ship the one-line monkey-patch above to bypass it; drop the patch once LiteLLM 1.84+ is on PyPI.
- Use `extra_args` (top-level kwargs to `litellm.acompletion`) not `extra_body` (goes inside request body, Anthropic rejects as "Extra inputs are not permitted").
- Omit `temperature` entirely when thinking is enabled — Anthropic requires the default. Setting it explicitly to anything other than 1.0 errors out.

Gemini native (via `LitellmModel` with the `gemini/` prefix). Two channels for thinking, preferring the explicit one for brand-new models where LiteLLM's unified mapping may be conservative:
```python
model = LitellmModel(
    model="gemini/gemini-3.1-pro-preview",
    api_key=os.environ["GOOGLE_API_KEY"],
)
# Preferred: explicit thinking budget — LiteLLM forwards to Google's `thinkingConfig`.
settings = ModelSettings(
    max_tokens=64_000,
    extra_args={"thinking": {"type": "enabled", "budget_tokens": 32_000}},
)
# Alternative: LiteLLM's unified reasoning_effort knob (low/medium/high).
# May map conservatively for very new model ids that LiteLLM doesn't know yet.
settings = ModelSettings(
    max_tokens=64_000,
    extra_args={"reasoning_effort": "high"},
)
```
Gotchas:
- The `gemini/` prefix routes through Google AI Studio; `vertex_ai/` would route through Vertex (not what we want — we have `GOOGLE_API_KEY`, not service-account creds).
- Pass Gemini knobs through `extra_args` (same channel as Anthropic). It is NOT the same as OpenAI's structured `Reasoning(effort=...)` object.
- Our config supports both shapes: `{"budget_tokens": N}` uses the explicit `thinking` form; `{"effort": "..."}` falls back to `reasoning_effort`.

### Provider routing cheat sheet

| Provider | `build_model` returns | Reasoning knob channel | Config shape it reads |
|---|---|---|---|
| `openai`     | plain model-id string        | `ModelSettings.reasoning=Reasoning(effort=...)`, or omitted entirely when `effort="none"` | `{"effort": "xhigh"\|"high"\|...\|"none"}` |
| `anthropic`  | `LitellmModel`               | `extra_args={"thinking":{"type":"adaptive"},"output_config":{"effort":...},"cache_control":{"type":"ephemeral"}}` | `{"effort": "max"\|"high"\|"low"\|...}` |
| `google`     | `LitellmModel`               | Prefers `extra_args={"thinking":{"type":"enabled","budget_tokens":N}}` if `budget_tokens` is in config; else `extra_args={"reasoning_effort":...}` | `{"budget_tokens": N}` **or** `{"effort": "high"\|...}` |
| `deepseek`   | `OpenAIChatCompletionsModel` pointed at `https://api.deepseek.com` | `extra_body={"reasoning_effort":...,"thinking":{"type":"enabled"\|"disabled"}}` (either or both, whichever the config carries) | `{"effort": "max", "thinking": {"type":"enabled"}}` for max; `{"thinking": {"type":"disabled"}}` for eco |
| `openrouter` | `OpenAIChatCompletionsModel` | `extra_body={"reasoning":{"effort":...}}` | `{"effort": "xhigh"\|"high"\|...}` |

### Config file shape: max vs eco variants

`backend/arena/config.json` stores TWO variants per trader so we can flip between a full max-reasoning line-up and a cheap eco line-up from the UI without editing config. Top-level key per trader:

```json
{
  "id": "claude",
  "max_tokens": 64000,
  "max": { "display_name": "Claude Opus 4.7",  "provider": "anthropic", "model": "anthropic/claude-opus-4-7",  "reasoning": {"effort": "max"} },
  "eco": { "display_name": "Claude Haiku 4.5", "provider": "anthropic", "model": "anthropic/claude-haiku-4-5", "reasoning": {"effort": "low"} }
}
```

`max_tokens` is shared across variants (per-trader). `display_name`, `provider`, `model`, and `reasoning` are per-variant.

- `ArenaConfig.load(path, max_mode=True|False)` flattens the selected variant into a `TraderConfig`.
- `/arena/start` accepts `max_mode: bool` (default `False` — eco mode; the UI sidebar's "Max mode" toggle drives this).
- `/arena/config` (GET) returns both variants' display_name + reasoning_label so the sidebar roster can preview the line-up before Start.
- `arena.reasoning_label(reasoning)` turns a reasoning dict into a short UI label: `{"effort": "max"}` → `"max"`, `{"budget_tokens": 32000}` → `"32k"`, `{"thinking": {"type": "disabled"}}` → `"off"`, `{"thinking": {"type": "enabled"}}` → `"on"`. When both `effort` and `thinking` are set (DeepSeek max), effort wins. Emitted on every `TraderSnapshot` so panel headers render `"Claude Opus 4.7 (max)"`.

### Model defaults

- `max_tokens=64_000` to avoid truncation at xhigh reasoning (xhigh reserves ~95% as thinking budget).
- Anthropic `budget_tokens` ≤ `max_tokens` (strictly less if you want any output tokens).
- `MAX_TURNS_PER_CYCLE = 200` on the Trader loop — reasoning models with heavy Massive MCP usage blow past 40 turns easily on exploratory cycles.
- `INTER_CYCLE_SLEEP_SECONDS = 10.0` — the main cost throttle. After each `final_output` the trader loop sleeps 10 s before the next `Runner.run_streamed`. Caps cycles at ~6/minute/trader; without it a full game at max reasoning can rack up serious Anthropic/OpenAI spend surprisingly fast. Lower if you want more responsive trading, but watch the bill.

### Frontend gotchas (uPlot + Vite)

Three things bit us hard when wiring the chart panels:

1. **`tsc -b` was emitting `.js` next to `.ts` in `src/`.** Vite's dev server serves whichever file matches the import URL (`/src/panel.js`); when stale `.js` exist, Vite serves THEM instead of compiling the live `.ts`. Symptoms: code changes visibly do nothing in the browser even after restarts. Fix: `noEmit: true` in `frontend/tsconfig.json` and `.gitignore` for `frontend/src/*.js`. Don't ever check those in.

2. **uPlot misbehaves if its host is not in the DOM at `new uPlot(...)` time** — internal initial-draw goes down a code path that subsequently throws `s.stroke is not a function` on every redraw, the canvas stays at its initial size, and you get a panel with axes but no line. `TraderPanel` defers chart creation to a `mount()` method that `main.ts` calls AFTER `panelHost.append(panel.root)`.

3. **Don't mutate `series.stroke` to a string after init** to flip the line color (e.g. green/red on P&L sign). uPlot caches the value in a way that breaks subsequent renders. Use a stroke callback instead: `stroke: (u) => u.data[1].at(-1) >= initial ? "#3fbf7f" : "#e05560"`. Same `s.stroke is not a function` symptom.

### Prompt caching (per provider)

- **OpenAI Responses API**: automatic for prompts ≥1024 tokens. No opt-in needed. Already firing for GPT.
- **Anthropic**: NOT implicit by default. Anthropic's 2026 "automatic caching" mode is a single top-level `cache_control={"type":"ephemeral"}` field on `/v1/messages` — without it nothing caches. We add it via `extra_args` in `build_model_settings` for the `anthropic` provider. LiteLLM 1.83 officially documents only the older per-message cache_control markers, but the top-level field is passed through unchanged to Anthropic. Confirm it's firing by checking `result.usage.cache_creation_input_tokens` / `cache_read_input_tokens`.
- **Gemini**: Google has a separate CachedContent API; not automatic, and LiteLLM doesn't expose a unified knob. Skipped for now — Gemini's per-token cost is lower and this plays second-fiddle to the Anthropic fix.
- **System prompt stability**: `render_system_prompt(duration_seconds)` is computed once at Agent construction in `Trader.run_until_stopped`. All cycles within a game reuse the same `Agent` with the same `instructions` string, so the system prompt is byte-identical across cycles. Tool descriptions are cached too (`cache_tools_list=True` on both MCPs). Prefix is cache-friendly; the varying suffix is just the per-cycle user message.

## Tool output formatting

SDK-level `event.item.raw_item` for `tool_output` events arrives in two shapes that must be normalised before display:
- **MCP tools**: `[{"type": "input_text", "text": "..."}]` — a list of content parts.
- **Function tools** (`get_state`, `trade`): a plain dict.

`backend/traders/trader.py::_format_output` flattens both: strips MCP wrappers to the inner text, JSON-serialises dicts. Doing `str(raw_out)` would emit Python repr noise (`[{'type': 'input_text', 'text': '...'}]`) which is unreadable in the UI.

The frontend `log.ts` further humanises tool *calls* (e.g. `trade({"ticker":"INTC","quantity":-100})` → `sell 100 INTC`; `call_api({path, params, store_as})` → `path · k=v k=v`; memory ops `create_entities`/`add_observations`/`read_graph` → `remember X` / `note on X` / `read memory`) and compacts tool *outputs* ("Stored N rows in X" → `stored N rows → X`; endpoint listings → `N endpoints found`; empty responses → `no data`).
