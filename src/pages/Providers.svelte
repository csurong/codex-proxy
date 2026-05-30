<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../lib/api";

  let providers: any[] = [];
  let models: any[] = [];
  let settings: any = {};
  let loading = true;
  let testingProviderId: string | null = null;
  let providerTestResults: Record<string, string> = {};

  // Provider form
  let showProviderModal = false;
  let editProviderId: string | null = null;
  let pForm: any = { type: "custom", display_name: "", base_url: "", api_key: "", config: {} };

  // Model form
  let showModelModal = false;
  let editModelId: number | null = null;
  let mForm: any = { provider_id: "", model_id: "", display_name: "", supports_images: false, supports_reasoning: false, supports_tools: false, context_window: null, max_output_tokens: null };

  onMount(load);

  async function load() {
    loading = true;
    [providers, models, settings] = await Promise.all([api.getProviders(), api.getModels(), api.getSettings()]);
    loading = false;
  }

  $: activeModelId = settings.active_model_id ? parseInt(settings.active_model_id) : null;

  async function setActiveModel(modelId: number | null) {
    await api.updateSettings({ active_model_id: modelId ? String(modelId) : "" });
    settings = { ...settings, active_model_id: modelId ? String(modelId) : "" };
  }

  function modelsFor(pid: string) {
    return models.filter(m => m.provider_id === pid);
  }

  // ── Provider actions ──

  function openAddProvider(ptype: string) {
    editProviderId = null;
    pForm = { type: ptype, display_name: "", base_url: "", api_key: "", config: {} };
    if (ptype === "mimo") pForm.display_name = "MiMo";
    if (ptype === "vllm") { pForm.display_name = "Qwen (vLLM)"; pForm.config = { enable_thinking: false }; }
    showProviderModal = true;
  }

  function openEditProvider(p: any) {
    editProviderId = p.id;
    pForm = { type: p.type, display_name: p.display_name, base_url: p.base_url, api_key: "", config: p.config_json ? JSON.parse(p.config_json) : {} };
    showProviderModal = true;
  }

  async function saveProvider() {
    const payload = { ...pForm };
    if (editProviderId && !payload.api_key) delete payload.api_key;
    if (editProviderId) {
      await api.updateProvider(editProviderId, payload);
    } else {
      await api.createProvider(payload);
    }
    showProviderModal = false;
    await load();
  }

  async function testProvider(id: string) {
    testingProviderId = id;
    providerTestResults = { ...providerTestResults, [id]: "Testing..." };
    try {
      const result = await api.testProvider(id);
      providerTestResults = {
        ...providerTestResults,
        [id]: result.ok ? `Connected: ${result.model || "ok"}` : `Failed: ${result.error || "unknown error"}`,
      };
    } catch (e: any) {
      providerTestResults = { ...providerTestResults, [id]: e.message || "Failed" };
    }
    testingProviderId = null;
  }

  async function deleteProvider(id: string) {
    if (!confirm("Delete this provider and all its models?")) return;
    await api.deleteProvider(id);
    await load();
  }

  // ── Model actions ──

  function openAddModel(pid: string) {
    editModelId = null;
    mForm = { provider_id: pid, model_id: "", display_name: "", supports_images: false, supports_reasoning: false, supports_tools: false, context_window: null, max_output_tokens: null };
    showModelModal = true;
  }

  async function saveModel() {
    const data = { ...mForm, supports_images: mForm.supports_images ? 1 : 0, supports_reasoning: mForm.supports_reasoning ? 1 : 0, supports_tools: mForm.supports_tools ? 1 : 0 };
    await api.createModel(data);
    showModelModal = false;
    await load();
  }

  async function deleteModel(id: number) {
    if (!confirm("Delete this model?")) return;
    await api.deleteModel(id);
    await load();
  }

</script>

<div class="page-header">
  <h1>Providers & Models</h1>
  <div style="display:flex;gap:8px;">
    <button class="btn btn-primary" on:click={() => openAddProvider("mimo")}>+ MiMo</button>
    <button class="btn btn-primary" on:click={() => openAddProvider("vllm")}>+ Qwen/vLLM</button>
    <button class="btn btn-ghost" on:click={() => openAddProvider("custom")}>+ Custom</button>
  </div>
</div>

