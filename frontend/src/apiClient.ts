// Fetch wrapper: injects JWT Authorization header, auto-logout on 401.

const TOKEN_KEY = "tradewars-auth-token";
const USER_KEY = "tradewars-auth-user";

export async function apiFetch(
  input: string,
  init: RequestInit = {},
): Promise<Response> {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  const headers: Record<string, string> = {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...((init.headers as Record<string, string>) || {}),
  };
  const res = await fetch(input, { ...init, headers });

  if (res.status === 401 && !input.includes("/api/auth/")) {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    window.dispatchEvent(new CustomEvent("auth-expired"));
  }

  return res;
}

export function apiPost(
  input: string,
  body?: unknown,
): Promise<Response> {
  return apiFetch(input, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
}

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) || "";
}
