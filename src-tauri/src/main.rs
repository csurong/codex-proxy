// Codex-Proxy Desktop: Tauri shell with Python sidecar management.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::process::{Child, Command};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::Manager;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

struct Sidecar(Mutex<Option<Child>>);

const HEALTH_URL: &str = "http://127.0.0.1:18788/admin/api/status";
const HEALTH_TIMEOUT: Duration = Duration::from_secs(15);
const HEALTH_INTERVAL: Duration = Duration::from_millis(500);

fn new_instance_id() -> String {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    format!("{}-{}", std::process::id(), millis)
}

fn backend_matches_instance(instance_id: &str) -> bool {
    let Ok(resp) = reqwest::blocking::get(HEALTH_URL) else {
        return false;
    };
    if !resp.status().is_success() {
        return false;
    }
    let Ok(text) = resp.text() else {
        return false;
    };
    let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else {
        return false;
    };
    value
        .get("instance_id")
        .and_then(|v| v.as_str())
        .is_some_and(|id| id == instance_id)
}

fn stop_sidecar(app: &tauri::AppHandle) {
    let state = app.state::<Sidecar>();
    let mut guard = state.0.lock().unwrap();
    if let Some(mut child) = guard.take() {
        kill_child_tree(&mut child);
    }
}

fn kill_child_tree(child: &mut Child) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;

        let pid = child.id().to_string();
        let _ = Command::new("taskkill")
            .args(["/PID", &pid, "/T", "/F"])
            .creation_flags(CREATE_NO_WINDOW)
            .output();
    }

    let _ = child.kill();
    let _ = child.wait();
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Sidecar(Mutex::new(None)))
        .setup(|app| {
            let instance_id = new_instance_id();

            // Spawn Python sidecar
            let mut sidecar_cmd = if cfg!(debug_assertions) {
                // Dev mode: assume Python is available directly
                let mut cmd = Command::new("python3");
                cmd.args(["-m", "codex_proxy.main"]);
                cmd
            } else {
                // Release mode: use bundled binary
                let sidecar_name = if cfg!(windows) {
                    "codex-proxy.exe"
                } else {
                    "codex-proxy"
                };
                let sidecar_path = app.path().resource_dir()
                    .expect("Failed to resolve resource dir")
                    .join("binaries")
                    .join(sidecar_name);
                Command::new(sidecar_path)
            };

            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                sidecar_cmd.creation_flags(CREATE_NO_WINDOW);
            }

            let child = sidecar_cmd
                .env("CODEX_PROXY_NO_BROWSER", "1")
                .env("CODEX_PROXY_INSTANCE_ID", &instance_id)
                .env("CODEX_PROXY_PARENT_PID", std::process::id().to_string())
                .spawn()
                .expect("Failed to start Python sidecar");
            let mut child = child;

            // Wait for backend to be ready
            let start = Instant::now();
            while start.elapsed() < HEALTH_TIMEOUT {
                if backend_matches_instance(&instance_id) {
                    // Store child handle only after confirming this window owns the backend.
                    *app.state::<Sidecar>().0.lock().unwrap() = Some(child);

                    // Navigate to admin UI
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.navigate("http://127.0.0.1:18788/admin/".parse().unwrap());
                    }
                    return Ok(());
                }
                if let Ok(Some(status)) = child.try_wait() {
                    return Err(format!("Python sidecar exited before becoming ready: {status}").into());
                }
                thread::sleep(HEALTH_INTERVAL);
            }

            kill_child_tree(&mut child);
            Err("Timed out waiting for this Codex-Proxy backend instance to start. Close any old Codex-Proxy processes and try again.".into())
        })
        .on_window_event(|window, event| {
            // Kill sidecar on window close
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                let app = window.app_handle();
                stop_sidecar(app);
                app.exit(0);
            } else if matches!(event, tauri::WindowEvent::Destroyed) {
                stop_sidecar(window.app_handle());
            }
        })
        .build(tauri::generate_context!())
        .expect("Failed to build Tauri app")
        .run(|app, event| {
            if matches!(event, tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit) {
                stop_sidecar(app);
            }
        });
}
