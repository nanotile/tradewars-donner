// Minimal toast notification — fixed top-right, auto-dismiss after 5s.

const DISMISS_MS = 5000;

let container: HTMLDivElement | null = null;

function ensureContainer(): HTMLDivElement {
  if (container) return container;
  container = document.createElement("div");
  container.id = "toast-container";
  document.body.append(container);
  return container;
}

export function showToast(message: string, level: "error" | "info" = "error"): void {
  const host = ensureContainer();
  const el = document.createElement("div");
  el.className = `toast toast-${level}`;
  el.textContent = message;

  el.addEventListener("click", () => el.remove());
  host.append(el);

  requestAnimationFrame(() => el.classList.add("toast-visible"));

  setTimeout(() => {
    el.classList.remove("toast-visible");
    el.addEventListener("transitionend", () => el.remove(), { once: true });
    setTimeout(() => el.remove(), 400);
  }, DISMISS_MS);
}
