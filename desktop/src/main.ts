import { openUrl } from "@tauri-apps/plugin-opener";

// Scaffold wiring: remember the server URL and open the scan pipeline in the
// browser for now. The native capture / reconstruct / view flow replaces this.
const DEFAULT_SERVER = "https://gotta-bike-3d.up.railway.app";

window.addEventListener("DOMContentLoaded", () => {
  const input = document.querySelector<HTMLInputElement>("#server-input")!;
  const status = document.querySelector<HTMLParagraphElement>("#status-msg")!;
  input.value = localStorage.getItem("serverUrl") ?? DEFAULT_SERVER;

  document.querySelector("#server-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const url = input.value.trim().replace(/\/+$/, "");
    if (!url) return;
    localStorage.setItem("serverUrl", url);
    status.textContent = `Opening ${url} …`;
    await openUrl(url);
  });
});
