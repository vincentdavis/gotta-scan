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

// --- Reconstruction (Swift PhotogrammetrySession CLI) ---

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn recon_source() -> PathBuf {
    repo_root().join("worker").join("PhotogrammetryCLI.swift")
}

fn recon_binary() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join(".recon-build")
        .join("photogrammetry-cli")
}

/// Compile the Swift reconstruction CLI if the binary is missing or older than
/// the source. (Dev convenience; a packaged app ships a prebuilt binary.)
fn ensure_recon_binary() -> Result<PathBuf, String> {
    let source = recon_source();
    let binary = recon_binary();

    let needs_build = match (std::fs::metadata(&binary), std::fs::metadata(&source)) {
        (Ok(b), Ok(s)) => match (b.modified(), s.modified()) {
            (Ok(bt), Ok(st)) => bt < st,
            _ => false,
        },
        _ => true,
    };

    if needs_build {
        if let Some(dir) = binary.parent() {
            std::fs::create_dir_all(dir).map_err(|e| format!("mkdir failed: {e}"))?;
        }
        let out = Command::new("swiftc")
            .args(["-O", "-parse-as-library"])
            .arg(&source)
            .arg("-o")
            .arg(&binary)
            .output()
            .map_err(|e| format!("failed to launch swiftc: {e}"))?;
        if !out.status.success() {
            return Err(format!(
                "swiftc failed: {}",
                String::from_utf8_lossy(&out.stderr).trim()
            ));
        }
    }
    Ok(binary)
}

/// Reconstruct a 3D mesh from a folder of photos. Writes model.usdz and the OBJ
/// master (for exports/measurements) into out_dir. Blocks until done (minutes).
#[tauri::command]
fn reconstruct(images_dir: String, out_dir: String, detail: Option<String>) -> Result<String, String> {
    let binary = ensure_recon_binary()?;
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
