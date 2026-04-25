// Typed client for the Tradewars FastAPI backend.

export interface HoldingDetail {
  quantity: number;
  avg_cost: number;
  current_price: number;
  market_value: number;
  unrealized_pnl: number;
}

export interface TraderSnapshot {
  trader_id: string;
  display_name: string;
  reasoning_label: string;
  cash: number;
  holdings: Record<string, HoldingDetail>;
  total_portfolio_value: number;
  total_pnl: number;
}

export interface ArenaSnapshot {
  started_at: string;
  time_elapsed_seconds: number;
  time_remaining_seconds: number;
  running: boolean;
  traders: TraderSnapshot[];
}

export interface ReasoningOption {
  label: string;
  reasoning: Record<string, unknown>;
}

export interface ModelSpec {
  display_name: string;
  provider: string;
  model: string;
  reasoning_options: ReasoningOption[];
}

export interface PresetSelection {
  model_id: string;
  reasoning_label: string;
}

export interface ArenaConfigCatalog {
  duration_seconds: number;
  max_tokens: number;
  models: Record<string, ModelSpec>;
  presets: Record<string, PresetSelection[]>;
}

export interface TraderEvent {
  trader_id: string;
  type:
    | "cycle_start"
    | "cycle_end"
    | "tool_called"
    | "tool_output"
    | "message"
    | "error"
    | "liquidation";
  timestamp: string;
  payload: Record<string, unknown>;
}

async function post(path: string, body?: unknown): Promise<Response> {
  return fetch(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
}

export interface StartOptions {
  durationSeconds?: number;
  selections?: PresetSelection[];
}

export async function startArena(opts: StartOptions = {}): Promise<ArenaSnapshot> {
  const body: Record<string, unknown> = {};
  if (opts.durationSeconds) body.duration_seconds = opts.durationSeconds;
  if (opts.selections) body.selections = opts.selections;
  const r = await post("/arena/start", body);
  if (!r.ok) throw new Error(`start failed: ${r.status} — ${await r.text()}`);
  return r.json();
}

export async function stopArena(): Promise<ArenaSnapshot> {
  const r = await post("/arena/stop");
  if (!r.ok) throw new Error(`stop failed: ${r.status}`);
  return r.json();
}

export async function tickArena(): Promise<ArenaSnapshot> {
  const r = await post("/arena/tick");
  if (!r.ok) throw new Error(`tick failed: ${r.status}`);
  return r.json();
}

export async function fetchArenaConfig(): Promise<ArenaConfigCatalog> {
  const r = await fetch("/arena/config");
  if (!r.ok) throw new Error(`config failed: ${r.status}`);
  return r.json();
}

export function openStream(
  onEvent: (event: TraderEvent) => void,
  onError?: (err: Event) => void,
): EventSource {
  const es = new EventSource("/arena/stream");
  const dispatch = (e: MessageEvent) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch {
      /* ignore malformed frames */
    }
  };
  const types: TraderEvent["type"][] = [
    "cycle_start",
    "cycle_end",
    "tool_called",
    "tool_output",
    "message",
    "error",
    "liquidation",
  ];
  for (const t of types) es.addEventListener(t, dispatch as EventListener);
  if (onError) es.onerror = onError;
  return es;
}
