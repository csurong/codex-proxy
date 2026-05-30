.PHONY: dev-py dev-web build test install

# Development
dev-py:
	.venv/bin/python -m uvicorn codex_proxy.app:app --reload --host 127.0.0.1 --port 18788

dev-web:
	npm run dev

# Install dependencies
install:
	python3 -m venv .venv
	.venv/bin/pip install -e ".[dev]"
	npm install

# Build
build-web:
	npm run build

build-py:
	PYINSTALLER_CONFIG_DIR=build/pyinstaller-config .venv/bin/pyinstaller --clean --onefile --name codex-proxy --distpath src-tauri/binaries --add-data "static:static" codex_proxy_launcher.py

build-desktop:
	npm run tauri build

build-all: build-web build-py build-desktop

# Test
test:
	.venv/bin/python -m pytest tests-py/ -v
