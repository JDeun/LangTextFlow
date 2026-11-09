//! Actual shipped Rust subprocess reached through the unchanged operator/audio API.
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

#[test]
fn current_python_api_routes_capture_to_rust_and_reads_its_canonical_history() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .to_owned();
    let python = std::env::var_os("LTF_HISTORY_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            root.join(if cfg!(windows) {
                ".venv/Scripts/python.exe"
            } else {
                ".venv/bin/python"
            })
        });
    let mut child = Command::new(python)
        .args([
            "-m",
            "pytest",
            "-q",
            "-x",
            "apps/server/tests/test_rust_runtime_bridge.py",
        ])
        .current_dir(root)
        .env(
            "LTF_RUNTIME_BINARY",
            env!("CARGO_BIN_EXE_langtextflow-runtime"),
        )
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(90);
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            assert!(status.success(), "actual runtime/API contract failed");
            return;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            panic!("actual runtime/API contract exceeded bounded deadline");
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}
