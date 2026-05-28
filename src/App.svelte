<script lang="ts">
  import Dashboard from "./pages/Dashboard.svelte";
  import Providers from "./pages/Providers.svelte";

  let current = window.location.hash.slice(1) || "/";

  window.addEventListener("hashchange", () => {
    current = window.location.hash.slice(1) || "/";
  });

  function nav(path: string) {
    window.location.hash = path;
  }

  const pages: Record<string, any> = {
    "/": Dashboard,
    "/providers": Providers,
  };

  $: component = pages[current] || Dashboard;
</script>

<div class="app-layout">
  <aside class="sidebar">
    <div class="sidebar-brand">⚡ Codex-Proxy</div>
    <nav>
      <a href="#/" class:active={current === "/"} on:click|preventDefault={() => nav("/")}>
        📊 Dashboard
      </a>
      <a href="#/providers" class:active={current === "/providers"} on:click|preventDefault={() => nav("/providers")}>
        🔌 Providers
      </a>
    </nav>
  </aside>
  <main class="main">
    <svelte:component this={component} />
  </main>
</div>
