// Dark/light theme toggle persisted in localStorage. Dark is the default.

const KEY = "tradewars-theme";
type Theme = "dark" | "light";

function apply(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
}

export function initTheme(button: HTMLButtonElement): void {
  const saved = (localStorage.getItem(KEY) as Theme | null) ?? "dark";
  apply(saved);
  button.addEventListener("click", () => {
    const next: Theme =
      document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    apply(next);
    localStorage.setItem(KEY, next);
  });
}
