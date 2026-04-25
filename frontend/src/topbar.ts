// Top bar: Start/Stop buttons and the countdown clock.

export function formatClock(remainingSeconds: number): string {
  const s = Math.max(0, Math.floor(remainingSeconds));
  const mm = Math.floor(s / 60).toString().padStart(2, "0");
  const ss = (s % 60).toString().padStart(2, "0");
  return `${mm}:${ss}`;
}

export interface TopBar {
  startBtn: HTMLButtonElement;
  stopBtn: HTMLButtonElement;
  clock: HTMLElement;
  setRunning(running: boolean): void;
  setRemaining(seconds: number): void;
}

export function mountTopBar(): TopBar {
  const startBtn = document.getElementById("btn-start") as HTMLButtonElement;
  const stopBtn = document.getElementById("btn-stop") as HTMLButtonElement;
  const clock = document.getElementById("clock")!;

  return {
    startBtn,
    stopBtn,
    clock,
    setRunning(running) {
      startBtn.disabled = running;
      stopBtn.disabled = !running;
    },
    setRemaining(seconds) {
      clock.textContent = formatClock(seconds);
    },
  };
}
