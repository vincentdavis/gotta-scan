fn main() {
    // Expose the build target triple to the crate so the sidecar resolver can
    // find the dev-mode binaries in `bin/<name>-<triple>`.
    println!(
        "cargo:rustc-env=TARGET_TRIPLE={}",
        std::env::var("TARGET").unwrap()
    );
    tauri_build::build()
}
