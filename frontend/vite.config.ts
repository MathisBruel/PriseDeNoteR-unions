import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// En développement, l'API tourne sur localhost:8000 (uvicorn).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
});
