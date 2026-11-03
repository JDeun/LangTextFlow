fn main() {
    if let Err(error) = langtextflow_core::runtime_control::run_stdio() {
        eprintln!("rust_runtime_failed:{error}");
        std::process::exit(1);
    }
}
