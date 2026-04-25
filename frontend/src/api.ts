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

export interface TraderVariant {
  display_name: string;
  reasoning_label: string;
}

export interface TraderConfigSummary {
  id: string;
  max: TraderVariant;
  eco: TraderVariant;
}

export interface ArenaConfigSummary {
  duration_seconds: number;
  traders: TraderConfigSummary[];
}

export interface ArenaSnapshot {
  started_at: string;
  time_elapsed_seconds: number;
  time_remaining_seconds: number;
  running: boolean;
  traders: TraderSnapshot[];
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
  maxMode?: boolean;
}

export async function startArena(opts: StartOptions = {}): Promise<ArenaSnapshot> {
  const body = {
    duration_seconds: opts.durationSeconds,
    max_mode: opts.maxMode ?? false,
  };
  const r = await post("/arena/start", body);
  if (!r.ok) throw new Error(`start failed: ${r.status}`);
  return r.json();
}

export async function fetchArenaConfig(): Promise<ArenaConfigSummary> {
  const r = await fetch("/arena/config");
  if (!r.ok) throw new Error(`config failed: ${r.status}`);
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
  // sse-starlette emits named events; listen to each type we care about.
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
