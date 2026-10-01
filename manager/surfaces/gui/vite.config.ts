import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// `base: "./"` makes built asset URLs relative, so the bundle loads from the `tauri://`
// origin in the desktop shell (absolute `/assets` 404s there); a server-hosted build is
// unaffected. Dev runs on a fixed port (1420) with strictPort so the Tauri webview always
// loads the vite instance Tauri itself spawns (a drifting port would make the window load a
// stale/other server). `tauri.conf.json` devUrl must match this.
export default defineConfig(({ command }) => {
  let devToken = "";
  if (command === "serve") {
    const state =
      process.env.COWORKER_STATE_DIR ||
      (process.platform === "win32"
        ? path.join(process.env.APPDATA || os.homedir(), "coworker")
        : path.join(os.homedir(), ".config", "coworker"));
    try {
      devToken = fs.readFileSync(path.join(state, "sidecar-8765.token"), "utf8").trim();
    } catch {
      // The Tauri dev shell injects its in-memory token at runtime. Plain browser dev
      // shows the normal startup retry until the standalone server/token file exists.
    }
  }
  return {
    base: "./",
    plugins: [react()],
    server: {
      port: 1420,
      strictPort: true,
      // Tauri writes bundled sidecar files and generated HTML under src-tauri/target while
      // compiling. Watching that tree reloads the WebView during startup and can leave WKWebView
      // on an unpainted white frame even though React and the sidecar are healthy.
      watch: { ignored: ["**/src-tauri/target/**"] },
    },
    define: { __COWORKER_DEV_TOKEN__: JSON.stringify(devToken) },
    // Tauri CLI looks for these; harmless for the browser build.
    clearScreen: false,
    envPrefix: ["VITE_", "TAURI_"],
    worker: {
      // react-xlsx's parser worker imports its local WASM module and therefore requires an
      // ES-module worker bundle. Vite's IIFE worker default cannot represent that split graph.
      format: "es",
    },
    optimizeDeps: {
      // The package constructs its worker relative to import.meta.url. Pre-bundling moves the
      // module into .vite/deps without copying xlsx-worker.js, so dev/test WebViews cannot load it.
      exclude: ["@extend-ai/react-xlsx"],
      // react-xlsx imports regl's CommonJS build as a default export. Once the viewer itself is
      // excluded, force this leaf through Vite's CJS interop instead of serving it as raw ESM.
      include: ["regl"],
    },
    build: {
      manifest: true,
    },
  };
});
