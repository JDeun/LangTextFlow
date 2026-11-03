use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicU64, Ordering};

pub struct Fixture(pub PathBuf);
impl Fixture {
    pub fn new(mode: &str) -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "ltf-history-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        let fixture = Self(path);
        fixture.python(mode);
        fixture
    }
    pub fn db(&self) -> PathBuf {
        self.0.join("history.db")
    }
    pub fn output(&self, mode: &str) -> String {
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .to_owned();
        let interpreter = std::env::var_os("LTF_HISTORY_PYTHON")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                root.join(if cfg!(windows) {
                    ".venv/Scripts/python.exe"
                } else {
                    ".venv/bin/python"
                })
            });
        let result = Command::new(interpreter)
            .arg(root.join("crates/langtextflow-core/tests/history_python.py"))
            .arg(mode)
            .arg(&self.0)
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "Python contract {mode}: {}",
            String::from_utf8_lossy(&result.stderr)
        );
        String::from_utf8(result.stdout).unwrap()
    }
    pub fn python(&self, mode: &str) {
        self.output(mode);
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}
