// Holdings heatmap: one tile per ticker, size ∝ market value, color = P&L.
// Tiles briefly flash green/red when the price ticks up/down.

import type { HoldingDetail } from "./api";

const FLASH_MS = 600;

export class Heatmap {
  private host: HTMLElement;
  private tiles = new Map<string, HTMLElement>();

  constructor(host: HTMLElement) {
    this.host = host;
    host.classList.add("heatmap");
  }

  render(
    holdings: Record<string, HoldingDetail>,
    priceDirections: Record<string, "up" | "down" | "same">,
  ): void {
    const tickers = Object.keys(holdings);

    for (const [ticker, el] of this.tiles) {
      if (!holdings[ticker]) {
        el.remove();
        this.tiles.delete(ticker);
      }
    }

    if (tickers.length === 0) {
      this.host.dataset.empty = "true";
      return;
    }
    delete this.host.dataset.empty;

    const totalValue = tickers.reduce((s, t) => s + holdings[t].market_value, 0);

    for (const ticker of tickers) {
      const h = holdings[ticker];
      const share = totalValue > 0 ? h.market_value / totalValue : 1 / tickers.length;
      let tile = this.tiles.get(ticker);
      if (!tile) {
        tile = this.createTile(ticker);
        this.host.append(tile);
        this.tiles.set(ticker, tile);
      }
      tile.style.flexGrow = String(Math.max(0.05, share));
      tile.dataset.pnl = h.unrealized_pnl >= 0 ? "up" : "down";
      tile.querySelector(".heatmap-value")!.textContent =
        formatMoney(h.market_value);

      const dir = priceDirections[ticker];
      if (dir === "up" || dir === "down") flash(tile, dir);
    }
  }

  private createTile(ticker: string): HTMLElement {
    const tile = document.createElement("div");
    tile.className = "heatmap-tile";
    tile.innerHTML = `
      <span class="heatmap-ticker">${ticker}</span>
      <span class="heatmap-value"></span>
    `;
    return tile;
  }
}

function flash(tile: HTMLElement, dir: "up" | "down"): void {
  tile.classList.remove("flash-up", "flash-down");
  // Force reflow so the animation restarts when direction repeats.
  void tile.offsetWidth;
  tile.classList.add(dir === "up" ? "flash-up" : "flash-down");
  setTimeout(() => tile.classList.remove("flash-up", "flash-down"), FLASH_MS);
}

function formatMoney(n: number): string {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}k`;
  return `$${n.toFixed(0)}`;
}
