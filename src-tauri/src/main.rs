// Codex-Proxy Desktop: Tauri shell with Python sidecar management.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Command, Child};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};
use tauri::{Manager, Emitter};

struct Sidecar(Mutex<Option<Child>>);

const HEALTH_URL: &str = "http://127.0.0.1:18788/admin/api/status";
const HEALTH_TIMEOUT: Duration = Duration::from_secs(15);
const HEALTH_INTERVAL: Duration = Duration::from_millis(500);

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Sidecar(Mutex::new(None)))
        .setup(|app| {
            // Spawn Python sidecar
            let sidecar_cmd = if cfg!(debug_assertions) {
                // Dev mode: assume Python is available directly
                let mut cmd = Command::new("python3");
                cmd.args(["-m", "codex_proxy.main"]);
                cmd
            } else {
                // Release mode: use bundled binary
                let sidecar_path = app.path().resource_dir()
                    .expect("Failed to resolve resource dir")
                    .join("codex-proxy");
                Command::new(sidecar_path)
            };

            let child = sidecar_cmd
                .env("CODEX_PROXY_NO_BROWSER", "1")
                .spawn()
                .expect("Failed to start Python sidecar");

            // Store child handle
            *app.state::<Sidecar>().0.lock().unwrap() = Some(child);

            // Wait for backend to be ready
            let start = Instant::now();
            while start.elapsed() < HEALTH_TIMEOUT {
                if let Ok(resp) = reqwest::blocking::get(HEALTH_URL) {
                    if resp.status().is_success() {
                        break;
                    }
                }
                thread::sleep(HEALTH_INTERVAL);
            }

            // Navigate to admin UI
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.navigate("http://127.0.0.1:18788/admin/".parse().unwrap());
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            // Kill sidecar on window close
            if let tauri::WindowEvent::Destroyed = event {
                let app = window.app_handle();
                let mut guard = app.state::<Sidecar>().0.lock().unwrap();
                if let Some(ref mut child) = *guard {
                    let _ = child.kill();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("Failed to run Tauri app");
}
