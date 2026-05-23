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

export interface CycleStats {
  cyclesPerMinute: number | null;
  avgDurationSeconds: number | null;
}

export class TraderState {
  readonly traderId: string;
  displayName: string;
  latest: TraderSnapshot | null = null;
  chart: ChartPoint[] = [];
  log: TraderEvent[] = [];
  previousPrices: Record<string, number> = {};

  private _cycleStarts = new Map<number, number>();
  private _cycleDurations: number[] = [];
  private _cycleCount = 0;

  constructor(traderId: string, displayName: string) {
    this.traderId = traderId;
    this.displayName = displayName;
  }

  get cycleStats(): CycleStats {
    let cyclesPerMinute: number | null = null;
    const lastPoint = this.chart[this.chart.length - 1];
    if (this._cycleCount >= 1 && lastPoint && lastPoint.t > 5) {
      cyclesPerMinute = this._cycleCount / (lastPoint.t / 60);
    }
    const avgDurationSeconds =
      this._cycleDurations.length > 0
        ? this._cycleDurations.reduce((a, b) => a + b, 0) /
          this._cycleDurations.length
        : null;
    return { cyclesPerMinute, avgDurationSeconds };
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

    if (ev.type === "cycle_start") {
      const ts = new Date(ev.timestamp).getTime();
      this._cycleStarts.set(ev.payload.cycle as number, ts);
      this._cycleCount++;
    } else if (ev.type === "cycle_end" || ev.type === "error") {
      const cycleNum = ev.payload.cycle as number | undefined;
      if (cycleNum !== undefined) {
        const startTs = this._cycleStarts.get(cycleNum);
        if (startTs !== undefined) {
          this._cycleDurations.push(
            (new Date(ev.timestamp).getTime() - startTs) / 1000,
          );
          this._cycleStarts.delete(cycleNum);
        }
      }
    }
  }

  reset(): void {
    this.latest = null;
    this.chart = [];
    this.log = [];
    this.previousPrices = {};
    this._cycleStarts.clear();
    this._cycleDurations = [];
    this._cycleCount = 0;
  }
}
