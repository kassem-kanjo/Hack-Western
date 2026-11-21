import { defineConfig } from "vite";

// Dev-only proxy: the browser calls /api on the Vite server, which forwards to the
// local FastAPI app. There is no proxy in the production build. Run the API from a
// snapshot with `DARKFLEET_DATA=data/snapshot`.
export default defineConfig({
  server: {
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", changeOrigin: false },
    },
  },
});
