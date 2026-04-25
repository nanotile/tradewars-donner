// Thin uPlot wrapper for the per-trader portfolio-value line chart.
// Starts at a 1×1 canvas, syncs to real container dimensions after the DOM
// lays out. uPlot re-renders crisply at each size — no CSS stretching.

import uPlot, { type Options } from "uplot";
import "uplot/dist/uPlot.min.css";

import type { ChartPoint } from "./state";

const MIN_HEIGHT = 120;
const Y_AXIS_WIDTH = 64;

export class PortfolioChart {
  private plot: uPlot;
  private host: HTMLElement;
  private initialValue = 1_000_000;

  constructor(host: HTMLElement, durationSeconds: number) {
    this.host = host;

    const opts: Options = {
      width: 1,
      height: 1,
      pxAlign: false,
      cursor: { show: false },
      legend: { show: false },
      scales: {
        x: { time: false, range: [0, durationSeconds] },
        y: {
          range: (_u, min, max) =>
            min === max
              ? [min - 1_000, max + 1_000]
              : [min - (max - min) * 0.1, max + (max - min) * 0.1],
        },
      },
      axes: [
        {
          stroke: getVar("--fg-muted") || "#8a93a1",
          grid: { stroke: getVar("--grid") || "#232831" },
          values: (_u, splits) => splits.map((s) => `${Math.round(s / 60)}m`),
        },
        {
          stroke: getVar("--fg-muted") || "#8a93a1",
          grid: { stroke: getVar("--grid") || "#232831" },
          size: Y_AXIS_WIDTH,
          values: (_u, splits) => splits.map(formatCompact),
        },
      ],
      series: [
        {},
        {
          // Static color: dynamic re-coloring caused uPlot to throw on redraw.
          stroke: (u: uPlot) => {
            const ys = u.data[1];
            const latest = ys[ys.length - 1] ?? this.initialValue;
            const up = (latest as number) >= this.initialValue;
            return getVar(up ? "--trend-up" : "--trend-down") || (up ? "#3fbf7f" : "#e05560");
          },
          width: 2,
        },
      ],
    };
    this.plot = new uPlot(opts, [[], []], host);

    // Double-RAF lets the panel grid finish layout before we read dimensions.
    requestAnimationFrame(() => requestAnimationFrame(() => this.syncSize()));
    new ResizeObserver(() => this.syncSize()).observe(this.host);
  }

  update(points: ChartPoint[], initialValue: number): void {
    this.initialValue = initialValue;
    const xs = points.map((p) => p.t);
    const ys = points.map((p) => p.value);
    this.plot.setData([xs, ys]);
  }

  private syncSize(): void {
    const rect = this.host.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    this.plot.setSize({
      width: Math.floor(rect.width),
      height: Math.max(MIN_HEIGHT, Math.floor(rect.height)),
    });
  }
}

function getVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function formatCompact(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}k`;
  return `$${n.toFixed(0)}`;
}
