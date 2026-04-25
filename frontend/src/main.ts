// App entry point — wires the top bar, slot pickers, tick loop, SSE, panels.

import {
  fetchArenaConfig,
  openStream,
  startArena,
  stopArena,
  tickArena,
  type ArenaConfigCatalog,
  type ArenaSnapshot,
  type PresetSelection,
  type TraderEvent,
} from "./api";
import { TraderPanel } from "./panel";
import { TraderState } from "./state";
import { initTheme } from "./theme";
import { mountTopBar } from "./topbar";

const TICK_MS = 1000;
const DEFAULT_DURATION_MINUTES = 12;
const SLOT_COUNT = 4;

const bar = mountTopBar();
initTheme(document.getElementById("btn-theme") as HTMLButtonElement);
const durationInput = document.getElementById("duration-minutes") as HTMLInputElement;
const slotsHost = document.getElementById("slots") as HTMLDivElement;
const ecoBtn = document.getElementById("preset-eco") as HTMLButtonElement;
const maxBtn = document.getElementById("preset-max") as HTMLButtonElement;

const panelHost = document.getElementById("panels")!;
const panels = new Map<string, TraderPanel>();
const states = new Map<string, TraderState>();
let stream: EventSource | null = null;
let tickTimer: number | null = null;
let durationSeconds = DEFAULT_DURATION_MINUTES * 60;
let catalog: ArenaConfigCatalog | null = null;
let selections: PresetSelection[] = []; // length === SLOT_COUNT once catalog loaded

bar.setRunning(false);
bar.setRemaining(durationSeconds);

fetchArenaConfig()
  .then((c) => {
    catalog = c;
    selections = [...c.presets.eco]; // default to eco preset (cheap)
    renderSlots();
  })
  .catch((err) => console.error("config fetch failed", err));

ecoBtn.addEventListener("click", () => applyPreset("eco"));
maxBtn.addEventListener("click", () => applyPreset("max"));

bar.startBtn.addEventListener("click", async () => {
  bar.startBtn.disabled = true;
  try {
    const minutes = clamp(parseInt(durationInput.value, 10) || DEFAULT_DURATION_MINUTES, 1, 240);
    durationInput.value = String(minutes);
    durationSeconds = minutes * 60;
    panels.clear();
    states.clear();
    panelHost.innerHTML = "";
    const snap = await startArena({ durationSeconds, selections });
    hydratePanels(snap);
    applySnapshot(snap);
    openEventStream();
    startTickLoop();
    bar.setRunning(true);
    setControlsDisabled(true);
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

function applyPreset(name: "eco" | "max"): void {
  if (!catalog) return;
  selections = [...catalog.presets[name]];
  renderSlots();
}

function renderSlots(): void {
  if (!catalog) return;
  slotsHost.innerHTML = "";
  for (let i = 0; i < SLOT_COUNT; i++) {
    slotsHost.append(slotElement(i));
  }
}

function slotElement(index: number): HTMLElement {
  const slot = document.createElement("div");
  slot.className = "slot";

  const sel = document.createElement("select");
  sel.className = "slot-model";
  for (const [id, spec] of Object.entries(catalog!.models)) {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = spec.display_name;
    sel.append(opt);
  }
  sel.value = selections[index].model_id;
  sel.addEventListener("change", () => {
    const newId = sel.value;
    const newOpts = catalog!.models[newId].reasoning_options;
    // If the previous reasoning label exists on the new model, keep it.
    const prevLabel = selections[index].reasoning_label;
    const keep = newOpts.find((o) => o.label === prevLabel)?.label ?? newOpts[0].label;
    selections[index] = { model_id: newId, reasoning_label: keep };
    renderSlots();
  });

  const reasoningRow = document.createElement("div");
  reasoningRow.className = "slot-reasoning";
  const opts = catalog!.models[selections[index].model_id].reasoning_options;
  for (const opt of opts) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "reasoning-btn";
    btn.textContent = opt.label;
    btn.dataset.active = String(selections[index].reasoning_label === opt.label);
    if (opts.length === 1) {
      btn.disabled = true; // single option — show as info, not interactive
    }
    btn.addEventListener("click", () => {
      selections[index] = { model_id: selections[index].model_id, reasoning_label: opt.label };
      renderSlots();
    });
    reasoningRow.append(btn);
  }

  slot.append(sel, reasoningRow);
  return slot;
}

function setControlsDisabled(disabled: boolean): void {
  durationInput.disabled = disabled;
  ecoBtn.disabled = disabled;
  maxBtn.disabled = disabled;
  for (const el of slotsHost.querySelectorAll("select, button")) {
    (el as HTMLButtonElement | HTMLSelectElement).disabled =
      disabled || (el as HTMLButtonElement).dataset?.lockedSingle === "true";
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
    panel.mount();
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
  setControlsDisabled(false);
}

function formatTraderLabel(name: string, reasoning: string): string {
  return reasoning ? `${name} (${reasoning})` : name;
}

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}
