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
OpenRouter's `reasoning.effort` accepts `minimal | low | medium | high | xhigh`. `xhigh` ≈ 95% of `max_tokens` as the thinking budget. Works uniformly for Claude, GPT-5.4, Kimi-K2.6, DeepSeek. **Do NOT pass Anthropic's native `thinking: {type: "enabled", ...}` field via OpenRouter — it's silently dropped.**

**Path B — native SDKs:**

GPT-5.4 native (uses OpenAI `reasoning.effort`):
```python
from openai.types.shared import Reasoning
settings = ModelSettings(
    reasoning=Reasoning(effort="xhigh"),
    max_tokens=64_000,
)
agent = Agent(..., model="gpt-5.4", model_settings=settings)
```

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
- **Opus 4.7 uses the new adaptive thinking interface.** The old Sonnet-era `thinking: {type: "enabled", budget_tokens: N}` is rejected with: *"thinking.type.enabled is not supported for this model. Use thinking.type.adaptive and output_config.effort to control thinking behavior."*
- **LiteLLM 1.83 blocks `effort="max"` for Opus 4.7 via a stale validator.** We ship the one-line monkey-patch above to bypass it; drop the patch once LiteLLM 1.84+ is on PyPI.
- Use `extra_args` (top-level kwargs to `litellm.acompletion`) not `extra_body` (goes inside request body, Anthropic rejects as "Extra inputs are not permitted").
- Omit `temperature` entirely when thinking is enabled — Anthropic requires the default. Setting it explicitly to anything other than 1.0 errors out.

### Model defaults

- `max_tokens=64_000` to avoid truncation at xhigh reasoning (xhigh reserves ~95% as thinking budget).
- Anthropic `budget_tokens` ≤ `max_tokens` (strictly less if you want any output tokens).
