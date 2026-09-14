use std::{
    collections::HashMap,
    fs,
    io::{Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    sync::Mutex,
    thread,
    time::Duration,
};

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::{process::CommandChild, ShellExt};
use tauri_plugin_updater::UpdaterExt;

struct BackendProcess(Mutex<Option<CommandChild>>);

const BACKEND_ADDR: &str = "127.0.0.1:8000";

fn backend_health_responds() -> bool {
    let Ok(address) = BACKEND_ADDR.parse::<SocketAddr>() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(250)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(250)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(250)));
    let request = concat!(
        "GET /health HTTP/1.1\r\n",
        "Host: 127.0.0.1:8000\r\n",
        "Connection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() || stream.flush().is_err() {
        return false;
    }
    let mut response = [0_u8; 64];
    let Ok(read) = stream.read(&mut response) else {
        return false;
    };
    let header = String::from_utf8_lossy(&response[..read]);
    header.starts_with("HTTP/1.1 200") || header.starts_with("HTTP/1.0 200")
}

fn ensure_backend_port_available() -> Result<(), Box<dyn std::error::Error>> {
    match TcpListener::bind(BACKEND_ADDR) {
        Ok(listener) => {
            drop(listener);
            Ok(())
        }
        Err(_error) if backend_health_responds() => Err(format!(
            "LangTextFlow is already running or its backend already owns {BACKEND_ADDR}. Close the existing LangTextFlow window before starting another instance."
        )
        .into()),
        Err(error) => Err(format!(
            "LangTextFlow cannot start because {BACKEND_ADDR} is already in use by another application: {error}. Close the conflicting application or free port 8000."
        )
        .into()),
    }
}

fn spawn_backend(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    ensure_backend_port_available()?;

    let app_data = app.path().app_data_dir()?;
    let data_dir = app_data.join("data");
    let cache_dir = app_data.join("cache");
    let runtime_dir = app_data.join("runtime");
    fs::create_dir_all(&data_dir)?;
    fs::create_dir_all(&cache_dir)?;
    fs::create_dir_all(&runtime_dir)?;

    let mut env = HashMap::new();
    env.insert(
        "LANGTEXTFLOW_APP_DATA".to_string(),
        app_data.to_string_lossy().into_owned(),
    );
    env.insert(
        "LANGTEXTFLOW_DATABASE_PATH".to_string(),
        data_dir.join("langtextflow.db").to_string_lossy().into_owned(),
    );
    env.insert(
        "LANGTEXTFLOW_MODEL_CACHE_DIR".to_string(),
        cache_dir.join("models").to_string_lossy().into_owned(),
    );
    env.insert(
        "LANGTEXTFLOW_MANAGED_RUNTIME_DIR".to_string(),
        runtime_dir.to_string_lossy().into_owned(),
    );
    env.insert("LANGTEXTFLOW_BACKEND_PORT".to_string(), "8000".to_string());
    env.insert("LANGTEXTFLOW_ENVIRONMENT".to_string(), "desktop".to_string());

    let command = app.shell().sidecar("langtextflow-server")?.envs(env);
    let (_events, child) = command.spawn()?;

    let state = app.state::<BackendProcess>();
    *state.0.lock().expect("backend process mutex poisoned") = Some(child);
    Ok(())
}

fn request_backend_shutdown() {
    let Ok(address) = BACKEND_ADDR.parse::<SocketAddr>() else {
        return;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(250)) else {
        return;
    };
    let _ = stream.set_write_timeout(Some(Duration::from_millis(250)));
    let request = concat!(
        "POST /api/v1/desktop/shutdown HTTP/1.1\r\n",
        "Host: 127.0.0.1:8000\r\n",
        "Content-Length: 0\r\n",
        "Connection: close\r\n\r\n"
    );
    let _ = stream.write_all(request.as_bytes());
    let _ = stream.flush();
}

fn wait_for_backend_shutdown(timeout: Duration) {
    let started = std::time::Instant::now();
    while started.elapsed() < timeout {
        if !backend_health_responds() {
            return;
        }
        thread::sleep(Duration::from_millis(100));
    }
}

fn stop_backend(app: &tauri::AppHandle) {
    let state = app.state::<BackendProcess>();
    let child = {
        let mut guard = state.0.lock().expect("backend process mutex poisoned");
        guard.take()
    };
    if let Some(child) = child {
        request_backend_shutdown();
        // Give FastAPI/Uvicorn lifespan enough time to drain post-processing and SQLite.
        // kill() remains a bounded fallback for a wedged backend.
        wait_for_backend_shutdown(Duration::from_millis(2500));
        let _ = child.kill();
    }
}

#[tauri::command]
async fn check_for_update(app: tauri::AppHandle) -> Result<Option<String>, String> {
    let updater = app.updater().map_err(|error| error.to_string())?;
    let update = updater.check().await.map_err(|error| error.to_string())?;
    Ok(update.map(|candidate| candidate.version))
}

#[tauri::command]
async fn install_update(app: tauri::AppHandle) -> Result<(), String> {
    let updater = app.updater().map_err(|error| error.to_string())?;
    let Some(update) = updater.check().await.map_err(|error| error.to_string())? else {
        return Err("No newer LangTextFlow update is available.".to_string());
    };

    // The updater verifies the downloaded artifact signature before returning the bytes.
    // We deliberately keep the current application untouched until this step succeeds.
    let bytes = update
        .download(|_, _| {}, || {})
        .await
        .map_err(|error| format!("Update download or signature verification failed: {error}"))?;

    request_backend_shutdown();
    wait_for_backend_shutdown(Duration::from_millis(2500));
    update
        .install(bytes)
        .map_err(|error| format!("Verified update could not be installed: {error}"))?;

    // Windows installers may terminate the current process themselves. On platforms
    // where install returns, request a normal Tauri restart so ExitRequested still
    // drains the backend lifecycle before the new binary starts.
    app.request_restart();
    Ok(())
}

pub fn run() {
    let app = tauri::Builder::default()
        .manage(BackendProcess(Mutex::new(None)))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![check_for_update, install_update])
        .setup(|app| {
            spawn_backend(&app.handle())?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building LangTextFlow desktop application");

    app.run(|app_handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            stop_backend(app_handle);
        }
    });
}
