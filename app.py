"""
metastate-quantum — Quantum worker for the METASTATE L2 router.

A small service that holds IBM Quantum credentials (NEVER on the public Space)
and bridges METASTATE's process-matrix evaluation to real quantum hardware.

Flow:
  METASTATE Space  --POST /route-->  this worker  --Qiskit Runtime-->  IBM QPU
                   <--probabilities--             <--counts--

If IBM credentials are absent, OR the requested mode is "simulator", it runs a
local statevector simulation so the endpoint always responds. The response shape
is identical either way, so METASTATE's contract never changes.

Deploy on Render (free tier). Set these as Render environment variables:
  IBM_QUANTUM_TOKEN   — your IBM Quantum Platform API key (44 chars)
  IBM_QUANTUM_CRN     — your instance Cloud Resource Name (CRN)
  WORKER_SHARED_SECRET— a random string; METASTATE sends it as a header so only
                        your Space can call this worker
"""
import os, json, math
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import List, Optional

IBM_TOKEN  = os.environ.get("IBM_QUANTUM_TOKEN", "")
IBM_CRN    = os.environ.get("IBM_QUANTUM_CRN", "")
SHARED     = os.environ.get("WORKER_SHARED_SECRET", "")
HAVE_IBM   = bool(IBM_TOKEN and IBM_CRN)

app = FastAPI(title="METASTATE Quantum Worker", version="1.0.0")

# ---- lazy Qiskit import so the service boots even without the heavy deps ----
_service = None
def ibm_service():
    global _service
    if _service is None:
        from qiskit_ibm_runtime import QiskitRuntimeService
        _service = QiskitRuntimeService(channel="ibm_quantum_platform",
                                        token=IBM_TOKEN, instance=IBM_CRN)
    return _service

# ----------------------------------------------------------------- models
class RouteReq(BaseModel):
    process_matrix: List[List[float]]   # the W coupling
    backend: str = "auto"               # auto | simulator | <ibm backend name>
    shots: int = 1024

# ----------------------------------------------------------------- helpers
def matrix_to_circuit(W, n_qubits):
    """
    Encode the (normalised) process-matrix row structure into a small circuit:
    rotation angles from row magnitudes + entangling layer. This is a faithful,
    intentionally simple embedding — it maps the coupling into a real circuit
    whose measurement distribution reflects W's structure.
    """
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(n_qubits, n_qubits)
    for i in range(n_qubits):
        row = W[i % len(W)]
        s = sum(abs(v) for v in row) or 1.0
        theta = math.pi * (abs(row[i % len(row)]) / s)
        qc.ry(theta, i)
    for i in range(n_qubits - 1):
        qc.cx(i, i + 1)
    qc.measure(range(n_qubits), range(n_qubits))
    return qc

def simulate(W, n_qubits, shots):
    """Local statevector simulation — always available, no credentials."""
    from qiskit import QuantumCircuit, transpile
    from qiskit_aer import AerSimulator
    qc = matrix_to_circuit(W, n_qubits)
    sim = AerSimulator()
    result = sim.run(transpile(qc, sim), shots=shots).result()
    counts = result.get_counts()
    total = sum(counts.values()) or 1
    return {k: v / total for k, v in counts.items()}

def run_on_ibm(W, n_qubits, shots, backend_name):
    """Submit to a real IBM QPU via Qiskit Runtime SamplerV2."""
    from qiskit import transpile
    from qiskit_ibm_runtime import SamplerV2
    svc = ibm_service()
    backend = (svc.backend(backend_name) if backend_name not in ("auto", "")
               else svc.least_busy(operational=True, simulator=False))
    qc = matrix_to_circuit(W, n_qubits)
    qc_t = transpile(qc, backend)
    sampler = SamplerV2(mode=backend)
    job = sampler.run([qc_t], shots=shots)
    res = job.result()
    counts = res[0].data.c.get_counts()
    total = sum(counts.values()) or 1
    return {k: v / total for k, v in counts.items()}, backend.name, job.job_id()

# ----------------------------------------------------------------- routes
@app.get("/")
def health():
    return {"service": "metastate-quantum", "ibm_configured": HAVE_IBM,
            "mode_default": "ibm" if HAVE_IBM else "simulator"}

@app.post("/route")
def route(r: RouteReq, x_worker_secret: str = Header(None)):
    # only METASTATE (which knows the shared secret) may call the QPU
    if SHARED and x_worker_secret != SHARED:
        raise HTTPException(401, "bad worker secret")
    n = min(max(len(r.process_matrix), 1), 5)   # cap qubits for the free plan
    shots = min(max(r.shots, 64), 4096)
    want_real = HAVE_IBM and r.backend != "simulator"
    try:
        if want_real:
            probs, backend_used, job_id = run_on_ibm(r.process_matrix, n, shots, r.backend)
            return {"backend_requested": r.backend, "backend_used": backend_used,
                    "hardware_status": "live (IBM Quantum)", "job_id": job_id,
                    "dimension": n, "shots": shots, "measurement_probabilities": probs}
        else:
            probs = simulate(r.process_matrix, n, shots)
            return {"backend_requested": r.backend,
                    "backend_used": "aer-simulator" if HAVE_IBM else "aer-simulator (no IBM creds)",
                    "hardware_status": "simulator", "dimension": n, "shots": shots,
                    "measurement_probabilities": probs}
    except Exception as e:
        # never 500 the caller: fall back to simulation and report why
        probs = simulate(r.process_matrix, n, shots)
        return {"backend_requested": r.backend, "backend_used": "aer-simulator (fallback)",
                "hardware_status": "simulator (hardware error)", "error": str(e)[:200],
                "dimension": n, "shots": shots, "measurement_probabilities": probs}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
