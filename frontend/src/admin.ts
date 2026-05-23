// Admin user management overlay — list/add/delete users.

import { apiFetch } from "./apiClient";
import { getUser } from "./auth";

interface UserEntry {
  username: string;
  display_name: string;
  is_admin: boolean;
}

let _overlay: HTMLElement | null = null;

export function openAdminPanel(): void {
  if (_overlay) return;
  const user = getUser();
  if (!user?.is_admin) return;

  _overlay = document.createElement("div");
  _overlay.className = "admin-overlay";
  _overlay.innerHTML = `
    <div class="admin-panel">
      <div class="admin-header">
        <h2 class="admin-title">Manage Users</h2>
        <button class="admin-close" aria-label="Close">&times;</button>
      </div>
      <div class="admin-users" id="admin-users">Loading…</div>
      <div class="admin-add">
        <h3 class="admin-section-title">Add User</h3>
        <form class="admin-form" id="admin-add-form">
          <input type="email" id="admin-new-email" placeholder="Email"
                 required maxlength="100" />
          <input type="text" id="admin-new-name" placeholder="Display name"
                 required maxlength="100" />
          <div class="login-password-wrap">
            <input type="password" id="admin-new-pw" placeholder="Password (min 6)"
                   required minlength="6" maxlength="200" />
            <button type="button" class="login-toggle-pw" id="admin-toggle-pw"
                    aria-label="Show password" tabindex="-1">
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
          <button type="submit" class="btn btn-primary">Add</button>
        </form>
        <div class="admin-feedback" id="admin-feedback"></div>
      </div>
    </div>
  `;

  document.body.append(_overlay);

  _overlay
    .querySelector(".admin-close")!
    .addEventListener("click", closeAdminPanel);
  _overlay.addEventListener("click", (e) => {
    if (e.target === _overlay) closeAdminPanel();
  });

  const form = _overlay.querySelector("#admin-add-form") as HTMLFormElement;
  form.addEventListener("submit", handleAdd);

  const pwInput = _overlay.querySelector("#admin-new-pw") as HTMLInputElement;
  const pwToggle = _overlay.querySelector("#admin-toggle-pw") as HTMLButtonElement;
  const pwWrap = pwToggle.closest(".login-password-wrap") as HTMLElement;
  let showPw = false;
  pwToggle.addEventListener("click", () => {
    showPw = !showPw;
    pwInput.type = showPw ? "text" : "password";
    pwToggle.setAttribute("aria-label", showPw ? "Hide password" : "Show password");
    pwWrap.classList.toggle("pw-visible", showPw);
  });

  loadUsers();
}

export function closeAdminPanel(): void {
  _overlay?.remove();
  _overlay = null;
}

async function loadUsers(): Promise<void> {
  const container = document.getElementById("admin-users");
  if (!container) return;

  try {
    const res = await apiFetch("/api/admin/users");
    if (!res.ok) {
      container.textContent = "Failed to load users";
      return;
    }
    const users: UserEntry[] = await res.json();
    const currentUser = getUser();
    container.innerHTML = "";

    for (const u of users) {
      const row = document.createElement("div");
      row.className = "admin-user-row";
      const icon = u.is_admin
        ? `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="var(--yellow)" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>`
        : `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="var(--fg-muted)" stroke-width="2"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>`;
      const isSelf = u.username === currentUser?.username;
      row.innerHTML = `
        <span class="admin-user-icon">${icon}</span>
        <span class="admin-user-name">${esc(u.display_name)}</span>
        <span class="admin-user-email">${esc(u.username)}</span>
        ${isSelf ? "" : `<button class="admin-delete-btn" data-username="${esc(u.username)}" aria-label="Delete ${esc(u.username)}">&times;</button>`}
      `;
      container.append(row);
    }

    container.querySelectorAll(".admin-delete-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const username = (btn as HTMLElement).dataset.username!;
        handleDelete(username);
      });
    });
  } catch {
    container.textContent = "Network error";
  }
}

async function handleAdd(e: Event): Promise<void> {
  e.preventDefault();
  const feedback = document.getElementById("admin-feedback")!;
  const emailEl = document.getElementById("admin-new-email") as HTMLInputElement;
  const nameEl = document.getElementById("admin-new-name") as HTMLInputElement;
  const pwEl = document.getElementById("admin-new-pw") as HTMLInputElement;

  const email = emailEl.value.trim();
  const name = nameEl.value.trim();
  const pw = pwEl.value;

  feedback.textContent = "";
  feedback.className = "admin-feedback";

  try {
    const res = await apiFetch("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: email,
        display_name: name,
        password: pw,
      }),
    });
    if (res.ok) {
      feedback.textContent = `Added ${email}`;
      feedback.className = "admin-feedback admin-feedback-ok";
      (document.getElementById("admin-add-form") as HTMLFormElement).reset();
      loadUsers();
    } else {
      const data = await res.json();
      feedback.textContent = data.detail || "Failed to create user";
      feedback.className = "admin-feedback admin-feedback-err";
    }
  } catch {
    feedback.textContent = "Network error";
    feedback.className = "admin-feedback admin-feedback-err";
  }
}

async function handleDelete(username: string): Promise<void> {
  if (!confirm(`Remove ${username}?`)) return;
  const res = await apiFetch(
    `/api/admin/users/${encodeURIComponent(username)}`,
    { method: "DELETE" },
  );
  if (res.ok) loadUsers();
}

function esc(s: string): string {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}
