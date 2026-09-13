use std::{collections::HashMap, fs, sync::Mutex};

use tauri::{Manager, RunEvent};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

struct BackendProcess(Mutex<Option<CommandChild>>);

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
    env.insert("LANGTEXTFLOW_ENVIRONMENT".to_string(), "desktop".to_string());

    let command = app.shell().sidecar("langtextflow-server")?.envs(env);
    let (_events, child) = command.spawn()?;

    let state = app.state::<BackendProcess>();
    *state.0.lock().expect("backend process mutex poisoned") = Some(child);
    Ok(())
}

fn stop_backend(app: &tauri::AppHandle) {
    let state = app.state::<BackendProcess>();
    let child = {
        let mut guard = state.0.lock().expect("backend process mutex poisoned");
        guard.take()
    };
    if let Some(child) = child {
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
