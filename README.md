# Commander Deckbuilder

Aplicación para construir mazos de Commander: selecciona un comandante, genera una base de 99 cartas y ajústala con swaps validados en vivo.

## Desarrollo

Backend (FastAPI, desde `backend/`):

```bash
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Frontend (React + Vite, desde `frontend/`):

```bash
npm install
npm run dev   # proxy de /api hacia http://localhost:8000
```

Tests (desde `backend/`):

```bash
pytest
```

La imagen Docker de la raíz construye el frontend y sirve API + SPA en el puerto 7860 (Hugging Face Spaces).
