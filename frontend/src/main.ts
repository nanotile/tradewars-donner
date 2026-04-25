// App entry point — wires the top bar, tick loop, SSE stream, and panels.

import {
  fetchArenaConfig,
  openStream,
  startArena,
  stopArena,
  tickArena,
  type ArenaConfigSummary,
  type ArenaSnapshot,
  type TraderEvent,
} from "./api";
import { TraderPanel } from "./panel";
import { TraderState } from "./state";
import { initTheme } from "./theme";
import { mountTopBar } from "./topbar";

const TICK_MS = 1000;
const DEFAULT_DURATION_MINUTES = 12;

const bar = mountTopBar();
initTheme(document.getElementById("btn-theme") as HTMLButtonElement);
const durationInput = document.getElementById("duration-minutes") as HTMLInputElement;
const maxToggle = document.getElementById("toggle-max") as HTMLInputElement;
const rosterEl = document.getElementById("roster") as HTMLUListElement;

const panelHost = document.getElementById("panels")!;
const panels = new Map<string, TraderPanel>();
const states = new Map<string, TraderState>();
let stream: EventSource | null = null;
let tickTimer: number | null = null;
let durationSeconds = DEFAULT_DURATION_MINUTES * 60;
let arenaConfig: ArenaConfigSummary | null = null;

bar.setRunning(false);
bar.setRemaining(durationSeconds);

fetchArenaConfig()
  .then((c) => {
    arenaConfig = c;
    renderRoster();
  })
  .catch((err) => console.error("config fetch failed", err));

maxToggle.addEventListener("change", renderRoster);

bar.startBtn.addEventListener("click", async () => {
  bar.startBtn.disabled = true;
  try {
    const minutes = clamp(parseInt(durationInput.value, 10) || DEFAULT_DURATION_MINUTES, 1, 240);
    durationInput.value = String(minutes);
    durationSeconds = minutes * 60;
    panels.clear();
    states.clear();
    panelHost.innerHTML = "";
    const snap = await startArena({
      durationSeconds,
      maxMode: maxToggle.checked,
    });
    hydratePanels(snap);
    applySnapshot(snap);
    openEventStream();
    startTickLoop();
    bar.setRunning(true);
    durationInput.disabled = true;
    maxToggle.disabled = true;
  } catch (err) {
    console.error(err);
    bar.setRunning(false);
  }
});

bar.stopBtn.addEventListener("click", async () => {
  bar.stopBtn.disabled = true;
  try {
    const snap = await stopArena();
    applySnapshot(snap);
    teardown();
  } catch (err) {
    console.error(err);
  } finally {
    bar.setRunning(false);
  }
});

function renderRoster(): void {
  if (!arenaConfig) return;
  const key = maxToggle.checked ? "max" : "eco";
  rosterEl.innerHTML = "";
  for (const t of arenaConfig.traders) {
    const v = t[key];
    const li = document.createElement("li");
    li.className = "roster-item";
    li.innerHTML = `
      <span class="roster-name"></span>
      <span class="roster-effort"></span>
    `;
    li.querySelector(".roster-name")!.textContent = v.display_name;
    li.querySelector(".roster-effort")!.textContent = `(${v.reasoning_label})`;
    rosterEl.append(li);
  }
}

function hydratePanels(snap: ArenaSnapshot): void {
  if (panels.size) return;
  panelHost.innerHTML = "";
  for (const t of snap.traders) {
    const state = new TraderState(t.trader_id, formatTraderLabel(t.display_name, t.reasoning_label));
    const panel = new TraderPanel(state, durationSeconds);
    states.set(t.trader_id, state);
    panels.set(t.trader_id, panel);
    panelHost.append(panel.root);
    panel.mount();  // chart needs host in DOM before uPlot is constructed
  }
}

function applySnapshot(snap: ArenaSnapshot): void {
  bar.setRemaining(snap.time_remaining_seconds);
  for (const t of snap.traders) {
    const state = states.get(t.trader_id);
    const panel = panels.get(t.trader_id);
    if (!state || !panel) continue;
    state.recordSnapshot(t, snap.time_elapsed_seconds);
    panel.update();
  }
  if (!snap.running) teardown();
}

function startTickLoop(): void {
  if (tickTimer !== null) return;
  tickTimer = window.setInterval(async () => {
    try {
      const snap = await tickArena();
      applySnapshot(snap);
    } catch (err) {
      console.error("tick failed", err);
    }
  }, TICK_MS);
}

function openEventStream(): void {
  stream = openStream(onTraderEvent, (err) => console.warn("stream error", err));
}

function onTraderEvent(ev: TraderEvent): void {
  const state = states.get(ev.trader_id);
  const panel = panels.get(ev.trader_id);
  if (!state || !panel) return;
  state.pushEvent(ev);
  panel.update();
}

function teardown(): void {
  if (tickTimer !== null) {
    clearInterval(tickTimer);
    tickTimer = null;
  }
  stream?.close();
  stream = null;
  bar.setRunning(false);
  durationInput.disabled = false;
  maxToggle.disabled = false;
}

function formatTraderLabel(name: string, reasoning: string): string {
  return reasoning ? `${name} (${reasoning})` : name;
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}
