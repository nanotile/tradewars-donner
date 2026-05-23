// Typed client for the Tradewars FastAPI backend.

import { apiFetch, apiPost } from "./apiClient";

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
  total_trades: number;
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
  return apiPost(path, body);
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
  const r = await apiFetch("/arena/config");
  if (!r.ok) throw new Error(`config failed: ${r.status}`);
  return r.json();
}

export interface ArenaStatus {
  running: boolean;
  snapshot?: ArenaSnapshot;
}

export async function fetchArenaStatus(): Promise<ArenaStatus> {
  const r = await apiFetch("/arena/status");
  if (!r.ok) throw new Error(`status failed: ${r.status}`);
  return r.json();
}

const SSE_RECONNECT_BASE_MS = 1000;
const SSE_RECONNECT_MAX_MS = 30000;

async function fetchSseTicket(): Promise<string> {
  const r = await apiPost("/api/auth/sse-ticket");
  if (!r.ok) throw new Error(`sse-ticket failed: ${r.status}`);
  const data = await r.json();
  return data.ticket;
}

export function openStream(
  onEvent: (event: TraderEvent) => void,
  onError?: (err: Event) => void,
): { close: () => void } {
  let es: EventSource | null = null;
  let attempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let closed = false;

  const types: TraderEvent["type"][] = [
    "cycle_start",
    "cycle_end",
    "tool_called",
    "tool_output",
    "message",
    "error",
    "liquidation",
  ];

  function dispatch(e: MessageEvent) {
    try {
      onEvent(JSON.parse(e.data));
    } catch {
      /* ignore malformed frames */
    }
  }

  async function connect() {
    if (closed) return;
    let url = "/arena/stream";
    try {
      const ticket = await fetchSseTicket();
      url = `/arena/stream?ticket=${encodeURIComponent(ticket)}`;
    } catch {
      /* fall through — server will 401 if ticket required */
    }
    es = new EventSource(url);

    es.onopen = () => {
      attempt = 0;
    };

    for (const t of types) es.addEventListener(t, dispatch as EventListener);

    es.onerror = (err) => {
      if (onError) onError(err);
      if (closed) return;
      es?.close();
      const delay = Math.min(
        SSE_RECONNECT_BASE_MS * 2 ** attempt,
        SSE_RECONNECT_MAX_MS,
      );
      attempt++;
      reconnectTimer = setTimeout(() => connect(), delay);
    };
  }

  connect();

  return {
    close() {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
    },
  };
}
