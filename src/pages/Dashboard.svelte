<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";

  let status: any = null;
  let stats: any = null;
  let logs: any[] = [];
  let providers: any[] = [];
  let models: any[] = [];
  let settings: any = {};
  let loading = true;
  let showErrorsOnly = false;
  let refreshInterval: any;

  // API Key editing
  let editingKey = false;
  let apiKeyInput = "";
  let keySaving = false;
  let keySaved = false;
  let restoringCodex = false;
  let codexRestoreMessage = "";

  onMount(async () => {
    await refresh();
    loading = false;
    refreshInterval = setInterval(refresh, 5000);
    return () => clearInterval(refreshInterval);
  });

  async function refresh() {
    try {
      [status, stats, logs, providers, models, settings] = await Promise.all([
        api.getStatus(),
        api.getLogStats(),
        api.getLogs(30),
        api.getProviders(),
        api.getModels(),
        api.getSettings(),
      ]);
    } catch (e) { console.error(e); }
  }

  async function toggleProxy() {
    if (status?.running) {
      await api.stopProxy();
    } else {
      await api.startProxy();
    }
    await refresh();
  }

  // Active provider (first provider with models, typically mimo)
  $: activeProv = activeProvider || providers.find(p => models.some(m => m.provider_id === p.id));
  $: hasApiKey = !!(activeProv?.api_key_preview && activeProv.api_key_preview !== "null****");

  function startEditKey() {
    apiKeyInput = "";
    editingKey = true;
    keySaved = false;
  }

  function cancelEditKey() {
    editingKey = false;
    apiKeyInput = "";
  }

  async function saveApiKey() {
    if (!activeProv || !apiKeyInput.trim()) return;
    keySaving = true;
    try {
      await api.updateProvider(activeProv.id, { api_key: apiKeyInput.trim() });
      editingKey = false;
      apiKeyInput = "";
      keySaved = true;
      await refresh();
      setTimeout(() => keySaved = false, 3000);
    } catch (e) {
      console.error(e);
    }
    keySaving = false;
  }

  $: activeModelId = settings?.active_model_id ? parseInt(settings.active_model_id) : null;
  $: activeModel = activeModelId ? models.find(m => m.id === activeModelId) : null;
  $: activeProvider = activeModel ? providers.find(p => p.id === activeModel.provider_id) : null;

  async function selectModel(modelId: number | null) {
    await api.updateSettings({ active_model_id: modelId ? String(modelId) : "" });
    settings = { ...settings, active_model_id: modelId ? String(modelId) : "" };
  }

  async function toggleSetting(key: string, enabled: boolean) {
    const value = enabled ? "1" : "0";
    await api.updateSettings({ [key]: value });
    settings = { ...settings, [key]: value };
  }

  async function restoreCodexConfig() {
    restoringCodex = true;
    codexRestoreMessage = "";
    try {
      const result = await api.restoreCodexConfig();
      codexRestoreMessage = result.message || (result.ok ? "Restored" : "Restore failed");
    } catch (e: any) {
      codexRestoreMessage = e.message || "Restore failed";
    }
    restoringCodex = false;
  }

  function modelsFor(pid: string) {
    return models.filter(m => m.provider_id === pid);
  }

  $: filtered = showErrorsOnly ? logs.filter(l => l.status_code >= 400) : logs;
  $: thinkingDisabled = settings?.thinking_disabled === "1";
  $: forceHighEffort = settings?.thinking_force_high_effort === "1";
  $: visionRouteIncludeHistory = settings?.vision_route_include_history === "1";

  function fmtDuration(ms: number) {
    if (!ms) return "-";
    return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
  }
</script>

<div class="page-header">
  <h1>Dashboard</h1>
  <div style="display:flex;gap:8px;align-items:center;">
    <span class="status-dot {status?.running ? 'running' : 'stopped'}"></span>
    <span style="color:var(--text-dim);font-size:13px;">{status?.running ? 'Running' : 'Stopped'}</span>
    <button class="btn {status?.running ? 'btn-danger' : 'btn-success'}" on:click={toggleProxy}>
      {status?.running ? "⏹ Stop" : "▶ Start Proxy"}
    </button>
  </div>
