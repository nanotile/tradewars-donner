// Per-trader client-side state.
//
// One chart point per tick (cheap — uPlot handles thousands). The log is a
// bounded ring.

import type { TraderEvent, TraderSnapshot } from "./api";

export const CHART_MAX_POINTS = 5000;
export const LOG_MAX_ENTRIES = 100;

export interface ChartPoint {
  t: number; // seconds since arena start
  value: number;
}

export class TraderState {
  readonly traderId: string;
  displayName: string;
  latest: TraderSnapshot | null = null;
  chart: ChartPoint[] = [];
  log: TraderEvent[] = [];
  previousPrices: Record<string, number> = {};

  constructor(traderId: string, displayName: string) {
    this.traderId = traderId;
    this.displayName = displayName;
  }

  recordSnapshot(snap: TraderSnapshot, elapsedSeconds: number): void {
    this.latest = snap;
    this.chart.push({ t: elapsedSeconds, value: snap.total_portfolio_value });
    if (this.chart.length > CHART_MAX_POINTS) {
      this.chart.splice(0, this.chart.length - CHART_MAX_POINTS);
    }
  }

  priceDirections(): Record<string, "up" | "down" | "same"> {
    const out: Record<string, "up" | "down" | "same"> = {};
    if (!this.latest) return out;
    for (const [ticker, h] of Object.entries(this.latest.holdings)) {
      const prev = this.previousPrices[ticker];
      out[ticker] =
        prev === undefined || prev === h.current_price
          ? "same"
          : h.current_price > prev
            ? "up"
            : "down";
    }
    return out;
  }

  rememberPrices(): void {
    if (!this.latest) return;
    this.previousPrices = Object.fromEntries(
      Object.entries(this.latest.holdings).map(([t, h]) => [t, h.current_price]),
    );
  }

  pushEvent(ev: TraderEvent): void {
    this.log.push(ev);
    if (this.log.length > LOG_MAX_ENTRIES) {
      this.log.splice(0, this.log.length - LOG_MAX_ENTRIES);
    }
  }

  reset(): void {
    this.latest = null;
    this.chart = [];
    this.log = [];
    this.previousPrices = {};
  }
}
