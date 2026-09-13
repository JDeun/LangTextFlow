use std::{
    collections::HashMap,
    fs,
    io::Write,
    net::{SocketAddr, TcpStream},
    sync::Mutex,
    thread,
    time::Duration,
};

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

struct BackendProcess(Mutex<Option<CommandChild>>);

const BACKEND_ADDR: &str = "127.0.0.1:8000";

fn spawn_backend(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
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
        thread::sleep(Duration::from_millis(2500));
        let _ = child.kill();
    }
}

pub fn run() {
    let app = tauri::Builder::default()
        .manage(BackendProcess(Mutex::new(None)))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
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
