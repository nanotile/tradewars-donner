// Full-screen login page matching Tradewars dark/light theme.

import { login } from "./auth";

let _loginRoot: HTMLElement | null = null;

export function showLoginPage(onSuccess: () => void): void {
  if (_loginRoot) return;

  _loginRoot = document.createElement("div");
  _loginRoot.className = "login-screen";
  _loginRoot.innerHTML = `
    <div class="login-card">
      <div class="login-brand">
        <svg class="login-icon" viewBox="0 0 24 24" width="32" height="32" fill="none"
             stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/>
          <polyline points="16 7 22 7 22 13"/>
        </svg>
        <span class="login-title">Tradewars</span>
      </div>
      <p class="login-subtitle">Sign in to access the arena</p>
      <form class="login-form" autocomplete="on">
        <div class="login-error" id="login-error"></div>
        <label class="login-field">
          <span class="login-label">Email</span>
          <input id="login-email" type="email" autocomplete="email" autofocus
                 placeholder="you@email.com" />
        </label>
        <label class="login-field">
          <span class="login-label">Password</span>
          <div class="login-password-wrap">
            <input id="login-password" type="password" autocomplete="current-password"
                   placeholder="Enter password" />
            <button type="button" class="login-toggle-pw" aria-label="Show password" tabindex="-1">
              <svg class="pw-eye" viewBox="0 0 24 24" width="16" height="16" fill="none"
                   stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/>
                <circle cx="12" cy="12" r="3"/>
              </svg>
              <svg class="pw-eye-off" viewBox="0 0 24 24" width="16" height="16" fill="none"
                   stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M17.9 17.4C16.2 18.8 14.2 20 12 20 5 20 1 12 1 12s2.7-5.4 6.5-7.6"/>
                <path d="M9.9 4.2A10.5 10.5 0 0 1 12 4c7 0 11 8 11 8s-1.1 2.2-3 4.3"/>
                <line x1="1" y1="1" x2="23" y2="23"/>
                <path d="M14.1 14.1a3 3 0 1 1-4.2-4.2"/>
              </svg>
            </button>
          </div>
        </label>
        <button type="submit" class="login-submit btn btn-primary" disabled>
          Sign In
        </button>
      </form>
    </div>
  `;

  document.body.prepend(_loginRoot);

  const emailInput = _loginRoot.querySelector("#login-email") as HTMLInputElement;
  const pwInput = _loginRoot.querySelector("#login-password") as HTMLInputElement;
  const form = _loginRoot.querySelector(".login-form") as HTMLFormElement;
  const submitBtn = _loginRoot.querySelector(".login-submit") as HTMLButtonElement;
  const errorDiv = _loginRoot.querySelector("#login-error") as HTMLElement;
  const toggleBtn = _loginRoot.querySelector(".login-toggle-pw") as HTMLButtonElement;

  let showPw = false;

  function updateSubmitState(): void {
    submitBtn.disabled = !emailInput.value.trim() || !pwInput.value;
  }

  emailInput.addEventListener("input", updateSubmitState);
  pwInput.addEventListener("input", updateSubmitState);

  toggleBtn.addEventListener("click", () => {
    showPw = !showPw;
    pwInput.type = showPw ? "text" : "password";
    toggleBtn.setAttribute(
      "aria-label",
      showPw ? "Hide password" : "Show password",
    );
    _loginRoot!.classList.toggle("pw-visible", showPw);
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (submitBtn.disabled) return;
    submitBtn.disabled = true;
    submitBtn.textContent = "Signing in…";
    errorDiv.textContent = "";
    errorDiv.style.display = "none";

    const result = await login(emailInput.value.trim(), pwInput.value);

    if (result.ok) {
      onSuccess();
    } else {
      errorDiv.textContent = result.error || "Login failed";
      errorDiv.style.display = "flex";
      submitBtn.disabled = false;
      submitBtn.textContent = "Sign In";
    }
  });
}

export function hideLoginPage(): void {
  _loginRoot?.remove();
  _loginRoot = null;
}
