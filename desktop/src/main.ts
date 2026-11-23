import { invoke } from "@tauri-apps/api/core";

// Everything runs locally in this app: photos -> mesh (Swift PhotogrammetrySession
// sidecar) -> exports + measurements (Python sidecar). No server involved.

window.addEventListener("DOMContentLoaded", () => {
  const imagesInput = document.querySelector<HTMLInputElement>("#images-input")!;
  const reconOutInput = document.querySelector<HTMLInputElement>("#reconout-input")!;
  const reconOut = document.querySelector<HTMLPreElement>("#recon-out")!;
  document.querySelector("#recon-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!imagesInput.value.trim() || !reconOutInput.value.trim()) return;
    reconOut.textContent = "Reconstructing… (minutes)";
    try {
      const json = await invoke<string>("reconstruct", {
        imagesDir: imagesInput.value.trim(),
        outDir: reconOutInput.value.trim(),
      });
      reconOut.textContent = JSON.stringify(JSON.parse(json), null, 2);
    } catch (err) {
      reconOut.textContent = `Error: ${err}`;
    }
  });

  const meshInput = document.querySelector<HTMLInputElement>("#mesh-input")!;
  const measureOut = document.querySelector<HTMLPreElement>("#measure-out")!;
  document.querySelector("#measure-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const mesh = meshInput.value.trim();
    if (!mesh) return;
    measureOut.textContent = "Measuring…";
    try {
      const json = await invoke<string>("measure_foot", { meshPath: mesh });
      measureOut.textContent = JSON.stringify(JSON.parse(json), null, 2);
    } catch (err) {
      measureOut.textContent = `Error: ${err}`;
    }
  });

  const objDir = document.querySelector<HTMLInputElement>("#objdir-input")!;
  const outDir = document.querySelector<HTMLInputElement>("#outdir-input")!;
  const exportsOut = document.querySelector<HTMLPreElement>("#exports-out")!;
  document.querySelector("#exports-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!objDir.value.trim() || !outDir.value.trim()) return;
    exportsOut.textContent = "Generating exports…";
    try {
      const json = await invoke<string>("generate_exports", {
        objDir: objDir.value.trim(),
        outDir: outDir.value.trim(),
      });
      exportsOut.textContent = JSON.stringify(JSON.parse(json), null, 2);
    } catch (err) {
      exportsOut.textContent = `Error: ${err}`;
    }
  });
});
