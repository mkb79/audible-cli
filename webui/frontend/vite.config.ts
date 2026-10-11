import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In production this frontend is served same-origin by the FastAPI backend
// (see main.py's StaticFiles mount), so there is no CORS story to worry
// about there. In dev, Vite runs on its own port, so proxy /api and /auth
// to the backend to keep that same "same-origin, cookie-based session"
// behavior locally.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
    },
  },
})
