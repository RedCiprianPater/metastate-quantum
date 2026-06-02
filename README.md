# metastate-quantum

Quantum worker bridging the METASTATE L2 router to real quantum backends.

## Backends
- **IBM Quantum** (`backend: "ibm"` / `"auto"`) — Qiskit Runtime, free Open plan.
- **Origin / QPanda** (`backend: "origin"`) — Origin Pilot / QPanda3, the open-source
  stack from Origin Quantum (USTC). Runs the real QPanda CPU simulator with no
  credentials; the 72-qubit Origin Wukong hardware path activates when
  `ORIGIN_API_KEY` is set.
- **Simulator** (`backend: "simulator"`) — local Aer, always available.

All backends return the same response shape, so METASTATE's contract never changes.

## Deploy (Render, free tier)
1. Push this folder to GitHub.
2. Render → New → Web Service → connect the repo (render.yaml auto-detected, pins
   Python 3.11.9).
3. Set env vars: `IBM_QUANTUM_TOKEN`, `IBM_QUANTUM_CRN`, `WORKER_SHARED_SECRET`,
   and optionally `ORIGIN_API_KEY`.
4. Deploy. Health: GET / → shows configured backends.

`pyqpanda3` is installed best-effort in the build; if no wheel is available for the
platform, the Origin backend reports unavailable and falls back to the simulator —
the build never breaks.

## Endpoint
POST /route   header: X-Worker-Secret: <WORKER_SHARED_SECRET>
body: {"process_matrix": [[...],[...]], "backend": "auto|ibm|origin|simulator", "shots": 1024}
