import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command, mode }) => {
  if (command === "build") {
    const apiBase = loadEnv(mode, process.cwd(), "VITE_").VITE_API_BASE_URL || "";
    if (!apiBase.startsWith("https://")) {
      throw new Error("Production build requires an HTTPS VITE_API_BASE_URL");
    }
  }
  return {
    plugins: [react()],
    server: { host: "127.0.0.1", port: 5173 },
  };
});