{#if loading}
  <p style="color:var(--text-dim);">Loading...</p>
{:else}
  {#each providers as p}
    <div class="card">
      <!-- Provider header -->
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
        <div style="display:flex;align-items:center;gap:10px;">
          <span style="font-size:16px;font-weight:600;">{p.display_name}</span>
          <span class="badge badge-purple">{p.type}</span>
          {#if p.api_key_preview}
            <span style="font-size:11px;color:var(--text-dim);">🔑 {p.api_key_preview}</span>
          {/if}
          {#if p.base_url}
            <span style="font-size:11px;color:var(--text-dim);font-family:monospace;">{p.base_url}</span>
          {/if}
        </div>
        <div style="display:flex;gap:6px;">
          <button class="btn btn-ghost btn-sm" on:click={() => openEditProvider(p)}>Edit</button>
          <button class="btn btn-ghost btn-sm" on:click={() => testProvider(p.id)} disabled={testingProviderId === p.id}>
            {testingProviderId === p.id ? "Testing..." : "Test"}
          </button>
          <button class="btn btn-primary btn-sm" on:click={() => openAddModel(p.id)}>+ Model</button>
          {#if p.type === "custom"}
            <button class="btn btn-danger btn-sm" on:click={() => deleteProvider(p.id)}>Delete</button>
          {/if}
        </div>
      </div>
      {#if providerTestResults[p.id]}
        <div style="font-size:12px;color:{providerTestResults[p.id].startsWith('Connected') ? 'var(--green)' : 'var(--text-dim)'};margin-bottom:10px;">
          {providerTestResults[p.id]}
        </div>
      {/if}

      <!-- Models table -->
      {#if modelsFor(p.id).length === 0}
        <p style="color:var(--text-dim);font-size:13px;">No models configured.</p>
      {:else}
        <table>
          <thead>
            <tr><th style="width:60px;">Active</th><th>Model ID</th><th>Name</th><th>Capabilities</th><th>Context</th><th>Max Output</th><th></th></tr>
          </thead>
          <tbody>
            {#each modelsFor(p.id) as m}
              <tr style="{activeModelId === m.id ? 'background:rgba(99,102,241,0.06);' : ''}">
                <td style="text-align:center;">
                  <button
                    class="toggle {activeModelId === m.id ? 'active' : ''}"
                    on:click={() => setActiveModel(activeModelId === m.id ? null : m.id)}
                    title="{activeModelId === m.id ? 'Click to deactivate' : 'Click to set as active model'}"
                  ></button>
                </td>
                <td style="font-family:monospace;font-size:13px;">
                  {#if activeModelId === m.id}<span style="color:var(--accent);font-weight:600;">⚡ </span>{/if}{m.model_id}
                </td>
                <td>{m.display_name ?? "-"}</td>
                <td>
                  {#if m.supports_images}<span class="badge badge-blue">Vision</span>{/if}
                  {#if m.supports_reasoning}<span class="badge badge-purple">Reasoning</span>{/if}
                  {#if m.supports_tools}<span class="badge badge-green">Tools</span>{/if}
                </td>
                <td>{m.context_window ? (m.context_window / 1000) + "K" : "-"}</td>
                <td>{m.max_output_tokens ? (m.max_output_tokens / 1000) + "K" : "-"}</td>
                <td>
                  <button class="btn btn-danger btn-sm" on:click={() => deleteModel(m.id)}>×</button>
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </div>
  {/each}
{/if}

<!-- Provider modal -->
{#if showProviderModal}
  <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
  <div class="modal-overlay" on:click={() => showProviderModal = false}>
    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
    <div class="modal" on:click|stopPropagation>
      <h2>{editProviderId ? "Edit" : "Add"} {pForm.type === "mimo" ? "MiMo" : pForm.type === "vllm" ? "Qwen/vLLM" : "Custom"} Provider</h2>
      <div class="form-group">
        <label for="p-name">Display Name</label>
        <input id="p-name" bind:value={pForm.display_name} />
      </div>
      {#if pForm.type !== "mimo"}
        <div class="form-group">
          <label for="p-url">Base URL</label>
          <input id="p-url" bind:value={pForm.base_url} placeholder="http://localhost:8000/v1" />
        </div>
      {/if}
      <div class="form-group">
        <label for="p-key">API Key {pForm.type === "mimo" ? "(required)" : "(optional)"}</label>
        <input id="p-key" type="password" bind:value={pForm.api_key} placeholder={pForm.type === "mimo" ? "sk-..." : "Leave empty if no auth"} />
      </div>
      {#if pForm.type === "vllm"}
        <div class="form-group">
          <label for="p-model">Default Model</label>
          <input id="p-model" bind:value={pForm.config.model} placeholder="qwq-32b" />
        </div>
        <div class="form-group" style="display:flex;align-items:center;gap:10px;">
          <label for="p-think" style="margin:0;">Enable Thinking</label>
          <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
          <div id="p-think" class="toggle {pForm.config.enable_thinking ? 'active' : ''}" role="button" tabindex="0" on:click={() => pForm.config.enable_thinking = !pForm.config.enable_thinking}></div>
        </div>
      {/if}
      <div class="modal-actions">
        <button class="btn btn-ghost" on:click={() => showProviderModal = false}>Cancel</button>
        <button class="btn btn-primary" on:click={saveProvider}>{editProviderId ? "Save" : "Add"}</button>
      </div>
    </div>
  </div>
{/if}

<!-- Model modal -->
{#if showModelModal}
  <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
  <div class="modal-overlay" on:click={() => showModelModal = false}>
    <!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
    <div class="modal" on:click|stopPropagation>
      <h2>Add Model</h2>
      <div class="form-group">
        <label for="m-id">Model ID</label>
        <input id="m-id" bind:value={mForm.model_id} placeholder="mimo-v2.5-pro" />
      </div>
      <div class="form-group">
        <label for="m-name">Display Name</label>
        <input id="m-name" bind:value={mForm.display_name} />
      </div>
      <div class="form-group">
        <label for="m-ctx">Context Window</label>
        <input id="m-ctx" type="number" bind:value={mForm.context_window} placeholder="128000" />
      </div>
      <div class="form-group">
        <label for="m-max">Max Output Tokens</label>
        <input id="m-max" type="number" bind:value={mForm.max_output_tokens} placeholder="32768" />
      </div>
      <div style="display:flex;gap:16px;margin-bottom:16px;">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
          <input type="checkbox" bind:checked={mForm.supports_images} /> Vision
        </label>
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
          <input type="checkbox" bind:checked={mForm.supports_reasoning} /> Reasoning
        </label>
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
          <input type="checkbox" bind:checked={mForm.supports_tools} /> Tools
        </label>
      </div>
      <div class="modal-actions">
        <button class="btn btn-ghost" on:click={() => showModelModal = false}>Cancel</button>
        <button class="btn btn-primary" on:click={saveModel}>Add</button>
      </div>
    </div>
  </div>
{/if}
