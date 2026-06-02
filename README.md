# metastate-quantum

Quantum worker bridging the METASTATE L2 router to IBM Quantum hardware.

- Holds IBM credentials server-side (never on the public Space).
- `POST /route` accepts a process matrix, builds a circuit, runs it on a real
  IBM QPU via Qiskit Runtime, and returns measurement probabilities.
- Falls back to a local Aer simulator when credentials are absent or hardware
  errors — the response shape never changes.

## Deploy (Render, free tier)
1. Push this folder to a GitHub repo.
2. Render → New → Web Service → connect the repo (render.yaml auto-detected).
3. Set env vars: IBM_QUANTUM_TOKEN, IBM_QUANTUM_CRN, WORKER_SHARED_SECRET.
4. Deploy. Health check: GET / → {"ibm_configured": true}.

## Endpoint
POST /route   header: X-Worker-Secret: <WORKER_SHARED_SECRET>
body: {"process_matrix": [[...],[...]], "backend": "auto", "shots": 1024}
