// A single trader's panel. Composes value readout, chart, heatmap, log.

import { PortfolioChart } from "./chart";
import { Heatmap } from "./heatmap";
import { LogView } from "./log";
import type { TraderState } from "./state";

const STARTING_CASH = 1_000_000;

export class TraderPanel {
  readonly root: HTMLElement;
  private state: TraderState;
  private durationSeconds: number;
  private chart: PortfolioChart | null = null;
  private heatmap: Heatmap;
  private log: LogView;
  private valueEl: HTMLElement;
  private statsEl: HTMLElement;

  constructor(state: TraderState, durationSeconds: number) {
    this.state = state;
    this.durationSeconds = durationSeconds;
    this.root = document.createElement("section");
    this.root.className = "panel";
    this.root.innerHTML = `
      <header class="panel-head">
        <span class="panel-name">${state.displayName}</span>
        <span class="panel-value" data-trend="flat">$0</span>
        <span class="panel-cycle-stats"></span>
        <span class="panel-winner-pill">Winner</span>
      </header>
      <div class="panel-chart"></div>
      <div class="panel-heatmap"></div>
      <div class="panel-log"></div>
    `;
    this.valueEl = this.root.querySelector(".panel-value")!;
    this.statsEl = this.root.querySelector(".panel-cycle-stats")!;
    this.heatmap = new Heatmap(this.root.querySelector(".panel-heatmap")!);
    this.log = new LogView(this.root.querySelector(".panel-log")!);
    // Chart created in mount() — uPlot misbehaves when its host isn't in the
    // DOM at construction time (initial-draw error path that mangles the
    // stroke callback). mount() is called by main.ts after appending root.
  }

  mount(): void {
    if (this.chart) return;
    const host = this.root.querySelector(".panel-chart") as HTMLElement;
    this.chart = new PortfolioChart(host, this.durationSeconds);
  }

  setLeader(isLeader: boolean): void {
    if (isLeader) this.root.dataset.leader = "true";
    else delete this.root.dataset.leader;
  }

  setEndState(state: "winner" | "loser" | null): void {
    delete this.root.dataset.leader;
    delete this.root.dataset.winner;
    delete this.root.dataset.loser;
    if (state === "winner") this.root.dataset.winner = "true";
    else if (state === "loser") this.root.dataset.loser = "true";
  }

  update(): void {
    const snap = this.state.latest;
    if (snap) {
      this.valueEl.textContent = formatMoney(snap.total_portfolio_value);
      this.valueEl.dataset.trend = snap.total_pnl >= 0 ? "up" : "down";
      this.heatmap.render(snap.holdings, this.state.priceDirections());
      this.state.rememberPrices();
    }
    const { cyclesPerMinute, avgDurationSeconds } = this.state.cycleStats;
    this.statsEl.textContent = formatCycleStats(
      cyclesPerMinute,
      avgDurationSeconds,
    );
    if (this.chart) this.chart.update(this.state.chart, STARTING_CASH);
    this.log.render(this.state.log);
  }
}

function formatMoney(n: number): string {
  return n.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

function formatCycleStats(
  rpm: number | null,
  avgSec: number | null,
): string {
  if (rpm === null && avgSec === null) return "";
  const parts: string[] = [];
  if (rpm !== null) parts.push(`${rpm.toFixed(1)} req/min`);
  if (avgSec !== null) parts.push(`${avgSec.toFixed(0)}s avg`);
  return parts.join(" · ");
}
