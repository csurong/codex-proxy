# Codex-Proxy

Desktop proxy that lets Codex CLI/Desktop work with **Xiaomi MiMo** and **Qwen (vLLM)** models.

## Quick Start (Development)

```bash
# Install dependencies
make install

# Terminal 1: Python backend
make dev-py

# Terminal 2: Svelte frontend (optional, for HMR)
make dev-web
```

Open http://127.0.0.1:18788/admin/ in your browser.

## Usage

1. Open Codex-Proxy
2. Go to **Providers**, fill in your MiMo API key
3. Click **Start Proxy** on the Dashboard
4. Copy the proxy URL to your Codex config:

```toml
model = "mimo-v2.5-pro"
model_provider = "mimo"

[model_providers.mimo]
name = "MiMo (via Codex-Proxy)"
base_url = "http://127.0.0.1:18788/v1"
wire_api = "responses"
```

## Architecture

```
Codex → Responses API → Codex-Proxy (Python/FastAPI) → Chat Completions → MiMo/vLLM
                              ↕
                    Admin UI (Svelte) at /admin/
```

## Build Desktop App

Requires: Rust, Node.js, Python 3.10+

```bash
make build-all
```

## Tests

```bash
make test
```

## Project Structure

```
codex_proxy/       Python backend (FastAPI)
src/               Svelte frontend
src-tauri/         Tauri desktop shell (Rust)
static/            Built frontend output
tests-py/          Python tests
```