</div>

<!-- API Key & Proxy Config -->
<div class="stats-grid" style="margin-bottom:16px;">
  <div class="card" style="grid-column: span 1;">
    <div class="card-title">Proxy URL</div>
    <code style="background:var(--bg);padding:6px 12px;border-radius:4px;font-size:13px;display:inline-block;">
      http://127.0.0.1:{status?.port ?? 18788}/v1
    </code>
    <div style="display:flex;gap:8px;align-items:center;margin-top:8px;flex-wrap:wrap;">
      <span style="color:var(--text-dim);font-size:11px;">Start proxy 会自动写入 ~/.codex/config.toml</span>
      <button class="btn btn-ghost btn-sm" on:click={restoreCodexConfig} disabled={restoringCodex}>
        {restoringCodex ? "Restoring..." : "Restore Codex Config"}
      </button>
    </div>
    {#if codexRestoreMessage}
      <div style="color:var(--text-dim);font-size:11px;margin-top:6px;">{codexRestoreMessage}</div>
    {/if}
  </div>
  <div class="card" style="grid-column: span 1;">
    <div class="card-title">API Key {#if activeProv}({activeProv.display_name || activeProv.id}){/if}</div>
    {#if editingKey}
      <form on:submit|preventDefault={saveApiKey} style="display:flex;gap:8px;align-items:center;">
        <input
          type="password"
          bind:value={apiKeyInput}
          placeholder="sk-..."
          style="flex:1;padding:6px 12px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);font-size:13px;font-family:monospace;"
        />
        <button class="btn btn-primary btn-sm" type="submit" disabled={keySaving || !apiKeyInput.trim()}>
          {keySaving ? '...' : 'Save'}
        </button>
        <button class="btn btn-ghost btn-sm" type="button" on:click={cancelEditKey}>Cancel</button>
      </form>
    {:else}
      <div style="display:flex;align-items:center;gap:10px;">
        {#if hasApiKey}
          <span style="font-family:monospace;font-size:13px;color:var(--green);">🔑 {activeProv.api_key_preview}</span>
        {:else}
          <span style="font-size:13px;color:var(--red);">⚠ Not set</span>
        {/if}
        <button class="btn btn-ghost btn-sm" on:click={startEditKey}>
          {hasApiKey ? 'Change' : 'Set Key'}
        </button>
        {#if keySaved}
          <span style="font-size:12px;color:var(--green);">✓ Saved</span>
        {/if}
      </div>
    {/if}
  </div>
  <div class="card" style="grid-column: span 1;">
    <div class="card-title">Thinking</div>
    <div style="display:grid;gap:10px;">
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
        <span style="font-size:13px;color:var(--text-dim);">Disable thinking</span>
        <button
          type="button"
          class="toggle {thinkingDisabled ? 'active' : ''}"
          title="Disable provider thinking mode"
          on:click={() => toggleSetting("thinking_disabled", !thinkingDisabled)}
        ></button>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
        <span style="font-size:13px;color:var(--text-dim);">Force high effort</span>
        <button
          type="button"
          class="toggle {forceHighEffort ? 'active' : ''}"
          title="Use high reasoning effort when thinking is enabled"
          on:click={() => toggleSetting("thinking_force_high_effort", !forceHighEffort)}
        ></button>
      </div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">
        <span style="font-size:13px;color:var(--text-dim);">Use history for vision routing</span>
        <button
          type="button"
          class="toggle {visionRouteIncludeHistory ? 'active' : ''}"
          title="Keep routing to a vision model when earlier messages in the conversation contain images"
          on:click={() => toggleSetting("vision_route_include_history", !visionRouteIncludeHistory)}
        ></button>
      </div>
    </div>
  </div>
</div>

<!-- Active Model Selector -->
<div class="card active-model-card">
  <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;">
    <div style="display:flex;align-items:center;gap:12px;">
      <span style="font-size:13px;color:var(--text-dim);text-transform:uppercase;letter-spacing:0.5px;">Active Model</span>
      {#if activeModel}
        <span class="active-model-badge">
          ⚡ {activeModel.display_name || activeModel.model_id}
        </span>
        {#if activeProvider}
          <span class="badge badge-purple">{activeProvider.display_name || activeProvider.id}</span>
        {/if}
      {:else}
        <span style="color:var(--yellow);font-size:13px;">⚠ No model selected</span>
      {/if}
    </div>
    <div class="model-select-group">
      {#each providers as p}
        {#if modelsFor(p.id).length > 0}
          <span style="font-size:11px;color:var(--text-dim);margin-right:4px;">{p.display_name || p.id}:</span>
          {#each modelsFor(p.id) as m}
            <button
              class="model-chip {activeModelId === m.id ? 'model-chip-active' : ''}"
              on:click={() => selectModel(activeModelId === m.id ? null : m.id)}
            >
              {#if activeModelId === m.id}⚡ {/if}{m.display_name || m.model_id}
            </button>
          {/each}
        {/if}
      {/each}
      {#if models.length === 0}
        <span style="font-size:12px;color:var(--text-dim);">No models configured. Go to Providers →</span>
      {/if}
    </div>
  </div>
</div>

<!-- Stats -->
<div class="stats-grid">
  <div class="card">
    <div class="card-title">Total Requests</div>
    <div class="stat-value">{stats?.total ?? 0}</div>
  </div>
  <div class="card">
    <div class="card-title">Total Tokens</div>
    <div class="stat-value">{((stats?.total_prompt_tokens ?? 0) + (stats?.total_completion_tokens ?? 0)).toLocaleString()}</div>
  </div>
  {#if status?.running}
    <div class="card">
      <div class="card-title">Uptime</div>
      <div class="stat-value">{Math.floor((status?.uptime || 0) / 60)}m {(status?.uptime || 0) % 60}s</div>
    </div>
  {/if}
</div>

<!-- Logs -->
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <div class="card-title" style="margin:0;">Request Logs</div>
    <div style="display:flex;gap:8px;align-items:center;">
      <label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:12px;color:var(--text-dim);">
        <input type="checkbox" bind:checked={showErrorsOnly} /> Errors only
      </label>
      <button class="btn btn-ghost btn-sm" on:click={refresh}>↻</button>
    </div>
  </div>

  {#if loading}
    <p style="color:var(--text-dim);">Loading...</p>
  {:else if filtered.length === 0}
    <p style="color:var(--text-dim);">No requests yet. Start the proxy and send a request from Codex.</p>
  {:else}
    <table>
      <thead>
        <tr>
          <th>Time</th>
          <th>Provider</th>
          <th>Requested</th>
          <th>Sent</th>
          <th>Upstream</th>
          <th>Status</th>
          <th>Tokens</th>
          <th>Duration</th>
          <th>Error</th>
        </tr>
      </thead>
      <tbody>
        {#each filtered as log}
          <tr>
            <td style="white-space:nowrap;font-size:12px;">{new Date(log.ts * 1000).toLocaleString()}</td>
            <td>{log.provider_id ?? "-"}</td>
            <td style="font-family:monospace;font-size:12px;">{log.model ?? "-"}</td>
            <td style="font-family:monospace;font-size:12px;">{log.request_model ?? ""}</td>
            <td style="font-family:monospace;font-size:12px;color:var(--text-dim);">{log.upstream_model ?? ""}</td>
            <td>
              <span class="badge {(log.status_code ?? 0) < 400 ? 'badge-green' : 'badge-red'}">
                {log.status_code ?? "-"}
              </span>
            </td>
            <td>{(log.prompt_tokens ?? 0) + (log.completion_tokens ?? 0)}</td>
            <td>{fmtDuration(log.duration_ms)}</td>
            <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px;color:var(--red);">
              {log.error_snippet ?? ""}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>
