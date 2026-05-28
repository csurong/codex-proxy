const BASE = "/admin/api";

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const opts: RequestInit = {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
  };
  if (body) opts.body = JSON.stringify(body);
  const resp = await fetch(`${BASE}${path}`, opts);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}: ${await resp.text()}`);
  return resp.json();
}

export const api = {
  // Status
  getStatus: () => request<any>("GET", "/status"),
  startProxy: () => request<any>("POST", "/proxy/start"),
  stopProxy: () => request<any>("POST", "/proxy/stop"),

  // Providers
  getProviders: () => request<any[]>("GET", "/providers"),
  createProvider: (data: any) => request<any>("POST", "/providers", data),
  updateProvider: (id: string, data: any) => request<any>("PATCH", `/providers/${id}`, data),
  deleteProvider: (id: string) => request<any>("DELETE", `/providers/${id}`),

  // Models
  getModels: (providerId?: string) => request<any[]>("GET", `/models${providerId ? `?provider_id=${providerId}` : ""}`),
  createModel: (data: any) => request<any>("POST", "/models", data),
  updateModel: (id: number, data: any) => request<any>("PATCH", `/models/${id}`, data),
  deleteModel: (id: number) => request<any>("DELETE", `/models/${id}`),

  // Logs
  getLogs: (limit = 50, offset = 0) => request<any[]>("GET", `/logs?limit=${limit}&offset=${offset}`),
  getLogStats: () => request<any>("GET", "/logs/stats"),

  // Settings
  getSettings: () => request<any>("GET", "/settings"),
  updateSettings: (data: any) => request<any>("PATCH", "/settings", data),

  // Test
  testConnection: (data: any) => request<any>("POST", "/test", data),
};
