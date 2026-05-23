// Bounded per-trader log trace fed by SSE events.
//
// Tool calls and outputs are humanised — casual-reader phrasing instead of
// raw JSON arguments or Python repr. Unknown tools fall back to the tool name.

import type { TraderEvent } from "./api";

const TYPE_LABELS: Record<TraderEvent["type"], string> = {
  cycle_start: "cycle",
  cycle_end: "decided",
  tool_called: "action",
  tool_output: "result",
  message: "message",
  error: "error",
  liquidation: "liquidated",
};

export class LogView {
  private host: HTMLElement;
  private rendered = 0;

  constructor(host: HTMLElement) {
    this.host = host;
    host.classList.add("log");
  }

  render(events: TraderEvent[]): void {
    if (events.length < this.rendered) {
      this.host.innerHTML = "";
      this.rendered = 0;
    }
    for (let i = this.rendered; i < events.length; i++) {
      const ev = events[i];
      const row = document.createElement("div");
      row.className = `log-row log-${ev.type}`;
      row.innerHTML = `
        <span class="log-type">${TYPE_LABELS[ev.type]}</span>
        <span class="log-text"></span>
      `;
      row.querySelector(".log-text")!.textContent = summarise(ev);
      this.host.append(row);
    }
    this.rendered = events.length;
    this.host.scrollTop = this.host.scrollHeight;
  }
}

function summarise(ev: TraderEvent): string {
  const p = ev.payload;
  switch (ev.type) {
    case "tool_called":
      return summariseToolCall(String(p.tool ?? ""), String(p.arguments ?? "{}"));
    case "tool_output":
      return summariseToolOutput(String(p.output ?? ""));
    case "message":
      return oneLine(String(p.text ?? ""), 220);
    case "cycle_start":
      return `#${p.cycle}`;
    case "cycle_end":
      return oneLine(String(p.rationale ?? ""), 200);
    case "error":
      return `#${p.cycle}: ${String(p.error ?? "")}`;
    case "liquidation":
      return `${p.ticker} ×${p.quantity} @ ${p.price}`;
  }
}

function summariseToolCall(tool: string, argsJson: string): string {
  let args: Record<string, unknown> = {};
  try { args = JSON.parse(argsJson); } catch { /* fall through */ }

  switch (tool) {
    // ---- game tools ----
    case "get_state":
      return "check state";
    case "trade": {
      const q = Number(args.quantity ?? 0);
      const side = q >= 0 ? "buy" : "sell";
      return `${side} ${Math.abs(q)} ${args.ticker ?? ""}`.trim();
    }

    // ---- Massive MCP ----
    case "search_endpoints":
      return `search API: ${String(args.query ?? "")}`;
    case "call_api": {
      const path = String(args.path ?? "");
      const params = args.params as Record<string, unknown> | undefined;
      const compact = params ? formatParams(params) : "";
      return compact ? `${path} · ${compact}` : path;
    }
    case "query_data":
      return `sql: ${oneLine(String(args.sql ?? ""), 200)}`;

    // ---- Memory MCP ----
    case "create_entities":
      return `remember ${listNames((args.entities as Array<{ name: string }>) ?? [])}`;
    case "add_observations":
      return `note on ${listNames((args.observations as Array<{ entityName: string }>) ?? [], "entityName")}`;
    case "delete_entities":
      return `forget ${((args.entityNames as string[]) ?? []).join(", ")}`;
    case "read_graph":
      return "read memory";
    case "search_nodes":
      return `search memory: ${String(args.query ?? "")}`;
    case "open_nodes":
      return `recall ${((args.names as string[]) ?? []).join(", ")}`;

    default:
      return tool;
  }
}

function summariseToolOutput(raw: string): string {
  const text = raw.trim();
  if (!text) return "(no output)";

  // "Stored N rows in 'X'\nColumns: ..." → compact line
  const stored = text.match(/^Stored (\d+) rows? in ['"]([^'"]+)['"]/);
  if (stored) return `stored ${stored[1]} rows → ${stored[2]}`;

  // Surfaced errors/warnings from Massive MCP
  if (text.startsWith("Error")) return oneLine(text, 200);
  if (text.startsWith("Warning [EMPTY]")) return "no data";
  if (text.startsWith("Warning")) return oneLine(text, 200);

  // Endpoint search results "1. X\n   GET /..."
  const endpointMatches = text.match(/(?:^|\n)\d+\. [A-Z]/g);
  if (endpointMatches && endpointMatches.length >= 1) {
    const n = endpointMatches.length;
    return `${n} endpoint${n > 1 ? "s" : ""} found`;
  }

  // JSON-looking function-tool output → keep first values, compact
  if (text.startsWith("{")) return oneLine(text, 180);

  // CSV-looking output → row count (first line is usually headers)
  const lines = text.split("\n").filter((l) => l.length);
  if (lines.length >= 2 && lines[0].includes(",") && lines[1].includes(",")) {
    return `${lines.length - 1} rows`;
  }

  return oneLine(text, 180);
}

function formatParams(p: Record<string, unknown>): string {
  return Object.entries(p)
    .map(([k, v]) => `${k}=${formatValue(v)}`)
    .join(" ");
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function listNames<T extends Record<string, unknown>>(items: T[], key = "name"): string {
  return items.map((i) => String(i[key] ?? "")).filter(Boolean).join(", ");
}

function oneLine(s: string, max = 160): string {
  const compact = s.replace(/\s+/g, " ").trim();
  return compact.length > max ? `${compact.slice(0, max - 1)}…` : compact;
}
