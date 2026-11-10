# A.R.C.H. Backend

The backend provides local HTTP APIs and later hosts the Pipecat media pipeline.
Milestone 0 exposes only `GET /api/health`; it does not load a speech or language model.

Run the development server from this directory with:

    uv run uvicorn arch.main:app --host 127.0.0.1 --port 8000
