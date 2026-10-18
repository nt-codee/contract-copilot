# services/api

Python backend: upload, extract, review, verify quotes, search. Cloud access only through the
adapters in `app/adapters/` (`CLOUD=gcp|local`, `LLM=vertex|fake`); `pipeline.py` and `main.py`
import no cloud SDK.

```sh
uv sync
CLOUD=local LLM=fake uv run pytest -q                                   # offline, recorded fixtures
CLOUD=local LLM=vertex uv run python ../../scripts/review.py <file.pdf>  # real Gemini via ADC
CLOUD=local LLM=vertex uv run python ../../scripts/eval.py               # samples/contracts -> eval/results/
CLOUD=local LLM=fake DEV_CORS=1 uv run uvicorn --factory app.main:create_app --port 8080 --reload
```

Env: `CLOUD`, `LLM`, `GCP_PROJECT`, `GEMINI_LOCATION` (global), `GEMINI_MODEL`, `GEMINI_THINKING`
(low), `EMBED_MODEL`, `EMBED_LOCATION` (global), `UPLOAD_BUCKET`, `FIRESTORE_DB`, `SAMPLES_DIR`,
`BASELINE_DIR`, `WEB_DIST`, `DATA_DIR` (local mode), `DEV_CORS=1` (local dev only),
`RATE_LIMIT_PER_HOUR` (10). Set `RECORD_DIR` to dump raw Gemini responses when you refresh fixtures.

Embeddings are 768-dimensional (`EMBED_DIM` in `config.py`), matching the Firestore vector index on
`chunks.embedding`. gemini-embedding-2 accepts one content per request, so `GeminiEmbedder` fans
out over 8 threads.
