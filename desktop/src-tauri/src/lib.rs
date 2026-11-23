// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
use std::path::PathBuf;
use std::process::Command;

/// Resolve a bundled sidecar binary.
///
/// - Packaged `.app`: the binary sits next to the main executable
///   (`Contents/MacOS/<name>`), with the target-triple suffix stripped by Tauri.
/// - `tauri dev` / running from `target/*`: fall back to the staged
///   `src-tauri/bin/<name>-<triple>` produced by `build-sidecars.sh`.
fn sidecar_path(name: &str) -> Result<PathBuf, String> {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let bundled = dir.join(name);
            if bundled.exists() {
                return Ok(bundled);
            }
        }
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("bin")
        .join(format!("{name}-{}", env!("TARGET_TRIPLE")));
    if dev.exists() {
        return Ok(dev);
    }
    Err(format!(
        "sidecar `{name}` not found — run desktop/build-sidecars.sh to stage it"
    ))
}

/// Run the frozen Python sidecar with the given args and return its stdout JSON.
fn run_sidecar(args: &[&str]) -> Result<String, String> {
    let binary = sidecar_path("gotta-sidecar")?;
    let output = Command::new(&binary)
        // pymeshlab pulls in Qt; force headless so exports work with no display.
        .env("QT_QPA_PLATFORM", "offscreen")
        .args(args)
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

/// Reconstruct a 3D mesh from a folder of photos. Writes model.usdz and the OBJ
/// master (for exports/measurements) into out_dir. Blocks until done (minutes).
#[tauri::command]
fn reconstruct(images_dir: String, out_dir: String, detail: Option<String>) -> Result<String, String> {
    let binary = sidecar_path("photogrammetry-cli")?;
    std::fs::create_dir_all(&out_dir).map_err(|e| format!("mkdir failed: {e}"))?;
    let detail = detail.unwrap_or_else(|| "full".to_string());
    let usdz = PathBuf::from(&out_dir).join("model.usdz");

    let output = Command::new(&binary)
        .arg(&images_dir)
        .arg(&usdz)
        .arg(&detail)
        .arg(&out_dir) // obj-output-dir: writes the OBJ master here
        .output()
        .map_err(|e| format!("failed to launch reconstruction: {e}"))?;

    if output.status.success() {
        Ok(format!(
            "{{\"status\":\"complete\",\"usdz\":{:?},\"obj_dir\":{:?}}}",
            usdz.to_string_lossy(),
            out_dir
        ))
    } else {
        Err(String::from_utf8_lossy(&output.stderr).trim().to_string())
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![measure_foot, generate_exports, reconstruct])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
