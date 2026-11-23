// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
use std::path::PathBuf;
use std::process::Command;

/// Directory of the Python sidecar. Dev: resolved relative to this crate.
/// (Distribution will bundle the sidecar as a packaged binary instead.)
fn sidecar_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("sidecar")
}

/// Run the Python sidecar with the given args and return its stdout JSON string.
fn run_sidecar(args: &[&str]) -> Result<String, String> {
    let mut full = vec!["run", "python", "cli.py"];
    full.extend_from_slice(args);

    let output = Command::new("uv")
        .current_dir(sidecar_dir())
        .args(&full)
        .output()
        .map_err(|e| format!("failed to launch sidecar: {e}"))?;

    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if output.status.success() {
        Ok(stdout)
    } else {
        // The sidecar prints a JSON {"error": ...} to stdout on handled failures.
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        Err(if stdout.is_empty() { stderr } else { stdout })
    }
}

#[tauri::command]
fn measure_foot(mesh_path: String) -> Result<String, String> {
    run_sidecar(&["measure-foot", &mesh_path])
}

#[tauri::command]
fn generate_exports(obj_dir: String, out_dir: String) -> Result<String, String> {
    run_sidecar(&["exports", &obj_dir, &out_dir])
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![measure_foot, generate_exports])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
