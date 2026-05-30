# Codex-Proxy

[English](#english) | [中文](#中文)

## 中文

Codex-Proxy 是一个本地桌面代理，用来让 Codex CLI / Codex Desktop
连接更多 Chat Completions 兼容的上游模型服务，例如小米 MiMo、本地 vLLM
或你自己配置的 OpenAI-compatible provider。

它的核心作用是做协议和配置适配：

```text
Codex -> /v1/responses -> Codex-Proxy -> /chat/completions -> 上游模型服务
```

也就是说，Codex 仍然以 Responses API 的方式请求本地代理；Codex-Proxy
再把请求转换为常见的 Chat Completions 请求发给上游模型。这样你可以在本地
管理 provider、模型列表、图片模型路由、Web Search 转发和 Codex 配置写入，
不需要手动反复改 `~/.codex/auth.json` 与 `~/.codex/config.toml`。

### 主要功能

- 本地管理界面：`http://127.0.0.1:18788/admin/`
- 支持小米 MiMo、本地 vLLM、自定义 provider
- 支持按请求路由模型，也支持手动固定当前模型
- 支持 provider / model alias 配置
- 支持 MiMo `web_search` 转发；token-plan key 会自动降级，避免无效请求
- 请求包含图片时，如果当前模型不支持图片，会自动路由到同一 provider 下支持图片的模型
- 一键写入 / 恢复 Codex 配置，并保留时间戳备份
- 支持 Responses API 流式输出、tool calls、reasoning、usage 和请求日志
- 提供 macOS Tauri 桌面壳，内置 Python sidecar

### 快速开始

安装依赖：

```bash
make install
```

启动本地服务：

```bash
.venv/bin/python -m codex_proxy.main
```

打开管理界面：

```text
http://127.0.0.1:18788/admin/
```

然后：

1. 进入 **Providers**，配置 MiMo API Key、本地 vLLM 或自定义 provider。
2. 按需添加或修改模型。如果模型支持图片，把 `supports_images` 打开。
3. 回到 **Dashboard**，点击 **Start Proxy**。
4. 重启 Codex Desktop 或 Codex CLI，让它重新读取 `~/.codex/auth.json` 与 `~/.codex/config.toml`。

点击 **Start Proxy** 后，项目会写入本地 Codex provider 配置，并保留带时间戳的备份。
如果需要回滚，可以在 Dashboard 使用 **Restore Codex Config**。

### 开发

后端热重载：

```bash
make dev-py
```

前端 Vite HMR：

```bash
make dev-web
```

构建前端静态文件：

```bash
npm run build
```

运行测试：

```bash
make test
```

### 桌面安装包

依赖：

- Python 3.10+
- Node.js 和 npm
- Rust/Cargo

macOS / Linux 构建 release 产物：

```bash
make build-all
```

Windows PowerShell 构建 release 产物：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build-windows.ps1
```

macOS bundle 输出目录：

```text
src-tauri/target/release/bundle/
```

Windows installer 输出目录：

```text
src-tauri\target\release\bundle\
```

Python sidecar 输出位置：

```text
src-tauri/binaries/codex-proxy
```

Windows 下 sidecar 输出位置是：

```text
src-tauri\binaries\codex-proxy.exe
```

### 注意事项

- API Key 仅保存在本机 SQLite 数据库中，默认路径为 `~/.codex-proxy/data`。
- 管理 API 默认只绑定 `127.0.0.1`，但没有额外鉴权，建议仅个人本地使用。
- 本地构建的 macOS 安装包默认是 ad-hoc signing；正式分发需要配置 Apple Developer ID 和 notarization。
- Windows 安装包建议在 Windows 环境构建，这样 PyInstaller 可以生成原生 `.exe` sidecar。

## English

Codex-Proxy is a local desktop proxy that lets Codex CLI / Codex Desktop use
Chat Completions-compatible upstream model providers, such as Xiaomi MiMo,
local vLLM, or your own OpenAI-compatible provider.

Its main job is protocol and configuration adaptation:

```text
Codex -> /v1/responses -> Codex-Proxy -> /chat/completions -> upstream provider
```

Codex continues to talk to a local Responses API endpoint. Codex-Proxy then
translates those requests into Chat Completions requests for the upstream
provider. This lets you manage providers, models, image-capable routing,
Web Search forwarding, and Codex config writes locally without repeatedly
editing `~/.codex/auth.json` and `~/.codex/config.toml` by hand.

### Features

- Local admin UI at `http://127.0.0.1:18788/admin/`
- Xiaomi MiMo, local vLLM, and custom provider configuration
- Per-request model routing with optional active-model override
- Provider and model alias support through provider config JSON
- MiMo `web_search` forwarding; token-plan keys are safely downgraded
- Automatic image request routing to an image-capable model in the same provider
- One-click Codex config apply/restore with timestamped backups
- Streaming Responses API translation, tool calls, reasoning, usage, and request logs
- macOS Tauri desktop shell with a bundled Python sidecar

### Quick Start

Install dependencies once:

```bash
make install
```

Run the local app:

```bash
.venv/bin/python -m codex_proxy.main
```

Open the admin UI:

```text
http://127.0.0.1:18788/admin/
```

Then:

1. Go to **Providers** and configure your MiMo API key, local vLLM, or custom provider.
2. Add or edit models as needed. Enable `supports_images` for image-capable models.
3. Go to **Dashboard** and click **Start Proxy**.
4. Restart Codex Desktop or Codex CLI so it reloads `~/.codex/auth.json` and `~/.codex/config.toml`.

`Start Proxy` writes a local Codex provider config and keeps timestamped backups.
Use **Restore Codex Config** in the Dashboard to roll back to the latest backup.

### Development

Backend with reload:

```bash
make dev-py
```

Frontend with Vite HMR:

```bash
make dev-web
```

Production frontend build:

```bash
npm run build
```

Tests:

```bash
make test
```

### Desktop Build

Requirements:

- Python 3.10+
- Node.js and npm
- Rust/Cargo

Build all release artifacts on macOS / Linux:

```bash
make build-all
```

Build all release artifacts on Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build-windows.ps1
```

The macOS bundle is written under:

```text
src-tauri/target/release/bundle/
```

Windows installers are written under:

```text
src-tauri\target\release\bundle\
```

The Python sidecar is built into:

```text
src-tauri/binaries/codex-proxy
```

On Windows the sidecar is built into:

```text
src-tauri\binaries\codex-proxy.exe
```

### Notes

- API keys are stored locally in SQLite under `~/.codex-proxy/data`.
- The admin API is unauthenticated and binds to `127.0.0.1` by default, so this project is intended for personal local use.
- macOS release builds made locally are ad-hoc signed unless you configure an Apple Developer ID certificate and notarization.
- Windows installers should be built on Windows so PyInstaller can produce a native `.exe` sidecar.

## License

MIT License. See [LICENSE](LICENSE).
