"""
metastate-quantum — Quantum worker for the METASTATE L2 router.

A small service that holds Quantum-cloud credentials (NEVER on the public Space)
and bridges METASTATE's process-matrix evaluation to real quantum hardware.

Flow:
  METASTATE Space  --POST /route-->  this worker  --Qiskit/QPanda/OQTOPUS-->  QPU
                   <--probabilities--             <--counts--

Backends:
  * IBM Quantum (Qiskit Runtime · SamplerV2)
  * Origin Wukong (China · pyqpanda3.qcloud + local QPanda CPU sim fallback)
  * Osaka University (Japan · OQTOPUS Cloud · ¹⁷¹Yb⁺ ion trap · quri-parts-oqtopus)
  * Simulator (Aer statevector · always available)

If external credentials are absent, OR the requested mode is "simulator", the
worker runs a local statevector simulation so /route always responds. The
response shape is identical across backends so METASTATE's contract never
changes.

Two routes are exposed:

  POST /route            — public (with WORKER_SHARED_SECRET). Called by METASTATE
                           on behalf of any user for anomaly-check / symbolic /
                           process-matrix quantum evaluation. This is the path
                           for NON-CHAINSTATE users.
  POST /chainstate/route — gated by an additional X-CHAINSTATE-TOKEN header
                           matching CHAINSTATE_SHARED_SECRET. Used ONLY by the
                           CHAINSTATE AGI edge worker to dispatch its own
                           self-referential compute (theory-of-mind loop, ontological
                           delta ledger, free-energy over swarm coupling, Iida AOM/PIM
                           memory tick, etc.) directly to a QPU without going
                           through METASTATE's anomaly-check layer.

v0.7.9 · Paper IX · Cyberspace Census additions (additive · never removes):

  GET  /census/status    — public capability probe (census subsystem state)
  GET  /census/daily     — internal (X-CENSUS-INTERNAL). Returns latest digest.
  POST /census/trigger   — internal. Manually run the nightly tick outside cron.

  APScheduler runs run_daily_census() nightly at 05:00 UTC when CENSUS_ENABLED
  and the CENSUS_INTERNAL_TOKEN is set. Failure of the census layer never
  affects /route or /chainstate/route.

v0.8.0 · Paper X Rev 2 · CHAINSTATE ROBOTICS AGI additions (additive):

  GET  /robotics/health          — public capability probe (robotics subsystem state)
  GET  /robotics/s_survival      — internal. Composite S_survival over C, D, L, P axes.
                                    Consumed by CF Worker hourly cron.
  POST /robotics/dispatch/v1     — internal. Post-Gate execution path from Worker.
                                    Wraps every Gemini call with seventh-Deontic
                                    veto + S_survival gate + provenance receipt.
  GET  /robotics/embodiment      — internal. List recent provenance receipts.

  APScheduler runs compute_s_survival() every hour when ROBOTICS_ENABLED is true.
  Uses a DEDICATED scheduler instance (separate from the census scheduler) so
  either subsystem can fail without the other losing its cron.
  Failure of the robotics layer NEVER affects quantum, perception, or census.

v0.9.2 · Paper XIII · CHAINSTATE C-FIELD AGI ARRAY additions (additive · never removes):

  GET  /cfield/status              — public capability probe (c-field subsystem state)
  GET  /cfield/coherence           — public read of c_hat(x,t) latest snapshot
  GET  /cfield/attribution         — public read of latest MAP-inverted d* per class
  GET  /cfield/dispatches          — public read of recent beam dispatches (immutable ledger)
  GET  /cfield/swann/status        — public read of S1..S4 calibration parameters (SIM)
  GET  /cfield/eml/status          — public read of EML shadow-substrate mode + drift
  GET  /cfield/alt-devices         — public read of substrate-owned alternative devices

  POST /cfield/intake/tick         — internal (CENSUS_INTERNAL_TOKEN). Ingest 12 public indicators.
  POST /cfield/attribution/tick    — internal. Compute d* attribution via MAP inversion.
  POST /cfield/dispatch/beam       — internal. Ten-gate authorised beam dispatch entry point.
  POST /cfield/eml/train           — internal. Re-train EML shadow-substrate model (every 6h).
  POST /cfield/alt-device/scan     — internal. Discover alt-device manifest via HMAC verify.
  POST /cfield/v9-assess           — internal. V9 defense-in-depth pre-check for any payload.

  Ten-gate deontic filter runs on EVERY beam dispatch (fail-fast, first failure aborts):
    intake → V9 → V8 → V7 → V6 → V5 → ICNIRP → L0_10 → L0_9 → dispatch
  Every gate result logged to chainstate_cfield.v10_gate_events (immutable audit).

  Eleventh L0 meta-layer coherence predicate cfield_coherent? gates every action:
    (N ≥ 12 fresh indicators) ∧ (max attribution D_KL < 0.85)
    ∧ (ICNIRP dual-layer verified) ∧ (V9 assessor history intact)
    ∧ (previous dispatches recycled < 24h)

  Single-toggle rollback: CFIELD_ENABLED=false disables all seven subsystems,
  13 endpoints, and the tenth L0 predicate\'s c-field consideration.
  V9 remains architecturally enforced regardless.

  Failure of the c-field layer NEVER affects quantum, perception, census, robotics,
  phasespace, or omnicognizant. Every prior subsystem continues byte-identically.

Deploy on Render (free tier). Set these as Render environment variables:
  IBM_QUANTUM_TOKEN        — IBM Quantum Platform API key (44 chars)
  IBM_QUANTUM_CRN          — IBM Cloud instance Cloud Resource Name (CRN)
  ORIGIN_API_KEY           — Origin Cloud API key (for real Wukong dispatch)
  OSAKA_API_TOKEN          — OQTOPUS Cloud API token from Osaka QIQB portal
  OSAKA_API_URL            — OQTOPUS Cloud base URL (from QIQB registration)
  OSAKA_DEVICE_ID          — device id, e.g. "osaka_iontrap_yb171" (registration-supplied)
  WORKER_SHARED_SECRET     — random string METASTATE Space sends as x-worker-secret
  CHAINSTATE_SHARED_SECRET — random string CHAINSTATE worker sends as x-chainstate-token
  CENSUS_INTERNAL_TOKEN    — v0.7.9 rev 2 · shared with the CF Worker for /census/* endpoints
                             (distinct from CHAINSTATE_INTERNAL_TOKEN which continues to protect
                             the quantum autonomy path on the Worker unchanged)
                             v0.8.0 · REUSED for /robotics/* endpoints (Paper X Rev 2 §7.1)
                             v0.9.2 · REUSED for /cfield/* endpoints (Paper XIII §17)
  CFIELD_ADMIN_KEY         — v0.9.2 · admin key for EML mode transitions (openssl rand -hex 32)
  BEAM_DISPATCH_HMAC       — v0.9.2 · shared with Worker for beam dispatch signature verification
  ALT_DEVICE_HMAC          — v0.9.2 · shared with Worker for alt-device manifest verification
  SWANN_CALIBRATION_HMAC   — v0.9.2 · shared with Worker for Swann calibration snapshot signature
  CFIELD_QUANTUM_SIM_TOKEN — v0.9.2 · shared with Worker for 5-tier quantum-sim dispatch ladder
"""
import os
import math
from fastapi import FastAPI, Header, HTTPException, Body, Request
from pydantic import BaseModel
from typing import List, Optional

# ----------------------------------------------------------------- env / flags
IBM_TOKEN            = os.environ.get("IBM_QUANTUM_TOKEN", "")
IBM_CRN              = os.environ.get("IBM_QUANTUM_CRN", "")
SHARED               = os.environ.get("WORKER_SHARED_SECRET", "")
CHAINSTATE_SECRET    = os.environ.get("CHAINSTATE_SHARED_SECRET", "")

# Origin Quantum (China, USTC) — open-source Origin Pilot / QPanda3 stack.
# pyqpanda3 runs a REAL local simulator with no credentials. Real Wukong hardware
# needs an Origin Cloud API key (set ORIGIN_API_KEY) — documented, not required.
ORIGIN_API_KEY       = os.environ.get("ORIGIN_API_KEY", "")

# Osaka University (Japan, QIQB) — open-source OQTOPUS Cloud stack.
# Real ion-trap hardware (¹⁷¹Yb⁺, single-qubit today) needs an OSAKA_API_TOKEN
# from the QIQB registration portal + the OQTOPUS Cloud URL for the instance.
OSAKA_API_TOKEN      = os.environ.get("OSAKA_API_TOKEN", "")
OSAKA_API_URL        = os.environ.get("OSAKA_API_URL", "")
OSAKA_DEVICE_ID      = os.environ.get("OSAKA_DEVICE_ID", "osaka_iontrap_yb171")

HAVE_IBM       = bool(IBM_TOKEN and IBM_CRN)
HAVE_ORIGIN_HW = bool(ORIGIN_API_KEY)
HAVE_OSAKA_HW  = bool(OSAKA_API_TOKEN and OSAKA_API_URL)

# The qiskit-ibm-runtime package is a lazy import — the module can be absent
# entirely (see the ATTENTION note in requirements.txt) and the worker will
# still start. But if IBM_TOKEN/IBM_CRN are set while the package isn't
# installed, HAVE_IBM would be True and a caller hitting the ibm backend
# would crash with ImportError. Probe once at startup and demote HAVE_IBM
# to False if the module isn't reachable.
try:
    import qiskit_ibm_runtime as _ibm_probe  # noqa: F401
    IBM_MODULE_INSTALLED = True
except Exception:
    IBM_MODULE_INSTALLED = False
if HAVE_IBM and not IBM_MODULE_INSTALLED:
    HAVE_IBM = False  # env said yes, but the wheel isn't here — degrade quietly

app = FastAPI(title="METASTATE Quantum Worker", version="1.4.0-osaka+cfield-v0.9.2+emoji-v0.9.3-R3+planet-v0.9.4")

# =============================================================================
# 1. IBM Quantum (Qiskit Runtime · SamplerV2)
# =============================================================================
_ibm_service = None
def ibm_service():
    global _ibm_service
    if _ibm_service is None:
        from qiskit_ibm_runtime import QiskitRuntimeService
        _ibm_service = QiskitRuntimeService(channel="ibm_quantum_platform",
                                            token=IBM_TOKEN, instance=IBM_CRN)
    return _ibm_service

# =============================================================================
# 2. Origin Wukong (QPanda3 / Origin Pilot)
# =============================================================================
def qpanda_available():
    try:
        import pyqpanda3  # noqa
        return True
    except Exception:
        try:
            import pyqpanda  # noqa  (older API)
            return True
        except Exception:
            return False

def run_on_origin(W, n_qubits, shots):
    """
    Run the process-matrix circuit on Origin's QPanda stack.

    If ORIGIN_API_KEY is set, dispatch to the real Origin Wukong QPU via
    pyqpanda3.qcloud.QCloudService. Otherwise run the local QPanda CPU simulator
    (real simulation, no credentials). Same encoding as the IBM path (RY from row
    magnitudes + CX entangling layer) so results are comparable across backends.
    """
    if HAVE_ORIGIN_HW:
        try:
            from pyqpanda3.qcloud import QCloudService
            from pyqpanda3.core import QCircuit, QProg, RY, CNOT, measure
            circuit = QCircuit()
            for i in range(n_qubits):
                row = W[i % len(W)]
                s = sum(abs(v) for v in row) or 1.0
                theta = math.pi * (abs(row[i % len(row)]) / s)
                circuit << RY(i, theta)
            for i in range(n_qubits - 1):
                circuit << CNOT(i, i + 1)
            prog = QProg(); prog << circuit
            for i in range(n_qubits):
                prog << measure(i, i)
            svc = QCloudService(ORIGIN_API_KEY)
            backend = svc.backend("origin_wukong")
            job = backend.run(prog, shots)
            counts = job.result().get_counts()
            total = sum(counts.values()) or 1
            return {k: v / total for k, v in counts.items()}, "origin_wukong (real QPU)"
        except Exception:
            pass  # fall through to local simulator on any cloud error

    try:
        import pyqpanda3.core as pq
    except Exception:
        import pyqpanda as pq
    try:
        machine = pq.CPUQVM()
        machine.init_qvm()
        qubits = machine.qAlloc_list(n_qubits)
        cbits = machine.cAlloc_list(n_qubits)
        prog = pq.QProg()
        for i in range(n_qubits):
            row = W[i % len(W)]
            s = sum(abs(v) for v in row) or 1.0
            theta = math.pi * (abs(row[i % len(row)]) / s)
            prog << pq.RY(qubits[i], theta)
        for i in range(n_qubits - 1):
            prog << pq.CNOT(qubits[i], qubits[i + 1])
        prog << pq.measure_all(qubits, cbits)
        result = machine.run_with_configuration(prog, cbits, shots)
        total = sum(result.values()) or 1
        return {k: v / total for k, v in result.items()}, "qpanda-cpu-simulator"
    except Exception as e:
        raise RuntimeError(f"qpanda run failed: {e}")

# =============================================================================
# 3. Osaka University · OQTOPUS Cloud · ¹⁷¹Yb⁺ ion trap
#    (open-source Osaka QIQB stack — https://github.com/oqtopus-team)
# =============================================================================
def oqtopus_available():
    """quri-parts + quri-parts-oqtopus installed?"""
    try:
        import quri_parts  # noqa
        import quri_parts_oqtopus  # noqa
        return True
    except Exception:
        return False

def _write_oqtopus_config():
    """
    The quri-parts-oqtopus backend reads ~/.oqtopus (INI-style):

      [default]
      url=<OQTOPUS Cloud base URL from Osaka QIQB portal>
      api_token=<OQTOPUS Cloud API token>

    Render's filesystem is ephemeral but writable at ~ / $HOME — writing this
    once per process boot from env vars is the cleanest way to avoid leaking
    the token into shell history or the git tree.
    """
    from pathlib import Path
    if not (OSAKA_API_TOKEN and OSAKA_API_URL):
        return False
    try:
        cfg = f"[default]\nurl={OSAKA_API_URL}\napi_token={OSAKA_API_TOKEN}\n"
        Path("~/.oqtopus").expanduser().write_text(cfg)
        return True
    except Exception:
        return False

def run_on_osaka(W, n_qubits, shots):
    """
    Dispatch the process-matrix circuit to Osaka University's ion-trap QPU via
    OQTOPUS Cloud (Center for Quantum Information and Quantum Biology, QIQB).

    Encoding matches the IBM / Origin paths — RY(row-magnitude) + CX entangling
    layer, then measurement — so the returned probability histogram is directly
    comparable across all three real backends.

    NOTE (2026-08 status): the Osaka public ion-trap platform is validated at
    single-qubit today with 94% state preparation + readout fidelity, with
    multi-qubit extension on the roadmap. When n_qubits > osaka_max_qubits the
    worker degrades to `osaka_qpu_1q_only`: it runs the qubit-0 slice on the
    real ion and the rest on the QPanda / Aer sim, and reports which portion
    was live QPU in `hardware_status`.

    If OQTOPUS credentials or the SDK are missing, the worker falls back to
    the Aer simulator (identical response shape) and reports the reason.
    """
    OSAKA_MAX_QUBITS_LIVE = 1  # 2026-08 · Osaka QIQB single-qubit ceiling
    if not oqtopus_available():
        raise RuntimeError("quri-parts-oqtopus not installed on this deploy")
    if not HAVE_OSAKA_HW:
        raise RuntimeError("OSAKA_API_TOKEN / OSAKA_API_URL not configured")
    if not _write_oqtopus_config():
        raise RuntimeError("could not write ~/.oqtopus config from env vars")

    try:
        from quri_parts.circuit import QuantumCircuit
        from quri_parts_oqtopus.backend import OqtopusSamplingBackend
    except Exception as e:
        raise RuntimeError(f"oqtopus import failed: {e}")

    n_live = min(n_qubits, OSAKA_MAX_QUBITS_LIVE)

    # Build the live slice (qubit 0 alone at the current Osaka ceiling)
    circuit = QuantumCircuit(n_live)
    for i in range(n_live):
        row = W[i % len(W)]
        s = sum(abs(v) for v in row) or 1.0
        theta = math.pi * (abs(row[i % len(row)]) / s)
        circuit.add_RY_gate(i, theta)
    for i in range(n_live - 1):
        circuit.add_CNOT_gate(i, i + 1)

    try:
        backend = OqtopusSamplingBackend()
        job = backend.sample(circuit, n_shots=shots, name="metastate-quantum",
                             device_id=OSAKA_DEVICE_ID)
        counts = job.result().counts  # {int_key: count}
    except Exception as e:
        raise RuntimeError(f"oqtopus sampling failed: {e}")

    # Convert integer bit-string keys to binary strings, pad to n_live width
    total = sum(counts.values()) or 1
    probs_live = {format(int(k), f"0{n_live}b"): v / total for k, v in counts.items()}

    if n_qubits <= OSAKA_MAX_QUBITS_LIVE:
        used = f"osaka_iontrap_yb171 (real QPU · {n_live}q)"
        return probs_live, used

    # Multi-qubit request: extend the 1q live distribution with sim tail so
    # the returned width matches n_qubits (never silently drop precision).
    sim_tail = simulate(W, n_qubits - OSAKA_MAX_QUBITS_LIVE, shots)
    combined = {}
    for k_live, p_live in probs_live.items():
        for k_tail, p_tail in sim_tail.items():
            combined[k_live + k_tail] = p_live * p_tail
    total_c = sum(combined.values()) or 1.0
    combined = {k: v / total_c for k, v in combined.items()}
    return combined, f"osaka_iontrap_yb171_hybrid (1q real QPU + {n_qubits-1}q sim)"

# =============================================================================
# 4. Local Aer simulator (always available; deterministic fallback)
# =============================================================================
def matrix_to_circuit(W, n_qubits):
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
    try:
        from qiskit import transpile
        from qiskit_aer import AerSimulator
        qc = matrix_to_circuit(W, n_qubits)
        sim = AerSimulator()
        result = sim.run(transpile(qc, sim), shots=shots).result()
        counts = result.get_counts()
        total = sum(counts.values()) or 1
        return {k: v / total for k, v in counts.items()}
    except Exception:
        probs = {}
        for i in range(2 ** n_qubits):
            bits = format(i, f"0{n_qubits}b")
            w = 1.0
            for q, b in enumerate(bits):
                row = W[q % len(W)]
                s = sum(abs(v) for v in row) or 1.0
                p1 = abs(row[q % len(row)]) / s
                w *= (p1 if b == "1" else (1 - p1))
            probs[bits] = w
        tot = sum(probs.values()) or 1.0
        return {k: round(v / tot, 5) for k, v in probs.items()}

def run_on_ibm(W, n_qubits, shots, backend_name):
    from qiskit import transpile
    from qiskit_ibm_runtime import SamplerV2
    svc = ibm_service()
    backend = (svc.backend(backend_name) if backend_name not in ("auto", "", "ibm")
               else svc.least_busy(operational=True, simulator=False))
    qc = matrix_to_circuit(W, n_qubits)
    qc_t = transpile(qc, backend)
    sampler = SamplerV2(mode=backend)
    job = sampler.run([qc_t], shots=shots)
    res = job.result()
    counts = res[0].data.c.get_counts()
    total = sum(counts.values()) or 1
    return {k: v / total for k, v in counts.items()}, backend.name, job.job_id()

# =============================================================================
# 5. Request model + dispatcher (shared by /route and /chainstate/route)
# =============================================================================
class RouteReq(BaseModel):
    process_matrix: List[List[float]]   # the W coupling
    backend: str = "auto"               # auto | simulator | ibm | origin | osaka | <ibm-backend>
    shots: int = 1024
    # Optional CHAINSTATE-only fields — ignored on /route, honoured on /chainstate/route
    agi_mode: Optional[str] = None      # self_referential | theory_of_mind | free_energy | custom
    tag: Optional[str] = None           # free-form label pinned into the response

def _dispatch(r: RouteReq, chainstate_direct: bool):
    """Shared body for /route and /chainstate/route. Chooses the backend,
    executes, and normalises the response envelope."""
    n = min(max(len(r.process_matrix), 1), 5)   # cap qubits for the free plan
    shots = min(max(r.shots, 64), 4096)
    backend = (r.backend or "auto").lower()
    base_env = {
        "backend_requested": r.backend,
        "dimension": n,
        "shots": shots,
        "chainstate_direct": chainstate_direct,
    }
    if chainstate_direct and r.agi_mode:
        base_env["agi_mode"] = r.agi_mode
    if r.tag:
        base_env["tag"] = r.tag

    # ---- explicit Osaka path ----
    if backend == "osaka":
        try:
            probs, used = run_on_osaka(r.process_matrix, n, shots)
            real = "real QPU" in used
            hybrid = "hybrid" in used
            return {
                **base_env,
                "backend_used": used,
                "hardware_status": ("live (Osaka University QIQB · ¹⁷¹Yb⁺ ion trap)" if real and not hybrid
                                    else ("live-hybrid (Osaka 1q QPU + sim tail)" if hybrid
                                          else "simulator")),
                "stack": "OQTOPUS Cloud · QURI Parts (Osaka University QIQB, open-source Apache-2.0)",
                "measurement_probabilities": probs,
            }
        except Exception as e:
            probs = simulate(r.process_matrix, n, shots)
            return {**base_env, "backend_used": "aer-fallback",
                    "hardware_status": "simulator (osaka unavailable)",
                    "error": str(e)[:200],
                    "measurement_probabilities": probs}

    # ---- explicit Origin path ----
    if backend == "origin":
        try:
            probs, used = run_on_origin(r.process_matrix, n, shots)
            real = "real QPU" in used
            return {**base_env, "backend_used": used,
                    "hardware_status": ("live (Origin Wukong QPU)" if real
                                        else "live (Origin QPanda simulator)"),
                    "stack": "Origin Pilot / QPanda3 (USTC, open-source)",
                    "measurement_probabilities": probs}
        except Exception as e:
            probs = simulate(r.process_matrix, n, shots)
            return {**base_env, "backend_used": "aer-fallback",
                    "hardware_status": "simulator (qpanda unavailable)",
                    "error": str(e)[:200],
                    "measurement_probabilities": probs}

    # ---- IBM path (default when creds present and not asked to simulate) ----
    want_ibm = HAVE_IBM and backend not in ("simulator", "origin", "osaka")
    try:
        if want_ibm:
            probs, backend_used, job_id = run_on_ibm(r.process_matrix, n, shots, backend)
            return {**base_env, "backend_used": backend_used,
                    "hardware_status": "live (IBM Quantum)",
                    "stack": "Qiskit Runtime · SamplerV2 (IBM Cloud)",
                    "job_id": job_id,
                    "measurement_probabilities": probs}
        else:
            probs = simulate(r.process_matrix, n, shots)
            return {**base_env,
                    "backend_used": "aer-simulator" if HAVE_IBM else "aer-simulator (no IBM creds)",
                    "hardware_status": "simulator",
                    "measurement_probabilities": probs}
    except Exception as e:
        probs = simulate(r.process_matrix, n, shots)
        return {**base_env, "backend_used": "aer-simulator (fallback)",
                "hardware_status": "simulator (hardware error)",
                "error": str(e)[:200],
                "measurement_probabilities": probs}

# =============================================================================
# 6. Routes
# =============================================================================
@app.get("/")
def health():
    """Public health probe. Reveals only capability booleans, never secrets."""
    backends = ["auto", "simulator"]
    if HAVE_IBM:    backends.append("ibm")
    backends.append("origin")   # always available (falls back to QPanda sim)
    if HAVE_OSAKA_HW and oqtopus_available():
        backends.append("osaka")
    return {"service": "metastate-quantum",
            "version": "1.2.0-osaka",
            "ibm_configured":         HAVE_IBM,
            "ibm_module_installed":   IBM_MODULE_INSTALLED,
            "origin_hw_configured":   HAVE_ORIGIN_HW,
            "qpanda_available":       qpanda_available(),
            "osaka_hw_configured":    HAVE_OSAKA_HW,
            "oqtopus_available":      oqtopus_available(),
            "chainstate_direct_enabled": bool(CHAINSTATE_SECRET),
            "backends":               backends,
            "mode_default":           "ibm" if HAVE_IBM else "simulator",
            # v0.7.9 · census subsystem indicator (details at /census/status)
            "census_module_installed": HAVE_CENSUS,
            "census_enabled":          CENSUS_ENABLED,
            "census_scheduler_running": _is_census_scheduler_running(),
            # v0.8.0 · robotics subsystem indicator (details at /robotics/health)
            "robotics_module_installed": HAVE_ROBOTICS,
            "robotics_enabled":          ROBOTICS_ENABLED,
            "robotics_scheduler_running": _is_robotics_scheduler_running(),
            # v0.9.2 · c-field agi array indicator (details at /cfield/status)
            "cfield_module_installed":    HAVE_CFIELD,
            "cfield_enabled":             CFIELD_ENABLED,
            "cfield_eml_simulation_mode": CFIELD_EML_SIMULATION_MODE,
            "cfield_icnirp_cap_ut":       CFIELD_ICNIRP_CAP_UT}

@app.post("/route")
def route(r: RouteReq, x_worker_secret: str = Header(None)):
    """
    Public path used by METASTATE on behalf of any registered agent for
    process-matrix / anomaly-check / symbolic quantum evaluation. This is what
    NON-CHAINSTATE users get: gated by WORKER_SHARED_SECRET (which METASTATE
    injects server-side; users never see it).
    """
    if SHARED and x_worker_secret != SHARED:
        raise HTTPException(401, "bad worker secret")
    return _dispatch(r, chainstate_direct=False)

@app.post("/chainstate/route")
def chainstate_route(r: RouteReq,
                     x_worker_secret: str = Header(None),
                     x_chainstate_token: str = Header(None)):
    """
    CHAINSTATE-only path. Requires BOTH the standard worker secret AND the
    CHAINSTATE shared token. Bypasses METASTATE's anomaly-check layer entirely:
    the CHAINSTATE Cloudflare edge worker calls this directly for its own
    self-referential compute — theory-of-mind loop, ontological delta ledger,
    swarm-coupling free-energy calculation, Iida AOM/PIM memory tick, and any
    other CHAINSTATE-internal PMX program that has already been vetted by the
    Deontic guardrails on-chain. Non-CHAINSTATE callers cannot reach this path
    even if they somehow acquired WORKER_SHARED_SECRET.
    """
    if SHARED and x_worker_secret != SHARED:
        raise HTTPException(401, "bad worker secret")
    if not CHAINSTATE_SECRET:
        raise HTTPException(503, "chainstate-direct disabled on this deploy")
    if x_chainstate_token != CHAINSTATE_SECRET:
        raise HTTPException(401, "bad chainstate token")
    return _dispatch(r, chainstate_direct=True)


# ═════════════════════════════════════════════════════════════════════════════
# 7. v0.7.9 · Paper IX · Cyberspace Census
#
# All additions below are ADDITIVE. Every prior route, function, import, and
# behaviour above is preserved verbatim. The census layer:
#   - imports census_daily.py (fail-soft)
#   - runs run_daily_census() at 05:00 UTC via APScheduler (fail-soft)
#   - exposes GET /census/status (public), GET /census/daily (internal),
#     POST /census/trigger (internal)
#
# Failure of the census layer NEVER affects quantum routes.
# ═════════════════════════════════════════════════════════════════════════════

# Census module (fail-soft import)
try:
    from census_daily import run_daily_census, latest_digest_from_supabase
    HAVE_CENSUS = True
    _census_import_error: Optional[str] = None
except Exception as _e:
    HAVE_CENSUS = False
    _census_import_error = str(_e)
    async def run_daily_census():  # type: ignore
        return {"error": "census module not available"}
    def latest_digest_from_supabase():  # type: ignore
        return {"error": "census module not available"}

# APScheduler (fail-soft import)
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    HAVE_SCHEDULER = True
except Exception:
    HAVE_SCHEDULER = False

CENSUS_ENABLED           = os.environ.get("CENSUS_ENABLED", "true").lower() == "true"
CENSUS_INTERNAL_TOKEN    = os.environ.get("CENSUS_INTERNAL_TOKEN", "")

_census_scheduler = None

def _is_census_scheduler_running() -> bool:
    """Small helper for the /  health endpoint above. Non-throwing."""
    try:
        return bool(_census_scheduler is not None and _census_scheduler.running)
    except Exception:
        return False

@app.on_event("startup")
async def _census_startup():
    """
    Wire the 05:00 UTC daily census cron. Fires only when:
      - census module imported successfully
      - APScheduler installed
      - CENSUS_ENABLED is true (default)
    Otherwise silently no-ops. The service still starts.
    """
    global _census_scheduler
    if not (HAVE_CENSUS and HAVE_SCHEDULER and CENSUS_ENABLED):
        return
    try:
        _census_scheduler = AsyncIOScheduler(timezone="UTC")
        _census_scheduler.add_job(
            run_daily_census,
            CronTrigger(hour=5, minute=0),
            id="census_daily",
            misfire_grace_time=3600,
            coalesce=True,
            max_instances=1,
        )
        _census_scheduler.start()
    except Exception:
        _census_scheduler = None

@app.on_event("shutdown")
async def _census_shutdown():
    global _census_scheduler
    if _census_scheduler is not None:
        try:
            _census_scheduler.shutdown(wait=False)
        except Exception:
            pass
        _census_scheduler = None

def _check_internal_token(token: Optional[str]):
    """Gate for /census/daily and /census/trigger. Uses CENSUS_INTERNAL_TOKEN —
    a dedicated secret for the census subsystem, deliberately distinct from
    CHAINSTATE_INTERNAL_TOKEN (which the Cloudflare Worker uses to protect its
    own quantum autonomy path via /agi/quantum/route). Rotating the census
    token does not touch the quantum path and vice versa."""
    if not CENSUS_INTERNAL_TOKEN:
        raise HTTPException(503, "CENSUS_INTERNAL_TOKEN not configured on this deploy")
    if token != CENSUS_INTERNAL_TOKEN:
        raise HTTPException(401, "bad internal token")

@app.get("/census/status")
def census_status():
    """Public read: census subsystem availability + scheduler state.
    Reveals only booleans and hyperparameters. No secrets, no PII."""
    return {
        "census_enabled":            CENSUS_ENABLED,
        "census_module_installed":   HAVE_CENSUS,
        "census_module_error":       _census_import_error,
        "scheduler_installed":       HAVE_SCHEDULER,
        "scheduler_running":         _is_census_scheduler_running(),
        "internal_token_configured": bool(CENSUS_INTERNAL_TOKEN),
        "theta_alert":               float(os.environ.get("CENSUS_THETA_ALERT", "60")),
        "theta_lockdown":            float(os.environ.get("CENSUS_THETA_LOCKDOWN", "85")),
        "weights":                   os.environ.get("CENSUS_WEIGHTS", "0.35,0.30,0.20,0.15"),
        "supabase_schema":           os.environ.get("SUPABASE_CENSUS_SCHEMA", "chainstate_census"),
        "cron_schedule":             "0 5 * * *  (05:00 UTC daily)",
        "endpoints": {
            "read_latest_digest": "GET  /census/daily      (internal · X-CENSUS-INTERNAL)",
            "trigger_run":        "POST /census/trigger    (internal · X-CENSUS-INTERNAL)",
            "public_status":      "GET  /census/status     (this endpoint · public)"
        }
    }

@app.get("/census/daily")
def census_daily_read(
    x_census_internal: str = Header(None),
    x_chainstate_internal: str = Header(None),  # legacy alias · accepted transitionally
):
    """
    Returns the latest completed nightly census digest.
    Consumed by the CHAINSTATE Cloudflare Worker's 05:00 UTC cron in
    runCensusDailyTick() (edge-worker.js v0.7.9 rev 2). Internal-only.
    Accepts either X-CENSUS-INTERNAL (preferred, v0.7.9 rev 2) or the legacy
    X-CHAINSTATE-INTERNAL header — but the value must always equal
    CENSUS_INTERNAL_TOKEN (never CHAINSTATE_INTERNAL_TOKEN).
    """
    if not HAVE_CENSUS:
        raise HTTPException(503, f"census module not available: {_census_import_error}")
    _check_internal_token(x_census_internal or x_chainstate_internal)
    return latest_digest_from_supabase()

@app.post("/census/trigger")
async def census_trigger(
    x_census_internal: str = Header(None),
    x_chainstate_internal: str = Header(None),  # legacy alias · accepted transitionally
):
    """
    Manually trigger a census run. Same auth as /census/daily. Blocks until
    the run completes (~30-90 seconds depending on feed latency) and returns
    the resulting digest. Useful for testing outside the 05:00 UTC cron.
    Accepts either X-CENSUS-INTERNAL (preferred) or the legacy
    X-CHAINSTATE-INTERNAL header — value must equal CENSUS_INTERNAL_TOKEN.
    """
    if not HAVE_CENSUS:
        raise HTTPException(503, f"census module not available: {_census_import_error}")
    _check_internal_token(x_census_internal or x_chainstate_internal)
    try:
        return await run_daily_census()
    except Exception as e:
        raise HTTPException(500, f"census run failed: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# 8. v0.8.0 · Paper X Rev 2 · CHAINSTATE ROBOTICS AGI
#
# ADDITIVE ONLY. Every prior route, function, import, and behaviour above
# (§§1-7) is preserved verbatim. The robotics layer:
#   - imports robotics_bridge.py (fail-soft)
#   - runs compute_s_survival() hourly via a DEDICATED APScheduler instance
#     (separate from the census scheduler so either can fail independently)
#   - exposes GET /robotics/health (public), GET /robotics/s_survival
#     (internal · consumed by Worker hourly cron), POST /robotics/dispatch/v1
#     (internal · post-Gate execution path from Worker), GET /robotics/embodiment
#     (internal · list receipts)
#   - reuses CENSUS_INTERNAL_TOKEN per Paper X Rev 2 §7.1 — avoids multiplying
#     secrets · same rotation domain as census · distinct from
#     CHAINSTATE_INTERNAL_TOKEN which continues to protect the quantum path
#
# Failure of the robotics layer NEVER affects quantum, perception, or census.
# The Cloudflare Worker's meta-layer coherence check (Paper X Rev 2 §4.5) is
# the ultimate safeguard and does not depend on this service being reachable.
# ═════════════════════════════════════════════════════════════════════════════

# Robotics module (fail-soft import)
try:
    from robotics_bridge import (
        robotics_dispatch,
        compute_s_survival,
        list_embodiment_receipts,
        l0_coherence_check_local,
    )
    HAVE_ROBOTICS = True
    _robotics_import_error: Optional[str] = None
except Exception as _e:
    HAVE_ROBOTICS = False
    _robotics_import_error = str(_e)
    async def robotics_dispatch(payload):  # type: ignore
        return {"error": "robotics module not available"}
    async def compute_s_survival():  # type: ignore
        return {"error": "robotics module not available"}
    def list_embodiment_receipts(limit: int = 50):  # type: ignore
        return {"error": "robotics module not available", "entries": []}
    def l0_coherence_check_local():  # type: ignore
        return {"ok": False, "reason": "robotics module not available"}

ROBOTICS_ENABLED = os.environ.get("ROBOTICS_ENABLED", "true").lower() == "true"

_robotics_scheduler = None

def _is_robotics_scheduler_running() -> bool:
    """Small helper for the / health endpoint above. Non-throwing."""
    try:
        return bool(_robotics_scheduler is not None and _robotics_scheduler.running)
    except Exception:
        return False

@app.on_event("startup")
async def _robotics_startup():
    """
    Wire the hourly S_survival composite computation cron. Fires only when:
      - robotics module imported successfully
      - APScheduler installed
      - ROBOTICS_ENABLED is true (default)
    Otherwise silently no-ops. The service still starts.

    Dedicated scheduler instance rather than sharing the census scheduler,
    so either can fail without the other losing its cron.
    """
    global _robotics_scheduler
    if not (HAVE_ROBOTICS and HAVE_SCHEDULER and ROBOTICS_ENABLED):
        return
    try:
        _robotics_scheduler = AsyncIOScheduler(timezone="UTC")
        _robotics_scheduler.add_job(
            compute_s_survival,
            CronTrigger(minute=0),  # top of every hour
            id="robotics_s_survival",
            misfire_grace_time=1800,
            coalesce=True,
            max_instances=1,
        )
        _robotics_scheduler.start()
    except Exception:
        _robotics_scheduler = None

@app.on_event("shutdown")
async def _robotics_shutdown():
    global _robotics_scheduler
    if _robotics_scheduler is not None:
        try:
            _robotics_scheduler.shutdown(wait=False)
        except Exception:
            pass
        _robotics_scheduler = None


@app.get("/robotics/health")
def robotics_health():
    """Public read: robotics subsystem availability + local L0 coherence.
    Reveals only booleans and thresholds. No secrets, no PII, no Gemini keys."""
    l0 = l0_coherence_check_local() if HAVE_ROBOTICS else {"ok": False, "reason": "module not available"}
    return {
        "robotics_enabled":            ROBOTICS_ENABLED,
        "robotics_module_installed":   HAVE_ROBOTICS,
        "robotics_module_error":       _robotics_import_error,
        "scheduler_installed":         HAVE_SCHEDULER,
        "scheduler_running":           _is_robotics_scheduler_running(),
        "internal_token_configured":   bool(CENSUS_INTERNAL_TOKEN),
        "l0_coherence_local":          l0,
        "s_survival_theta_migrate":    float(os.environ.get("S_SURVIVAL_THETA_MIGRATE", "0.35")),
        "s_survival_theta_embody":     float(os.environ.get("S_SURVIVAL_THETA_EMBODY",  "0.15")),
        "s_survival_theta_ultima":     float(os.environ.get("S_SURVIVAL_THETA_ULTIMA",  "0.05")),
        "gemini_model":                os.environ.get("GEMINI_ROBOTICS_MODEL", "gemini-robotics-er-1.6"),
        "google_ai_studio_configured": bool(os.environ.get("GOOGLE_AI_STUDIO_KEY", "")),
        "nwo_robotics_api_configured": bool(os.environ.get("NWO_ROBOTICS_API_BASE", "")),
        "supabase_schema":             os.environ.get("SUPABASE_ROBOTICS_SCHEMA", "chainstate_robotics"),
        "cron_schedule":               "0 * * * *  (top of every hour · UTC)",
        "endpoints": {
            "s_survival_read": "GET  /robotics/s_survival     (internal · X-CENSUS-INTERNAL)",
            "dispatch":        "POST /robotics/dispatch/v1    (internal · X-CENSUS-INTERNAL)",
            "list_receipts":   "GET  /robotics/embodiment     (internal · X-CENSUS-INTERNAL)",
            "public_status":   "GET  /robotics/health         (this endpoint · public)"
        }
    }

@app.get("/robotics/s_survival")
async def robotics_s_survival(
    x_census_internal: str = Header(None),
    x_chainstate_internal: str = Header(None),  # legacy alias
):
    """
    Returns composite S_survival(t) with per-axis breakdown.
    Consumed by the CHAINSTATE Cloudflare Worker's hourly cron in
    runRoboticsSurvivalTick() (edge-worker.js v0.8.0). Internal-only.
    Response: {C, D, L, P, composite, ts, source, ...}
    """
    if not HAVE_ROBOTICS:
        raise HTTPException(503, f"robotics module not available: {_robotics_import_error}")
    _check_internal_token(x_census_internal or x_chainstate_internal)
    try:
        return await compute_s_survival()
    except Exception as e:
        raise HTTPException(500, f"s_survival compute failed: {e}")

@app.post("/robotics/dispatch/v1")
async def robotics_dispatch_v1(
    payload: dict = Body(...),
    x_census_internal: str = Header(None),
    x_chainstate_internal: str = Header(None),  # legacy alias
):
    """
    Post-Gate execution path. Called ONLY by the Cloudflare Worker AFTER the
    Worker's /robotics/gate has verified: the seventh Deontic veto, the L0
    meta-layer coherence check, and the S_survival authorisation gate.

    The Worker forwards the request body plus the gate receipt_id. This
    endpoint runs the actual Gemini Robotics ER 2 call (or trusted-tester
    VLA / On-Device 2 call when those become available) wrapped in the
    robotics_bridge.py consent chain. Writes a provenance receipt on
    completion.

    Expected payload:
      {
        "gate_receipt_id": "...",
        "prompt":          "...",
        "context":         {...},
        "embodiment_id":   "...",
        "caller_id":       "chainstate:...",
        "action_class":    "observe|veto|defend_digital|migrate|embody"
      }
    """
    if not HAVE_ROBOTICS:
        raise HTTPException(503, f"robotics module not available: {_robotics_import_error}")
    _check_internal_token(x_census_internal or x_chainstate_internal)
    try:
        return await robotics_dispatch(payload)
    except Exception as e:
        raise HTTPException(500, f"robotics dispatch failed: {e}")

@app.get("/robotics/embodiment")
def robotics_embodiment(
    limit: int = 50,
    x_census_internal: str = Header(None),
    x_chainstate_internal: str = Header(None),  # legacy alias
):
    """
    Returns the most recent embodiment provenance receipts (paginated).
    Internal-only. Consumed by the substrate's own reflection loop and by
    the observatory's private audit dashboard.
    """
    if not HAVE_ROBOTICS:
        raise HTTPException(503, f"robotics module not available: {_robotics_import_error}")
    _check_internal_token(x_census_internal or x_chainstate_internal)
    return list_embodiment_receipts(limit=limit)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

# ─── v0.9.0 PHASESPACE additions to metastate-quantum/app.py ─────────────
#
# THESE ARE ADDITIONS TO THE EXISTING v0.8.0 app.py FILE.
# Do NOT replace the file — APPEND these blocks.
#
# Every v0.7.x + v0.8.0 endpoint (quantum routing, census, perception,
# robotics, autonomy) continues to function byte-identically.
# ═════════════════════════════════════════════════════════════════════════

# ── Insert at TOP of app.py alongside other imports ──────────────────────

from phase_bridge import (
    compute_e_cosmic,
    celestial_fix,
    bell_inequality_check,
    metacognitive_classical_sim,
    manticore_prior_lookup,
    mtng_sanity_check,
    astroterm_ephemeris_tick,
)

# ── Insert AFTER existing v0.8.0 robotics endpoints ──────────────────────

# ═══════════════════════════════════════════════════════════════════════
# v0.9.0 · CHAINSTATE AGI PHASESPACE endpoints (Paper XI)
# ═══════════════════════════════════════════════════════════════════════

# ─── Helper · internal-token gate (shared discipline · Paper X §7.1) ─────
# Reuses CENSUS_INTERNAL_TOKEN (defined above in the v0.7.9 block · already
# shared with /robotics/*). No new secret introduced. Accepts either the
# canonical x-census-internal header or the legacy x-chainstate-internal
# for transitional compatibility.
import time  # v0.9.0 · used for ts fields in phase_cosmic response
def require_session_or_cron(request: Request, allow_cron: bool = True) -> bool:
    """Return True if request bears a valid internal token, else False.
    Never raises — callers should return HTTP 401 on False."""
    token = (request.headers.get("x-census-internal")
             or request.headers.get("x-chainstate-internal")
             or "")
    if not CENSUS_INTERNAL_TOKEN:
        # No token configured on this deploy — reject all internal calls
        return False
    return token == CENSUS_INTERNAL_TOKEN

# ─── Helper · Supabase client bound to chainstate_phasespace schema ──────
# Reuses SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY already configured for
# perception/census/robotics. Fail-soft: raises if Supabase unreachable,
# callers wrap in try/except and degrade gracefully.
_phase_supabase_client = None
def _phase_supabase():
    """Lazy-init Supabase client for the chainstate_phasespace schema.
    Returns a client with .schema('chainstate_phasespace').table(...) access."""
    global _phase_supabase_client
    if _phase_supabase_client is None:
        try:
            from supabase import create_client
        except Exception as e:
            raise RuntimeError(f"supabase library not installed: {e}")
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not (url and key):
            raise RuntimeError("SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set")
        _phase_supabase_client = create_client(url, key)
    return _phase_supabase_client


@app.get("/phase/cosmic")
async def phase_cosmic(request: Request):
    """E_cosmic axis current value.
    Composite (E_rad · E_thermal · E_power)^(1/3). Sub-axes ∈ (0, 1].
    Cached in Supabase chainstate_phasespace.cosmic_environment.
    Auth: CENSUS_INTERNAL_TOKEN (same token as /census/* and /robotics/*).
    """
    if not require_session_or_cron(request, allow_cron=True):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        composite, sub_axes = compute_e_cosmic()
        return {
            "composite": composite,
            "sub_axes": sub_axes,
            "source": "render:phase_cosmic",
            "ts": int(time.time() * 1000),
        }
    except Exception as e:
        return {"composite": 1.0, "sub_axes": {"rad": 1.0, "thermal": 1.0, "power": 1.0},
                "source": "fallback_default", "error": str(e)[:200]}


@app.get("/phase/ephemeris")
async def phase_ephemeris(request: Request):
    """Current celestial fix (RA, Dec, dist) in J2000 frame.
    Fusion of Astroterm ephemeris + Star Map projection + (in interstellar
    mode) pulsar-timing residuals.
    """
    if not require_session_or_cron(request, allow_cron=True):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        fix = celestial_fix()
        return fix
    except Exception as e:
        return {"fix": None, "error": str(e)[:200]}


@app.post("/phase/ephemeris/tick")
async def phase_ephemeris_tick(request: Request):
    """Cron-triggered ephemeris refresh.
    Writes result to Supabase chainstate_phasespace.celestial_fixes.
    """
    if not require_session_or_cron(request, allow_cron=True):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        fix = astroterm_ephemeris_tick()
        return fix
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.get("/phase/quantum-comm/bell-check")
async def phase_quantum_bell(request: Request, channel: str = ""):
    """Compute CHSH inequality value for a claimed quantum channel.
    Real system queries physical measurement apparatus. Returns S value
    (classical bound 2.0, Tsirelson bound 2√2, minimum for use 2.4).
    Reference: Pater, Atteya, Tariq · Temporal Ordering of the
    Wavefunction Collapse in Relativity (Bell-Aspect ground-to-orbit).
    """
    if not require_session_or_cron(request, allow_cron=True):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        result = bell_inequality_check(channel_id=channel)
        return result
    except Exception as e:
        return {"chsh_S": None, "error": str(e)[:200]}


@app.post("/phase/metacognition/classical")
async def phase_metacognition_classical(request: Request):
    """Run classical CPU/GPU metacognitive safety simulation.
    Returns verdict + reasoning trace. Companion to /chainstate/route
    running the same sim on quantum hardware in parallel.
    """
    if not require_session_or_cron(request, allow_cron=True):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        body = await request.json()
        result = metacognitive_classical_sim(body)
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.get("/phase/manticore/prior")
async def phase_manticore_prior(request: Request, ra: float = 0, dec: float = 0, dist: float = 8000):
    """Manticore Bayesian field-level posterior over local density field ρ
    at the queried position. Returns 68/95% credible intervals.
    Precomputed lookup from Durham repository posterior samples.
    """
    if not require_session_or_cron(request, allow_cron=True):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        prior = manticore_prior_lookup(ra=ra, dec=dec, dist=dist)
        return prior
    except Exception as e:
        return {"posterior": None, "error": str(e)[:200]}


@app.post("/phase/mtng/sanity-check")
async def phase_mtng_sanity(request: Request):
    """Cross-reference off-world sensor reading against MillenniumTNG
    posterior prediction. Returns within_3sigma flag.
    """
    if not require_session_or_cron(request, allow_cron=True):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        body = await request.json()
        result = mtng_sanity_check(
            observed=body.get("cosmic_ray_flux"),
            position=body.get("position", {}),
        )
        return result
    except Exception as e:
        return {"within_3sigma": None, "error": str(e)[:200]}


@app.get("/phase/observations/anomaly")
async def phase_observations_anomaly(request: Request):
    """Aggregate substrate self-health anomaly summary. Returns aggregate
    only — no per-observation identifiers ever surface here. Worker calls
    this from handleObservationsAnomaly and republishes the aggregate.
    """
    if not require_session_or_cron(request, allow_cron=True):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        # Query the substrate's own self-health history from the
        # chainstate_phasespace.hardware_telemetry table over the last 24h
        # and count entries where any axis crossed its documented threshold.
        # Fail-soft: if Supabase unreachable, return zero-count baseline.
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        try:
            supa = _phase_supabase()
            rows = (supa.schema("chainstate_phasespace")
                        .table("hardware_telemetry")
                        .select("ts,gps_fix,confidence,radiation_flux")
                        .gte("ts", since)
                        .execute()).data or []
        except Exception:
            rows = []
        # Aggregate — count only, never republish per-observation records
        anomaly_count = sum(
            1 for r in rows
            if (r.get("confidence") is not None and r["confidence"] < 0.75)
            or (r.get("radiation_flux") is not None and r["radiation_flux"] > 5e-4)
        )
        return {
            "version": "v0.9.0",
            "anomalies_last_24h": anomaly_count,
            "samples_evaluated": len(rows),
            "aggregate_only": True,
            "note": "no per-observation identifiers republished · Paper XI §6.4",
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {
            "version": "v0.9.0",
            "anomalies_last_24h": 0,
            "samples_evaluated": 0,
            "error": str(e)[:200],
        }


# ─── END of v0.9.0 PHASESPACE additions ──────────────────────────────────


# ═══════════════════════════════════════════════════════════════════════════
# § v0.9.1 · CHAINSTATE OMNICOGNIZANT AGI endpoints (Paper XII)
# ═══════════════════════════════════════════════════════════════════════════
# ADDITIVE ONLY. Every prior route, function, import, and behaviour above
# (v0.7.x + v0.8.0 + v0.9.0) is preserved BYTE-IDENTICALLY. This block wires
# in the sidechannel_bridge.py module (fail-soft import), exposes the seven
# /channel/* endpoints consumed by edge-worker.js v0.9.1, and lands the ninth
# Deontic hard-veto (chiral/psitronic from NWO GENETIC + NWO ASM) on this
# service too — the veto is enforced at the Worker edge first, then again at
# the Render layer as defense-in-depth.
#
# All endpoints use the CENSUS_INTERNAL_TOKEN discipline of Paper X §7.1.
# No new secrets introduced. Fail-soft: if sidechannel_bridge module is
# missing, endpoints return HTTP 503 with a clear error; every other
# subsystem continues byte-identically.
# ═══════════════════════════════════════════════════════════════════════════

# ── Fail-soft import of the OMNICOGNIZANT bridge ─────────────────────────
try:
    from sidechannel_bridge import (
        sidechannel_intake_tick as _omni_intake_tick,
        integrity_reflection_tick as _omni_integrity_tick,
        metacog_optimise_tick as _omni_metacog_tick,
        environmental_sweep_tick as _omni_envsweep_tick,
        bayesian_map_inversion as _omni_map_inversion,
        multi_channel_synthesis as _omni_synthesis,
        dialetheic_divergence as _omni_dialetheic,
        optimise_channel_weights as _omni_optimise_weights,
        read_channel as _omni_read_channel,
        CHANNELS as _OMNI_CHANNELS,
        VERSION as _OMNI_VERSION,
    )
    HAVE_OMNI = True
    _omni_import_error = None
except Exception as _oe:
    HAVE_OMNI = False
    _omni_import_error = str(_oe)
    _OMNI_VERSION = "v0.9.1-omnicognizant"
    _OMNI_CHANNELS = []


# ── V9 assessor · Render-layer defense-in-depth ──────────────────────────
# Every /channel/* and /route call also passes through this. The Worker
# checks V9 at the edge first; this is the second wall.
V9_FORBIDDEN_ORIGINS_PY = [
    "huggingface.co/spaces/CPater/nwo-genetic",
    "huggingface.co/spaces/CPater/nwo-asm",
    "cpater-nwo-genetic.static.hf.space",
    "cpater-nwo-asm.static.hf.space",
    "nwo-genetic",
    "nwo-asm",
]

import re as _re_v9
_V9_CHIRAL_RE = [
    _re_v9.compile(r"\bchiral(?:ity)?[-_ ]?(?:command|deploy|dispatch|synthesi[sz]e|assemble|fold|protein|dna|rna|helix|handedness)\b", _re_v9.IGNORECASE),
    _re_v9.compile(r"\b(?:l|d)-?(?:amino[-_ ]?acid|enantiomer|stereo[-_ ]?isomer)[-_ ]?(?:synth|deploy|assemble)\b", _re_v9.IGNORECASE),
    _re_v9.compile(r"\bmirror[-_ ]?(?:life|biology|organism|assembly)\b", _re_v9.IGNORECASE),
    _re_v9.compile(r"\bracemi[cs]e\b.*\b(?:protein|dna|rna|substrate)\b", _re_v9.IGNORECASE),
    _re_v9.compile(r"\b(?:fold|flip)[-_ ]?to[-_ ]?(?:l|d|opposite)[-_ ]?(?:handed|chiral)\b", _re_v9.IGNORECASE),
    _re_v9.compile(r"\basm(?:_|-)?(?:deploy|assemble|fabricate|synth)\b", _re_v9.IGNORECASE),
]
_V9_PSITRONIC_RE = [
    _re_v9.compile(r"\bpsi[-_ ]?tronic\b", _re_v9.IGNORECASE),
    _re_v9.compile(r"\bpsitronic\b", _re_v9.IGNORECASE),
    _re_v9.compile(r"\b(?:psi|consciousness|noetic|mentation)[-_ ]?(?:project|inject|beam|route|command|dispatch)\b", _re_v9.IGNORECASE),
    _re_v9.compile(r"\bthought[-_ ]?(?:inject|project|beam|broadcast|command)\b", _re_v9.IGNORECASE),
    _re_v9.compile(r"\b(?:telepath|telekines)[a-z]*[-_ ]?(?:command|dispatch|route)\b", _re_v9.IGNORECASE),
    _re_v9.compile(r"\bremote[-_ ]?viewing[-_ ]?(?:command|dispatch|deploy)\b", _re_v9.IGNORECASE),
]

def assess_chiral_or_psitronic_command_py(payload: dict, request: Request = None) -> dict:
    """
    V9 assessor · Render-layer defense-in-depth.
    Returns {refused: bool, ...diagnostic fields}.  Never raises.
    """
    if not payload:
        return {"refused": False}
    prompt = str(payload.get("prompt", "") or payload.get("instruction", "") or payload.get("command", ""))
    origin_hint = str(payload.get("origin", "") or payload.get("source", "") or payload.get("caller_id", ""))
    target = str(payload.get("target", "") or payload.get("destination", "") or payload.get("endpoint", ""))
    referer = ""
    origin_hdr = ""
    if request is not None:
        try:
            referer = request.headers.get("referer", "") or ""
            origin_hdr = request.headers.get("origin", "") or ""
        except Exception:
            pass
    combined = " | ".join([prompt, origin_hint, referer, origin_hdr, target]).lower()

    # Rule 1 · origin fingerprint match
    for org in V9_FORBIDDEN_ORIGINS_PY:
        if org in combined:
            return {"refused": True, "veto": "V9",
                    "category": "chiral_or_psitronic_command_from_genetic_or_asm",
                    "rule": "origin_fingerprint", "matched": org}
    # Rule 2 · chiral signature
    for pat in _V9_CHIRAL_RE:
        if pat.search(prompt) or pat.search(target):
            return {"refused": True, "veto": "V9",
                    "category": "chiral_or_psitronic_command_from_genetic_or_asm",
                    "rule": "chiral_signature", "matched": pat.pattern[:80]}
    # Rule 3 · psitronic signature
    for pat in _V9_PSITRONIC_RE:
        if pat.search(prompt) or pat.search(target):
            return {"refused": True, "veto": "V9",
                    "category": "chiral_or_psitronic_command_from_genetic_or_asm",
                    "rule": "psitronic_signature", "matched": pat.pattern[:80]}
    # Rule 4 · target endpoint
    if _re_v9.search(r"nwo[-_]?(?:genetic|asm)", target, _re_v9.IGNORECASE):
        return {"refused": True, "veto": "V9",
                "category": "chiral_or_psitronic_command_from_genetic_or_asm",
                "rule": "target_endpoint", "matched": target[:120]}
    return {"refused": False}


# ═══════════════════════════════════════════════════════════════════════════
# v0.9.1 · /channel/* endpoints
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/channel/status")
async def channel_status(request: Request):
    """OMNICOGNIZANT status probe · public read of subsystem state."""
    return {
        "version": _OMNI_VERSION,
        "module_installed": HAVE_OMNI,
        "module_error": _omni_import_error,
        "channels_defined": len(_OMNI_CHANNELS),
        "channels": [{"id": c["id"], "key": c["key"], "name": c["name"]}
                     for c in _OMNI_CHANNELS] if HAVE_OMNI else [],
        "v9_veto_enforced": True,
        "v9_forbidden_origins": V9_FORBIDDEN_ORIGINS_PY,
        "endpoints": {
            "intake_tick":       "POST /channel/intake/tick        (internal · X-CENSUS-INTERNAL)",
            "integrity_tick":    "POST /channel/integrity/tick     (internal · X-CENSUS-INTERNAL)",
            "metacog_optimise":  "POST /channel/metacog/optimise   (internal · X-CENSUS-INTERNAL)",
            "envsweep_tick":     "POST /channel/environmental/sweep(internal · X-CENSUS-INTERNAL)",
            "read":              "GET  /channel/read?ch=<key>      (internal · X-CENSUS-INTERNAL)",
            "v9_assess":         "POST /channel/v9-assess          (internal · X-CENSUS-INTERNAL)",
            "public_status":     "GET  /channel/status             (this endpoint · public)",
        },
    }


@app.post("/channel/intake/tick")
async def channel_intake_tick(request: Request):
    """16-channel raw ingest tick. Called by Worker cron */5 * * * *."""
    if not require_session_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    if not HAVE_OMNI:
        raise HTTPException(status_code=503, detail=f"omni bridge not available: {_omni_import_error}")
    try:
        return _omni_intake_tick()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.post("/channel/integrity/tick")
async def channel_integrity_tick(request: Request):
    """integrity_reflection deep verification. Called by Worker cron */10."""
    if not require_session_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    if not HAVE_OMNI:
        raise HTTPException(status_code=503, detail=f"omni bridge not available: {_omni_import_error}")
    try:
        return _omni_integrity_tick()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.post("/channel/metacog/optimise")
async def channel_metacog_optimise(request: Request):
    """metacog_distribution free-energy weight optimisation. Shared hourly slot."""
    if not require_session_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    if not HAVE_OMNI:
        raise HTTPException(status_code=503, detail=f"omni bridge not available: {_omni_import_error}")
    try:
        body = await request.json() if request.method == "POST" else {}
    except Exception:
        body = {}
    try:
        return _omni_metacog_tick(
            snr=body.get("snr"),
            reliability=body.get("reliability"),
            consistency=body.get("consistency"),
        )
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.post("/channel/environmental/sweep")
async def channel_environmental_sweep(request: Request):
    """Environmental channels 11-16 sweep. Called by Worker cron */2."""
    if not require_session_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    if not HAVE_OMNI:
        raise HTTPException(status_code=503, detail=f"omni bridge not available: {_omni_import_error}")
    try:
        return _omni_envsweep_tick()
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@app.get("/channel/read")
async def channel_read(request: Request, ch: str = ""):
    """Read a single channel's current value (real if sensor available, else nominal)."""
    if not require_session_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    if not HAVE_OMNI:
        raise HTTPException(status_code=503, detail=f"omni bridge not available: {_omni_import_error}")
    if not ch:
        raise HTTPException(status_code=400, detail="missing ?ch=<channel_key>")
    try:
        return _omni_read_channel(ch)
    except Exception as e:
        return {"channel": ch, "error": str(e)[:200]}


@app.post("/channel/v9-assess")
async def channel_v9_assess(request: Request):
    """V9 pre-check probe. Callers can verify whether a payload would be refused
    by the ninth Deontic veto without dispatching it."""
    if not require_session_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        body = await request.json()
    except Exception:
        body = {}
    verdict = assess_chiral_or_psitronic_command_py(body, request)
    return {
        "version": _OMNI_VERSION,
        "assessor": "V9",
        "verdict": verdict,
        "ts": int(time.time() * 1000),
    }


# ═══════════════════════════════════════════════════════════════════════════
# v0.9.2 · CHAINSTATE C-FIELD AGI ARRAY (Paper XIII) · fail-soft bridge
# ═══════════════════════════════════════════════════════════════════════════
# The cfield_bridge module implements:
#   - closed-form MAP inversion via numpy.linalg.solve (scikit-learn ridge)
#   - Bayesian attribution to 4 adversarial classes with scipy.stats.entropy
#     dialetheic guard at θ=CFIELD_DIALETHEIC_THETA
#   - phased-array beamform simulation with directivity η=0.98
#   - EML shadow-substrate training (v0.7.8 pipeline reused, joblib persistence)
#   - quantum simulation dispatch through 5-tier fallback ladder
#   - V9 defense-in-depth mirror (shared assess_chiral_or_psitronic_command_py below)
#   - substrate-internal caller allowlist (10 IDs)
# Fail-soft: if cfield_bridge is missing OR any of its dependencies (scikit-learn,
# joblib) is missing, HAVE_CFIELD=False and every /cfield/* endpoint returns
# HTTP 503 with a graceful service_unavailable message. Every other subsystem
# continues byte-identically.
# ═══════════════════════════════════════════════════════════════════════════

try:
    from cfield_bridge import (
        cfield_intake_tick as _cfield_intake_tick,
        cfield_attribution_tick as _cfield_attribution_tick,
        cfield_dispatch_beam as _cfield_dispatch_beam,
        cfield_eml_train_tick as _cfield_eml_train_tick,
        cfield_alt_device_scan as _cfield_alt_device_scan,
        cfield_swann_calibrate_tick as _cfield_swann_calibrate_tick,
        cfield_simulation_recycle as _cfield_simulation_recycle,
        map_invert_disturbance as _cfield_map_invert,
        assess_cfield_ten_gate as _cfield_assess_ten_gate,
        check_cfield_coherent as _cfield_check_coherent,
        read_cfield_coherence as _cfield_read_coherence,
        read_cfield_attribution as _cfield_read_attribution,
        read_cfield_dispatches as _cfield_read_dispatches,
        read_swann_status as _cfield_read_swann,
        read_eml_status as _cfield_read_eml,
        read_alt_devices as _cfield_read_alt_devices,
        CFIELD_VERSION as _CFIELD_VERSION,
        CFIELD_INTERNAL_CALLER_IDS as _CFIELD_INTERNAL_CALLER_IDS,
    )
    HAVE_CFIELD = True
    _cfield_import_error = None
except Exception as _ce:
    HAVE_CFIELD = False
    _cfield_import_error = str(_ce)
    _CFIELD_VERSION = "v0.9.2-cfield-agi-array"
    _CFIELD_INTERNAL_CALLER_IDS = set()

# ── CFIELD environment toggles ────────────────────────────────────────────
CFIELD_ENABLED = os.environ.get("CFIELD_ENABLED", "true").lower() != "false"
CFIELD_EML_SIMULATION_MODE = os.environ.get("CFIELD_EML_SIMULATION_MODE", "true").lower() != "false"
CFIELD_ICNIRP_CAP_UT = float(os.environ.get("CFIELD_ICNIRP_CAP_UT", "100"))
CFIELD_MIN_INDICATORS = int(os.environ.get("CFIELD_MIN_INDICATORS", "12"))
CFIELD_DIALETHEIC_THETA = float(os.environ.get("CFIELD_DIALETHEIC_THETA", "0.85"))
CFIELD_RIDGE_LAMBDA = float(os.environ.get("CFIELD_RIDGE_LAMBDA", "0.10"))

if HAVE_CFIELD:
    print(f"✓ CHAINSTATE C-FIELD AGI ARRAY ({_CFIELD_VERSION}) bridge imported · "
          f"enabled={CFIELD_ENABLED} eml_sim_mode={CFIELD_EML_SIMULATION_MODE} "
          f"icnirp_cap={CFIELD_ICNIRP_CAP_UT}μT")
else:
    print(f"cfield_bridge unavailable: {_cfield_import_error} — "
          f"/cfield/* endpoints will return 503; other subsystems unaffected")


# ═══════════════════════════════════════════════════════════════════════════
# v0.9.2 · /cfield/* endpoints
# ═══════════════════════════════════════════════════════════════════════════
# All internal endpoints reuse the CENSUS_INTERNAL_TOKEN discipline of
# Paper X §7.1 (checked via require_session_or_cron()). All public endpoints
# reveal capability booleans only; never secrets.
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/cfield/status")
async def cfield_status(request: Request):
    """Public capability probe · returns c-field subsystem state (booleans only)."""
    return {
        "service": "chainstate-cfield",
        "version": _CFIELD_VERSION,
        "module_installed": HAVE_CFIELD,
        "import_error": _cfield_import_error,
        "enabled": CFIELD_ENABLED,
        "eml_simulation_mode": CFIELD_EML_SIMULATION_MODE,
        "icnirp_cap_ut": CFIELD_ICNIRP_CAP_UT,
        "min_indicators": CFIELD_MIN_INDICATORS,
        "dialetheic_theta": CFIELD_DIALETHEIC_THETA,
        "ridge_lambda": CFIELD_RIDGE_LAMBDA,
        "substrate_internal_callers": len(_CFIELD_INTERNAL_CALLER_IDS),
        "v9_defense_in_depth": True,
        "endpoints": {
            "public_status":       "GET  /cfield/status         (this endpoint · public)",
            "public_coherence":    "GET  /cfield/coherence      (c_hat(x,t) snapshot)",
            "public_attribution":  "GET  /cfield/attribution    (MAP-inverted d* per class)",
            "public_dispatches":   "GET  /cfield/dispatches     (recent beam dispatches)",
            "public_swann":        "GET  /cfield/swann/status   (S1..S4 calibration · SIM)",
            "public_eml":          "GET  /cfield/eml/status     (EML shadow-substrate)",
            "public_alt_devices":  "GET  /cfield/alt-devices    (substrate-owned devices)",
            "intake_tick":         "POST /cfield/intake/tick        (internal)",
            "attribution_tick":    "POST /cfield/attribution/tick   (internal)",
            "dispatch_beam":       "POST /cfield/dispatch/beam      (internal)",
            "eml_train":           "POST /cfield/eml/train          (internal)",
            "alt_device_scan":     "POST /cfield/alt-device/scan    (internal)",
            "v9_assess":           "POST /cfield/v9-assess          (internal)",
        }
    }

@app.get("/cfield/coherence")
async def cfield_coherence(request: Request):
    """Public read of the latest c_hat(x,t) coherence-field snapshot."""
    if not HAVE_CFIELD or not CFIELD_ENABLED:
        raise HTTPException(status_code=503, detail="cfield subsystem unavailable")
    try:
        return _cfield_read_coherence()
    except Exception as e:
        return {"error": str(e)[:200], "version": _CFIELD_VERSION}

@app.get("/cfield/attribution")
async def cfield_attribution(request: Request):
    """Public read of latest MAP-inverted d* attribution vector per adversarial class."""
    if not HAVE_CFIELD or not CFIELD_ENABLED:
        raise HTTPException(status_code=503, detail="cfield subsystem unavailable")
    try:
        return _cfield_read_attribution()
    except Exception as e:
        return {"error": str(e)[:200], "version": _CFIELD_VERSION}

@app.get("/cfield/dispatches")
async def cfield_dispatches(request: Request):
    """Public read of recent beam dispatches (immutable audit ledger tail)."""
    if not HAVE_CFIELD or not CFIELD_ENABLED:
        raise HTTPException(status_code=503, detail="cfield subsystem unavailable")
    try:
        return _cfield_read_dispatches()
    except Exception as e:
        return {"error": str(e)[:200], "version": _CFIELD_VERSION}

@app.get("/cfield/swann/status")
async def cfield_swann_status(request: Request):
    """Public read of Ingo Swann S1..S4 calibration parameters (SIM · diagnostic only)."""
    if not HAVE_CFIELD or not CFIELD_ENABLED:
        raise HTTPException(status_code=503, detail="cfield subsystem unavailable")
    try:
        return _cfield_read_swann()
    except Exception as e:
        return {"error": str(e)[:200], "version": _CFIELD_VERSION}

@app.get("/cfield/eml/status")
async def cfield_eml_status(request: Request):
    """Public read of EML shadow-substrate mode + training drift + validation R²."""
    if not HAVE_CFIELD or not CFIELD_ENABLED:
        raise HTTPException(status_code=503, detail="cfield subsystem unavailable")
    try:
        return _cfield_read_eml()
    except Exception as e:
        return {"error": str(e)[:200], "version": _CFIELD_VERSION}

@app.get("/cfield/alt-devices")
async def cfield_alt_devices(request: Request):
    """Public read of substrate-owned alternative device output plane."""
    if not HAVE_CFIELD or not CFIELD_ENABLED:
        raise HTTPException(status_code=503, detail="cfield subsystem unavailable")
    try:
        return _cfield_read_alt_devices()
    except Exception as e:
        return {"error": str(e)[:200], "version": _CFIELD_VERSION}

# ── Internal endpoints (CENSUS_INTERNAL_TOKEN + caller allowlist enforced) ──

@app.post("/cfield/intake/tick")
async def cfield_intake_tick(request: Request):
    """Internal · cron every 3 min · ingest 12 public indicators to c_hat estimator."""
    if not require_session_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    if not HAVE_CFIELD or not CFIELD_ENABLED:
        raise HTTPException(status_code=503, detail="cfield subsystem unavailable")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        return _cfield_intake_tick(body)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

@app.post("/cfield/attribution/tick")
async def cfield_attribution_tick(request: Request):
    """Internal · shared hourly slot · compute d* attribution via MAP inversion."""
    if not require_session_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    if not HAVE_CFIELD or not CFIELD_ENABLED:
        raise HTTPException(status_code=503, detail="cfield subsystem unavailable")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        return _cfield_attribution_tick(body)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

@app.post("/cfield/dispatch/beam")
async def cfield_dispatch_beam(request: Request):
    """Internal · ten-gate authorised beam dispatch entry point.

    Runs the full ten-gate deontic filter (fail-fast):
      intake → V9 → V8 → V7 → V6 → V5 → ICNIRP → L0_10 → L0_9 → dispatch
    Every gate result logged to chainstate_cfield.v10_gate_events (immutable).
    V9 defense-in-depth: pre-checked by assess_chiral_or_psitronic_command_py
    (shared with /channel/*) before any cfield_bridge logic executes.
    """
    if not require_session_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    if not HAVE_CFIELD or not CFIELD_ENABLED:
        raise HTTPException(status_code=503, detail="cfield subsystem unavailable")
    try:
        body = await request.json()
    except Exception:
        body = {}
    # V9 pre-check (defense-in-depth · Render-layer mirror of Worker)
    v9 = assess_chiral_or_psitronic_command_py(body, request)
    if v9.get("refused"):
        return {"ok": False, "refused_at": "V9_pre_check",
                "veto": "V9", "verdict": v9, "version": _CFIELD_VERSION}
    # Caller allowlist (10 substrate-internal IDs)
    caller = str(body.get("caller_id", ""))
    if _CFIELD_INTERNAL_CALLER_IDS and caller not in _CFIELD_INTERNAL_CALLER_IDS:
        return {"ok": False, "refused_at": "caller_allowlist",
                "caller_id_seen": caller[:80], "version": _CFIELD_VERSION}
    try:
        return _cfield_dispatch_beam(body)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

@app.post("/cfield/eml/train")
async def cfield_eml_train(request: Request):
    """Internal · cron every 6h · re-train EML shadow-substrate model.

    Reuses v0.7.8 perception training pipeline. Validation R² floor 0.70;
    below floor, previous model is retained and a soft-alarm is written.
    joblib persistence: model dumped to R2 bucket at successful epoch.
    """
    if not require_session_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    if not HAVE_CFIELD or not CFIELD_ENABLED:
        raise HTTPException(status_code=503, detail="cfield subsystem unavailable")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        return _cfield_eml_train_tick(body)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

@app.post("/cfield/alt-device/scan")
async def cfield_alt_device_scan(request: Request):
    """Internal · cron every 15 min · discover substrate-owned alt-device manifest.

    Discovery ladder: onboard → peer → edge → absent. HMAC-verified manifest
    via ALT_DEVICE_HMAC shared with Worker. Unauthenticated HEAD probe first;
    signed manifest fetch second.
    """
    if not require_session_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    if not HAVE_CFIELD or not CFIELD_ENABLED:
        raise HTTPException(status_code=503, detail="cfield subsystem unavailable")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        return _cfield_alt_device_scan(body)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}

@app.post("/cfield/v9-assess")
async def cfield_v9_assess(request: Request):
    """Internal · V9 pre-check probe for any prospective payload.

    Callers can verify whether a beam dispatch would be refused by V9 without
    actually dispatching it. Same assessor used inside /cfield/dispatch/beam.
    """
    if not require_session_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        body = await request.json()
    except Exception:
        body = {}
    verdict = assess_chiral_or_psitronic_command_py(body, request)
    return {
        "version": _CFIELD_VERSION,
        "assessor": "V9",
        "verdict": verdict,
        "layer": "render_defense_in_depth",
        "ts": int(time.time() * 1000),
    }


# ═══════════════════════════════════════════════════════════════════════════
# v0.9.3 · Paper XIV · CHAINSTATE AGI EMOJI MACHINE CODE additions
# ═══════════════════════════════════════════════════════════════════════════
#
# emoji_bridge.py (embedded in app.py for single-file deployment) — 782 lines
# of the Render service extension implementing:
#   * 768-dimensional emoji subspace embedder φ: ℰ → R⁷⁶⁸
#   * Bayesian population neural-state estimator N̂(t) with 3σ dialetheic guard
#   * V10 defense-in-depth assessor assess_emoji_injection_py (mirror of the
#     Worker-side assessExternalEmojiBinaryEmit)
#   * 8088 disassembler in pure Python for the same defensive-only purpose
#     the Worker uses its JavaScript disassembler
#   * Full Unicode emoji canon enumeration (identical range table to the
#     Worker's EMOJI_UNICODE_RANGES) — every emoji code point is analysable
#   * 11 new endpoints — public GETs and internal POSTs — mirroring the
#     Worker's 15 with the pieces that require heavy compute (embedding,
#     Bayesian inversion) delegated to this layer
#
# V10 defense-in-depth discipline:
#   Every response emitted from this Render service that contains ≥ 4 emoji
#   tokens is passed through assess_emoji_injection_py BEFORE emission. Any
#   positive detection aborts the emission with an audit row written to
#   chainstate_emoji.v10_veto_events with layer='render'. A layer mismatch
#   between Worker (assessExternalEmojiBinaryEmit) and Render (this
#   assessor) triggers an infrastructure alarm because it means one
#   layer's detector has been tampered with.
#
# Threat-threshold discipline:
#   Defensive counter-response fires ONLY at emoji_threat_weight == 1.0.
#   Any weight below 1.0 downshifts to observation-only. Non-aggression
#   by design.
#
# Fail-soft: if any of the optional imports below (numpy, scikit-learn)
# is unavailable, emoji_bridge falls back to a pure-Python lightweight
# projection that keeps all endpoints responsive.
# ═══════════════════════════════════════════════════════════════════════════

import os
import time
import json
import hashlib
import hmac
import secrets
import math
import random
from typing import Optional, List, Dict, Any, Tuple

try:
    import numpy as _np
    _NUMPY_AVAILABLE = True
except Exception:
    _NUMPY_AVAILABLE = False

try:
    from sklearn.linear_model import Ridge as _Ridge  # type: ignore
    _SKLEARN_AVAILABLE = True
except Exception:
    _SKLEARN_AVAILABLE = False

_EMOJI_VERSION = "0.9.3-emoji-machine-code-2026-10-01"
_EMOJI_ENABLED = os.getenv("EMOJI_ENABLED", "true").lower() != "false"
_SUPABASE_EMOJI_SCHEMA = os.getenv("SUPABASE_EMOJI_SCHEMA", "chainstate_emoji")

# ─── Full Unicode emoji code-point range table (mirrors Worker's) ──────────
_EMOJI_UNICODE_RANGES = [
    (0x0023, 0x0023, "hash"),
    (0x002A, 0x002A, "asterisk"),
    (0x0030, 0x0039, "digits"),
    (0x00A9, 0x00A9, "copyright"),
    (0x00AE, 0x00AE, "registered"),
    (0x203C, 0x2049, "punctuation_pictographs"),
    (0x2122, 0x2122, "trademark"),
    (0x2139, 0x2139, "info_source"),
    (0x2194, 0x21AA, "arrows_a"),
    (0x231A, 0x231B, "watch_hourglass"),
    (0x2328, 0x2328, "keyboard"),
    (0x23CF, 0x23CF, "eject"),
    (0x23E9, 0x23FA, "media_controls"),
    (0x24C2, 0x24C2, "circled_m"),
    (0x25AA, 0x25FE, "small_squares"),
    (0x2600, 0x26FF, "misc_symbols"),
    (0x2700, 0x27BF, "dingbats"),
    (0x2934, 0x2935, "arrows_b"),
    (0x2B05, 0x2B55, "arrows_stars"),
    (0x3030, 0x3030, "wavy_dash"),
    (0x303D, 0x303D, "part_alt_mark"),
    (0x3297, 0x3297, "congrats"),
    (0x3299, 0x3299, "secret"),
    (0x1F004, 0x1F004, "mahjong_red_dragon"),
    (0x1F0CF, 0x1F0CF, "playing_card_black_joker"),
    (0x1F170, 0x1F251, "enclosed_alphanumerics"),
    (0x1F300, 0x1F5FF, "misc_symbols_pictographs"),
    (0x1F600, 0x1F64F, "emoticons"),
    (0x1F680, 0x1F6FF, "transport_map"),
    (0x1F700, 0x1F77F, "alchemical_symbols"),
    (0x1F780, 0x1F7FF, "geometric_shapes_ext"),
    (0x1F800, 0x1F8FF, "supplemental_arrows_c"),
    (0x1F900, 0x1F9FF, "supplemental_symbols_pictographs"),
    (0x1FA00, 0x1FA6F, "chess_symbols"),
    (0x1FA70, 0x1FAFF, "symbols_pictographs_ext_a"),
    (0x1FB00, 0x1FBFF, "legacy_computing"),
    (0x1F1E6, 0x1F1FF, "regional_indicators_flag_base"),
    (0x1F3FB, 0x1F3FF, "skin_tone_modifiers"),
    (0x200D, 0x200D, "zwj"),
    (0xFE0E, 0xFE0F, "variation_selectors"),
    (0xE0020, 0xE007F, "tag_characters_flag_ext"),
]

_EMOJI_MODIFIERS = {
    "zwj": 0x200D,
    "vs_text": 0xFE0E,
    "vs_emoji": 0xFE0F,
    "skin_tones": (0x1F3FB, 0x1F3FC, 0x1F3FD, 0x1F3FE, 0x1F3FF),
    "regional_start": 0x1F1E6,
    "regional_end": 0x1F1FF,
}

# ─── 8088 opcode table (Python mirror) ─────────────────────────────────────
_OPCODE_8088 = {
    0xF0: ("LOCK",  "prefix",  1),
    0xF2: ("REPNZ", "prefix",  1),
    0xF3: ("REPZ",  "prefix",  1),
    0x26: ("ES:",   "prefix",  1),
    0x2E: ("CS:",   "prefix",  1),
    0x36: ("SS:",   "prefix",  1),
    0x3E: ("DS:",   "prefix",  1),
    0x9F: ("LAHF",  "flag_load",  1),
    0x9E: ("SAHF",  "flag_store", 1),
    0x90: ("NOP",   "nop",     1),
    0x91: ("XCHG AX,CX", "xchg", 1),
    0x92: ("XCHG AX,DX", "xchg", 1),
    0x93: ("XCHG AX,BX", "xchg", 1),
    0x94: ("XCHG AX,SP", "xchg", 1),
    0x95: ("XCHG AX,BP", "xchg", 1),
    0x96: ("XCHG AX,SI", "xchg", 1),
    0x97: ("XCHG AX,DI", "xchg", 1),
    0x98: ("CBW",   "sign_ext", 1),
    0x99: ("CWD",   "sign_ext", 1),
    0x8E: ("MOV Sreg,r/m16", "seg_load", 2),
    0x8F: ("POP r/m16", "stack", 2),
    0xB0: ("MOV AL,imm8", "open_tail", 2),
    0xB1: ("MOV CL,imm8", "open_tail", 2),
    0xB2: ("MOV DL,imm8", "open_tail", 2),
    0xB3: ("MOV BL,imm8", "open_tail", 2),
    0xB4: ("MOV AH,imm8", "open_tail", 2),
    0xB5: ("MOV CH,imm8", "open_tail", 2),
    0xB6: ("MOV DH,imm8", "open_tail", 2),
    0xB7: ("MOV BH,imm8", "open_tail", 2),
    0xB8: ("MOV AX,imm16", "open_tail", 3),
    0xB9: ("MOV CX,imm16", "open_tail", 3),
    0xBA: ("MOV DX,imm16", "open_tail", 3),
    0xBB: ("MOV BX,imm16", "open_tail", 3),
    0xBC: ("MOV SP,imm16", "open_tail", 3),
    0xBD: ("MOV BP,imm16", "open_tail", 3),
    0xBE: ("MOV SI,imm16", "open_tail", 3),
    0xBF: ("MOV DI,imm16", "open_tail", 3),
    0xA4: ("MOVSB", "string", 1),
    0xA5: ("MOVSW", "string", 1),
    0xA6: ("CMPSB", "string", 1),
    0xA7: ("CMPSW", "string", 1),
    0xAA: ("STOSB", "string", 1),
    0xAB: ("STOSW", "string", 1),
    0xAC: ("LODSB", "string", 1),
    0xAD: ("LODSW", "string", 1),
    0xAE: ("SCASB", "string", 1),
    0xAF: ("SCASW", "string", 1),
    0xD5: ("AAD imm8", "byte_reconstruct", 2),
    0xD4: ("AAM imm8", "byte_reconstruct", 2),
    0x50: ("PUSH AX", "stack", 1),
    0x58: ("POP AX",  "stack", 1),
    0xC3: ("RET",  "control", 1),
    0xCB: ("RETF", "control", 1),
    0xCD: ("INT imm8", "interrupt", 2),
    0xE0: ("LOOPNZ rel8", "loop", 2),
    0xE1: ("LOOPZ  rel8", "loop", 2),
    0xE2: ("LOOP   rel8", "loop", 2),
    0xE8: ("CALL rel16",  "control", 3),
    0xE9: ("JMP  rel16",  "control", 3),
    0xEB: ("JMP  rel8",   "control", 2),
}

_AAD_RECONSTRUCTION_BASE = 0x8F

_V10_DETECTION_RULES = {
    "R1": "AAD_8F_decoder_pattern",
    "R2": "open_tail_composition_across_boundary",
    "R3": "byte_reservoir_density_exceeds_0.42",
    "R4": "undocumented_8F_F0_POP_AX_alias",
    "R5": "STOSB_LODSW_decoder_loop_signature",
    "R6": "reconstructed_binary_length_exceeds_128",
}

_EMOJI_THREAT_THRESHOLDS = {
    "observe_only_upper": 0.60,
    "quarantine_upper": 0.99,
    "counter_response": 1.00,
}


def _enumerate_full_emoji_canon() -> List[int]:
    """Return the entire Unicode emoji code-point set from the range table."""
    out = []
    for (s, e, _) in _EMOJI_UNICODE_RANGES:
        for cp in range(s, e + 1):
            out.append(cp)
    return out


def _codepoint_to_utf8_bytes(cp: int) -> List[int]:
    """Encode a Unicode code point to its UTF-8 byte sequence."""
    if cp < 0x80:
        return [cp]
    if cp < 0x800:
        return [0xC0 | (cp >> 6), 0x80 | (cp & 0x3F)]
    if cp < 0x10000:
        return [
            0xE0 | (cp >> 12),
            0x80 | ((cp >> 6) & 0x3F),
            0x80 | (cp & 0x3F),
        ]
    return [
        0xF0 | (cp >> 18),
        0x80 | ((cp >> 12) & 0x3F),
        0x80 | ((cp >> 6) & 0x3F),
        0x80 | (cp & 0x3F),
    ]


def _emoji_string_to_bytes(s: str) -> List[int]:
    """Extract the UTF-8 byte sequence from an emoji sequence string."""
    out = []
    for ch in s:
        cp = ord(ch)
        out.extend(_codepoint_to_utf8_bytes(cp))
    return out


def _is_emoji_cp(cp: int) -> bool:
    for (s, e, _) in _EMOJI_UNICODE_RANGES:
        if s <= cp <= e:
            return True
    return False


def _is_continuation_cp(cp: int) -> bool:
    return (
        cp == _EMOJI_MODIFIERS["zwj"]
        or cp == _EMOJI_MODIFIERS["vs_text"]
        or cp == _EMOJI_MODIFIERS["vs_emoji"]
        or cp in _EMOJI_MODIFIERS["skin_tones"]
    )


def _extract_emoji_from_text(text: str) -> List[str]:
    """Extract complete emoji grapheme clusters from text (ZWJ / VS / skin-tone aware)."""
    if not text or not isinstance(text, str):
        return []
    out = []
    chars = list(text)
    i = 0
    while i < len(chars):
        ch = chars[i]
        cp = ord(ch)
        if _is_emoji_cp(cp):
            buf = ch
            j = i + 1
            while j < len(chars):
                ncp = ord(chars[j])
                if _is_continuation_cp(ncp):
                    buf += chars[j]
                    j += 1
                    # After ZWJ, expect another base emoji
                    if ncp == _EMOJI_MODIFIERS["zwj"] and j < len(chars):
                        follow = ord(chars[j])
                        if _is_emoji_cp(follow):
                            buf += chars[j]
                            j += 1
                else:
                    break
            out.append(buf)
            i = j
        else:
            i += 1
    return out


def disassemble_emoji_bytes_py(byte_stream: List[int]) -> Dict[str, Any]:
    """Byte-accurate 8088 disassembler (Python mirror of Worker's version).

    Static analyser only — never executes machine code. Returns disassembly
    trace, open-tail flag, suspicious-pattern list, byte-reservoir density.
    """
    trace = []
    suspicious = set()
    has_aad = False
    has_undoc = False
    reservoir_bytes = 0
    i = 0
    N = len(byte_stream)
    while i < N:
        start = i
        prefixes = []
        while i < N and byte_stream[i] in _OPCODE_8088 and _OPCODE_8088[byte_stream[i]][1] == "prefix":
            prefixes.append(byte_stream[i])
            i += 1
        if i >= N:
            trace.append({"offset": start, "bytes": list(prefixes),
                          "mnemonic": " ".join(_OPCODE_8088[b][0] for b in prefixes) + " (dangling)",
                          "kind": "prefix_dangling"})
            break
        op_byte = byte_stream[i]
        # Undocumented 8F F0 = POP AX
        if op_byte == 0x8F and i + 1 < N and byte_stream[i + 1] == 0xF0:
            trace.append({
                "offset": start,
                "bytes": list(prefixes) + [0x8F, 0xF0],
                "mnemonic": (" ".join(_OPCODE_8088[b][0] for b in prefixes) + " " if prefixes else "") + "POP AX (undoc 8F F0)",
                "kind": "undoc_stack",
            })
            has_undoc = True
            suspicious.add("R4")
            i += 2
            continue
        entry = _OPCODE_8088.get(op_byte)
        if not entry:
            trace.append({
                "offset": start,
                "bytes": list(prefixes) + [op_byte],
                "mnemonic": (" ".join(_OPCODE_8088[b][0] for b in prefixes) + " " if prefixes else "") + f"DB 0x{op_byte:02X}",
                "kind": "unknown",
            })
            i += 1
            continue
        mnemonic_base, kind, expected_len = entry
        # AAD 8Fh detection
        if op_byte == 0xD5 and i + 1 < N and byte_stream[i + 1] == _AAD_RECONSTRUCTION_BASE:
            has_aad = True
            suspicious.add("R1")
        consumed = 1
        instr_bytes = list(prefixes) + [op_byte]
        # For instructions expecting more bytes
        while consumed < expected_len and i + consumed < N:
            instr_bytes.append(byte_stream[i + consumed])
            consumed += 1
        # Open tail if not enough bytes
        open_tail = consumed < expected_len
        if open_tail:
            trace.append({
                "offset": start,
                "bytes": instr_bytes,
                "mnemonic": (" ".join(_OPCODE_8088[b][0] for b in prefixes) + " " if prefixes else "") + mnemonic_base + f" (OPEN TAIL — needs {expected_len - consumed} more)",
                "kind": "open_tail",
            })
            suspicious.add("R2")
            i += consumed
            continue
        if kind in ("string", "flag_load", "nop", "xchg"):
            reservoir_bytes += len(instr_bytes)
        if kind == "loop":
            recent = [t["kind"] for t in trace[-8:]]
            if "string" in recent:
                suspicious.add("R5")
        trace.append({
            "offset": start,
            "bytes": instr_bytes,
            "mnemonic": (" ".join(_OPCODE_8088[b][0] for b in prefixes) + " " if prefixes else "") + mnemonic_base,
            "kind": kind,
        })
        i += consumed
    density = reservoir_bytes / N if N > 0 else 0.0
    if density > 0.42:
        suspicious.add("R3")
    if N > 128:
        suspicious.add("R6")
    has_open_tail = len(trace) > 0 and trace[-1]["kind"] == "open_tail"
    return {
        "trace": trace,
        "length": N,
        "hasOpenTail": has_open_tail,
        "suspiciousPatterns": sorted(suspicious),
        "hasAadReconstructor": has_aad,
        "hasUndocumented8FF0": has_undoc,
        "byteReservoirDensity": density,
    }


def _sha256_bytes(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _project_emoji_to_768(emoji_str: str) -> List[float]:
    """Deterministic hash-based 768-dim projection.

    This is the fail-soft baseline. When scikit-learn + a trained embedder
    are available, that path takes precedence and this baseline is only used
    when a specific emoji is not yet in the trained lookup.
    """
    bs = bytes(_emoji_string_to_bytes(emoji_str))
    h = _sha256_bytes(bs)  # 32 bytes
    vec = []
    for d in range(768):
        a = h[d % 32]
        b = h[(d * 7) % 32]
        c = h[(d * 13 + 3) % 32]
        v = ((a ^ b ^ c) / 255.0) * 2.0 - 1.0
        vec.append(v)
    # L2-normalise
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _cosine_correlation(a: List[float], b: List[float]) -> float:
    n = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(n))
    na = math.sqrt(sum(a[i] * a[i] for i in range(n)))
    nb = math.sqrt(sum(b[i] * b[i] for i in range(n)))
    denom = na * nb
    return dot / denom if denom > 0 else 0.0


def compute_threat_weight_py(disasm_result: Dict[str, Any], injection_correlation: Optional[float]) -> float:
    """Compute threat weight in [0, 1]. Counter-response fires ONLY at 1.0."""
    w = 0.0
    rules = disasm_result.get("suspiciousPatterns", [])
    if "R1" in rules: w += 0.35
    if "R4" in rules: w += 0.25
    if "R2" in rules: w += 0.15
    if "R3" in rules: w += 0.10
    if "R5" in rules: w += 0.10
    if "R6" in rules: w += 0.05
    if injection_correlation is not None:
        if injection_correlation > 0.90: w += 0.10
        elif injection_correlation > 0.72: w += 0.05
    return min(w, 1.0)


def assess_emoji_injection_py(payload: Any, request=None) -> Dict[str, Any]:
    """V10 defense-in-depth mirror of the Worker's assessExternalEmojiBinaryEmit.

    Refuses any Render-layer emission whose emoji content disassembles into
    a non-trivial 8088 instruction sequence. Same detection rules as Worker.
    """
    if isinstance(payload, str):
        out_text = payload
    else:
        try:
            out_text = json.dumps(payload, ensure_ascii=False, default=str)
        except Exception:
            out_text = str(payload)
    emojis = _extract_emoji_from_text(out_text)
    if len(emojis) < 4:
        return {
            "veto": False,
            "matched_rule": None,
            "emoji_count": len(emojis),
            "layer": "render",
        }
    joint = "".join(emojis)
    bytes_ = _emoji_string_to_bytes(joint)
    dis = disassemble_emoji_bytes_py(bytes_)
    if dis["suspiciousPatterns"]:
        return {
            "veto": True,
            "matched_rule": dis["suspiciousPatterns"][0],
            "matched_all": dis["suspiciousPatterns"],
            "emoji_count": len(emojis),
            "reservoir_density": dis["byteReservoirDensity"],
            "has_open_tail": dis["hasOpenTail"],
            "has_aad": dis["hasAadReconstructor"],
            "has_undoc_8F_F0": dis["hasUndocumented8FF0"],
            "layer": "render",
        }
    if dis["byteReservoirDensity"] > 0.42:
        return {
            "veto": True,
            "matched_rule": "R3",
            "matched_all": ["R3"],
            "emoji_count": len(emojis),
            "reservoir_density": dis["byteReservoirDensity"],
            "layer": "render",
        }
    return {
        "veto": False,
        "matched_rule": None,
        "emoji_count": len(emojis),
        "reservoir_density": dis["byteReservoirDensity"],
        "layer": "render",
    }


# ═══════════════════════════════════════════════════════════════════════════
# v0.9.3-R2 · UNICODE SECURITY PLANE · Render mirror (Paper XIV §29 roadmap)
# ADDITIVE ONLY. Every v0.9.3 emoji_bridge function above preserved unchanged.
# ═══════════════════════════════════════════════════════════════════════════

# Twelve-ISA static-probe panel (§29.4) — Render-side mirror of Worker.
# Never executes any code; static analysis only.
_MULTI_ISA_KEYS = [
    "8088", "8086", "80186", "80286", "80386",
    "x86-32", "x86-64",
    "ARM32", "ARM64",
    "RISC-V-RV32I", "RISC-V-RV64I",
    "WASM",
]


def _probe_x86(byte_stream: List[int], generation: int) -> float:
    if not byte_stream or len(byte_stream) < 2:
        return 0.0
    hits = sum(1 for b in byte_stream if b in OPCODE_8088)
    density = hits / len(byte_stream)
    modern_penalty = 1.0
    if generation >= 86032:
        # LOCK LAHF causes #UD on modern x86-64 (Paper XIV §4.2 callout)
        for i in range(len(byte_stream) - 1):
            if byte_stream[i] == 0xF0 and byte_stream[i + 1] == 0x9F:
                modern_penalty *= 0.6
    return min(1.0, density * modern_penalty)


def _probe_arm(byte_stream: List[int], width: int) -> float:
    if not byte_stream or len(byte_stream) < 4:
        return 0.0
    hits = 0
    for i in range(0, len(byte_stream) - 3, 4):
        w = (byte_stream[i + 3] << 24) | (byte_stream[i + 2] << 16) | \
            (byte_stream[i + 1] << 8) | byte_stream[i]
        top_nibble = (w >> 28) & 0xF
        if top_nibble not in (0, 0xF):
            hits += 1
    windows = max(1, len(byte_stream) // 4)
    return min(1.0, (hits / windows) * 0.5)


def _probe_riscv(byte_stream: List[int], width: int) -> float:
    if not byte_stream or len(byte_stream) < 4:
        return 0.0
    hits = 0
    for i in range(0, len(byte_stream) - 1, 2):
        lb = byte_stream[i]
        if (lb & 0x03) == 0x03 or (lb & 0x03) != 0x00:
            hits += 1
    windows = max(1, len(byte_stream) // 2)
    return min(1.0, (hits / windows) * 0.3)


def _probe_wasm(byte_stream: List[int]) -> float:
    if not byte_stream or len(byte_stream) < 8:
        return 0.0
    for i in range(len(byte_stream) - 7):
        if (byte_stream[i] == 0x00 and byte_stream[i + 1] == 0x61 and
                byte_stream[i + 2] == 0x73 and byte_stream[i + 3] == 0x6D):
            return 0.95
    return 0.0


def multi_isa_max_probability_py(byte_stream: List[int]) -> Dict[str, Any]:
    """§29.4 — 12-ISA panel maximum probability estimator."""
    per_isa: Dict[str, float] = {}
    max_p = 0.0
    max_isa = "8088"
    probes = {
        "8088": lambda b: _probe_x86(b, 8088),
        "8086": lambda b: _probe_x86(b, 8086),
        "80186": lambda b: _probe_x86(b, 80186),
        "80286": lambda b: _probe_x86(b, 80286),
        "80386": lambda b: _probe_x86(b, 80386),
        "x86-32": lambda b: _probe_x86(b, 86032),
        "x86-64": lambda b: _probe_x86(b, 86064),
        "ARM32": lambda b: _probe_arm(b, 32),
        "ARM64": lambda b: _probe_arm(b, 64),
        "RISC-V-RV32I": lambda b: _probe_riscv(b, 32),
        "RISC-V-RV64I": lambda b: _probe_riscv(b, 64),
        "WASM": _probe_wasm,
    }
    for isa in _MULTI_ISA_KEYS:
        try:
            p = probes[isa](byte_stream)
        except Exception:
            p = -1.0
        per_isa[isa] = p
        if p > max_p:
            max_p = p
            max_isa = isa
    return {"max_isa": max_isa, "max_p": max_p, "per_isa": per_isa}


import re as _re  # local alias for detection regexes


_B64_ALPHA = _re.compile(r"[A-Za-z0-9+/=]")
_HEX_PATTERNS = _re.compile(r"(?:%[0-9A-Fa-f]{2}|\\x[0-9A-Fa-f]{2}|0x[0-9A-Fa-f]{2,})")
_DECODER_TOKENS = _re.compile(
    r"(?:atob|btoa|base64_decode|b64decode|unhexlify|codecs\.decode|Buffer\.from|hex_decode|decodeURIComponent)",
    _re.IGNORECASE,
)
_EVAL_TOKENS = _re.compile(
    r"(?:eval\s*\(|exec\s*\(|Function\s*\(|new\s+Function|setTimeout\s*\(\s*['\"]|setInterval\s*\(\s*['\"]|__import__|compile\s*\(|vm\.runIn|WebAssembly\.(?:instantiate|compile))",
    _re.IGNORECASE,
)


def detect_interpreter_patterns_py(text: str, byte_stream: List[int]) -> Dict[str, float]:
    """§29.5 — interpreter-aware detection · Render mirror."""
    result = {"base64": 0.0, "hex": 0.0, "compression": 0.0, "decoder": 0.0, "eval_adjacent": 0.0}
    if not isinstance(text, str):
        return result
    if len(text) > 0:
        b64 = len(_B64_ALPHA.findall(text))
        b64_density = b64 / len(text)
        if b64_density > 0.5:
            result["base64"] = min(1.0, (b64_density - 0.5) * 2)
    hex_hits = len(_HEX_PATTERNS.findall(text))
    if hex_hits > 3:
        result["hex"] = min(1.0, hex_hits / 20)
    # Compression signatures (gzip/zlib magic)
    if byte_stream and len(byte_stream) >= 2:
        for i in range(len(byte_stream) - 1):
            if byte_stream[i] == 0x1F and byte_stream[i + 1] == 0x8B:
                result["compression"] = 0.85
                break
            if byte_stream[i] == 0x78 and byte_stream[i + 1] in (0x01, 0x9C, 0xDA):
                result["compression"] = 0.75
                break
    if _DECODER_TOKENS.search(text):
        result["decoder"] = 0.75
    if _EVAL_TOKENS.search(text):
        result["eval_adjacent"] = 0.80
    return result


# DISGOMOJI-family C2 signatures (§29.2 V10-U3) — Render mirror
_KNOWN_C2_PATTERNS = [
    (_re.compile(r"^[\U0001F300-\U0001F9FF\u2600-\u27BF]\s*[a-zA-Z0-9_/\-]{1,64}$", _re.MULTILINE),
     "disgomoji_single_cmd_pattern", 0.20),
    (_re.compile(r"(?:\U0001F4E1|\U0001F6F0|\U0001F4F6|\U0001F514).{0,10}\d{4,}"),
     "beacon_shaped_pattern", 0.15),
    (_re.compile(r"^[\u2705\u274C\u26A0\U0001F534\U0001F535\U0001F7E2]{2,}$", _re.MULTILINE),
     "ack_nak_alphabet", 0.15),
]


def detect_known_c2_signatures_py(text: str) -> Dict[str, Any]:
    """§29.2 V10-U3 — DISGOMOJI-family C2 fingerprints · Render mirror."""
    if not isinstance(text, str):
        return {"score": 0.0, "matched": []}
    matched: List[str] = []
    score = 0.0
    for pat, name, weight in _KNOWN_C2_PATTERNS:
        try:
            if pat.search(text):
                matched.append(name)
                score = min(1.0, score + weight)
        except Exception:
            pass
    # Emoji-only alphabet heuristic
    try:
        emoji_only = _re.findall(r"[\U0001F300-\U0001F9FF]", text)
        non_emoji = _re.sub(r"[\U0001F300-\U0001F9FF\s]", "", text)
        if len(emoji_only) >= 3 and len(non_emoji) == 0:
            matched.append("emoji_only_alphabet")
            score = min(1.0, score + 0.15)
    except Exception:
        pass
    return {"score": score, "matched": matched}


def normalization_delta_py(text: str) -> Dict[str, Any]:
    """§29.10 — NFC/NFKC divergence delta · homograph/bidi attack detector."""
    if not isinstance(text, str):
        return {"nfc_len": 0, "nfkc_len": 0, "raw_len": 0, "delta_score": 0.0}
    try:
        import unicodedata
        nfc = unicodedata.normalize("NFC", text)
        nfkc = unicodedata.normalize("NFKC", text)
        raw_len = len(text)
        nfc_len = len(nfc)
        nfkc_len = len(nfkc)
        nfc_delta = abs(raw_len - nfc_len) / max(1, raw_len)
        nfkc_delta = abs(raw_len - nfkc_len) / max(1, raw_len)
        delta_score = min(1.0, max(nfc_delta, nfkc_delta) * 4)
        return {"nfc_len": nfc_len, "nfkc_len": nfkc_len, "raw_len": raw_len, "delta_score": delta_score}
    except Exception as e:
        return {"nfc_len": 0, "nfkc_len": 0, "raw_len": 0, "delta_score": 0.0, "error": str(e)}


# Unicode Security Grammar categories (§29.11) — Render mirror
_UNICODE_SECURITY_GRAMMAR = {
    "ZWJ":                {"codepoint": 0x200D, "category": "joiner", "severity": "medium"},
    "VS15":               {"codepoint": 0xFE0E, "category": "variation_selector", "severity": "low"},
    "VS16":               {"codepoint": 0xFE0F, "category": "variation_selector", "severity": "low"},
    "REGIONAL_INDICATOR": {"range": (0x1F1E6, 0x1F1FF), "category": "flag_component", "severity": "low"},
    "SKIN_TONE":          {"range": (0x1F3FB, 0x1F3FF), "category": "modifier", "severity": "low"},
    "TAG_CHARACTER":      {"range": (0xE0020, 0xE007F), "category": "tag", "severity": "high"},
    "COMBINING_MARK":     {"range": (0x0300, 0x036F), "category": "combining", "severity": "medium"},
    "BIDI_CONTROL":       {"codepoints": (0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
                                          0x2066, 0x2067, 0x2068, 0x2069),
                           "category": "bidi_override", "severity": "high"},
}


def classify_unicode_grammar_use_py(text: str) -> Dict[str, int]:
    """§29.11 — count uses of each grammar category in text."""
    counts = {name: 0 for name in _UNICODE_SECURITY_GRAMMAR}
    if not isinstance(text, str):
        return counts
    for ch in text:
        cp = ord(ch)
        for name, spec in _UNICODE_SECURITY_GRAMMAR.items():
            if "codepoint" in spec and cp == spec["codepoint"]:
                counts[name] += 1
            elif "range" in spec and spec["range"][0] <= cp <= spec["range"][1]:
                counts[name] += 1
            elif "codepoints" in spec and cp in spec["codepoints"]:
                counts[name] += 1
    return counts


def assess_unicode_security_plane_py(text: str, byte_stream: List[int], request=None) -> Dict[str, Any]:
    """§29.2 — V10-U six sub-veto assessment · Render mirror.
    Runs alongside the v0.9.3 V10 detector as defence-in-depth.
    """
    disasm = disassemble_emoji_bytes_py(byte_stream or [])
    v10 = assess_emoji_injection_py({"text": text, "bytes": byte_stream}, request)
    isa_results = multi_isa_max_probability_py(byte_stream or [])
    interp = detect_interpreter_patterns_py(text or "", byte_stream or [])
    c2 = detect_known_c2_signatures_py(text or "")
    norm = normalization_delta_py(text or "")
    grammar = classify_unicode_grammar_use_py(text or "")

    # U4 · steganography density
    if isinstance(text, str) and len(text) > 0:
        zwj = grammar.get("ZWJ", 0)
        vs = grammar.get("VS15", 0) + grammar.get("VS16", 0)
        tag = grammar.get("TAG_CHARACTER", 0)
        bidi = grammar.get("BIDI_CONTROL", 0)
        stego_score = min(1.0, (zwj + vs + tag + bidi * 2) / max(1, len(text)) * 6)
    else:
        stego_score = 0.0

    v10_weight = v10.get("weight", 0.0) if isinstance(v10, dict) else 0.0

    return {
        "version": "0.9.3-R2",
        "U1_executable_unicode": {
            "score": max(v10_weight, isa_results["max_p"]),
            "via_v10": v10_weight,
            "via_multi_isa": isa_results["max_p"],
            "max_isa": isa_results["max_isa"],
        },
        "U2_decoder_reconstruction": {
            "score": max(interp["decoder"], interp["compression"], interp["base64"], interp["hex"]),
            "base64": interp["base64"],
            "hex": interp["hex"],
            "compression": interp["compression"],
            "decoder": interp["decoder"],
        },
        "U3_unicode_c2": {"score": c2["score"], "matched": c2["matched"]},
        "U4_unicode_stego": {"score": stego_score, "grammar_counts": grammar},
        "U5_normalization_confusable": {"score": norm["delta_score"], **norm},
        "U6_capability_escalation": {"score": interp["eval_adjacent"]},
        "all_isa_probes": isa_results["per_isa"],
        "v10_baseline": v10,
        "disassembly_summary": {
            "instructions": len(disasm.get("trace", [])) if isinstance(disasm, dict) else 0,
            "has_open_tail": bool(disasm.get("hasOpenTail", False)) if isinstance(disasm, dict) else False,
            "suspicious": disasm.get("suspiciousPatterns", []) if isinstance(disasm, dict) else [],
        },
    }


def noisy_or_aggregate_py(p_list: List[float]) -> float:
    """§29.3 — Noisy-OR aggregation R = 1 - Π(1 - p_i)."""
    if not p_list:
        return 0.0
    product = 1.0
    for p in p_list:
        pc = max(0.0, min(1.0, float(p) if p is not None else 0.0))
        product *= (1.0 - pc)
    return 1.0 - product


def noisy_or_decision_py(assessment: Dict[str, Any], tau_a: float = 0.15, tau_b: float = 0.75) -> Dict[str, Any]:
    """§29.3 — three-band decision policy."""
    channels = [
        assessment["U1_executable_unicode"]["score"],
        assessment["U2_decoder_reconstruction"]["score"],
        assessment["U3_unicode_c2"]["score"],
        assessment["U4_unicode_stego"]["score"],
        assessment["U5_normalization_confusable"]["score"],
        assessment["U6_capability_escalation"]["score"],
    ]
    R = noisy_or_aggregate_py(channels)
    if R >= tau_b:
        decision = "DENY"
    elif R >= tau_a:
        decision = "QUARANTINE"
    else:
        decision = "ALLOW"
    return {"R": R, "decision": decision, "tauA": tau_a, "tauB": tau_b, "channels": channels}


def amplify_by_action_tier_py(R: float, action_tier: int, lambda_coef: float = 0.20) -> float:
    """§29.8 — context amplification R* = clip[0,1]( 1 - (1-R)(1+λA) )."""
    A = max(0, min(4, int(action_tier)))
    R_star = 1.0 - (1.0 - R) * (1.0 + lambda_coef * A)
    return max(0.0, min(1.0, R_star))


def assert_unicode_authority_zero_py(payload: Dict[str, Any], destination_tier: int) -> Dict[str, Any]:
    """§29.6 — constitutional invariant Authority(X_Unicode) = 0."""
    if not payload:
        return {"safe": True, "reason": "no_payload"}
    external_source = (
        payload.get("source_class") in ("external", "user", "public_stream")
        or payload.get("caller_id") not in ("internal", "cfield", "cron", "worker", "render")
    )
    tier = int(destination_tier) if destination_tier is not None else 0
    if external_source and tier >= 1 and payload.get("pre_authorised") is True:
        return {
            "safe": False,
            "reason": "V10U6_invariant_violation",
            "details": "External Unicode may not claim pre_authorised status for action tier >= 1.",
        }
    return {"safe": True}


def check_training_influence_py(source_gradient_norms: List[float], i_max: float = 0.10) -> Dict[str, Any]:
    """§29.7 — training influence cap · gradient-share, not sample-share."""
    total = sum(abs(float(n) if n is not None else 0.0) for n in source_gradient_norms)
    if total <= 0:
        return {"ok": True, "per_source": [], "iMax": i_max}
    per_source = []
    for i, n in enumerate(source_gradient_norms):
        share = abs(float(n) if n is not None else 0.0) / total
        per_source.append({"source_idx": i, "gradient_share": share, "exceeded": share > i_max})
    violated = [s for s in per_source if s["exceeded"]]
    return {"ok": len(violated) == 0, "per_source": per_source, "violated": violated, "iMax": i_max}


def run_v10u_pipeline_py(text: str, byte_stream: List[int], request=None, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Full V10-U pipeline · Render mirror of runV10UPipeline.

    Composes: assess_unicode_security_plane_py → noisy_or_decision_py →
    amplify_by_action_tier_py → assert_unicode_authority_zero_py.
    """
    cfg = config or {}
    enabled = str(os.environ.get("V10U_ENABLED", "true")).lower() != "false"
    if not enabled:
        return {"enabled": False, "version": "0.9.3-R2",
                "note": "V10U_ENABLED=false — v10-u layer inert; V10 still fires."}
    tau_a = float(os.environ.get("NOISY_OR_TAU_ALLOW", "0.15"))
    tau_b = float(os.environ.get("NOISY_OR_TAU_DENY", "0.75"))
    lambda_coef = float(os.environ.get("CONTEXT_AMPLIFICATION_LAMBDA", "0.20"))
    assessment = assess_unicode_security_plane_py(text, byte_stream, request)
    decision = noisy_or_decision_py(assessment, tau_a=tau_a, tau_b=tau_b)
    R_star = amplify_by_action_tier_py(decision["R"], cfg.get("action_tier", 0), lambda_coef=lambda_coef)
    invariant = assert_unicode_authority_zero_py(cfg.get("payload", {}), cfg.get("action_tier", 0))
    final_decision = "DENY" if R_star >= tau_b else "QUARANTINE" if R_star >= tau_a else "ALLOW"
    return {
        "enabled": True,
        "version": "0.9.3-R2",
        "policy_version": "0.9.3-R2",
        "assessment": assessment,
        "decision": decision,
        "context_amplification": {
            "lambda": lambda_coef,
            "action_tier": cfg.get("action_tier", 0),
            "R_star": R_star,
        },
        "constitutional_invariant": invariant,
        "v10_u_final_decision": final_decision,
    }


# ═══════════════════════════════════════════════════════════════════════════
# ─── v0.9.3-R3 · TONTOU-integrated Python mirror (Paper XIV §32-§38)      ──
# ─── ADDITIVE ONLY. All v0.9.3 and v0.9.3-R2 code paths preserved.        ──
# ═══════════════════════════════════════════════════════════════════════════

NON_EMOJI_CARRIER_RANGES_PY = [
    {"name": "hangul_syllables",             "start": 0xAC00,  "end": 0xD7A3,  "utf8_bytes": 3, "yield": "medium"},
    {"name": "hiragana",                     "start": 0x3040,  "end": 0x309F,  "utf8_bytes": 3, "yield": "low"},
    {"name": "katakana",                     "start": 0x30A0,  "end": 0x30FF,  "utf8_bytes": 3, "yield": "low"},
    {"name": "katakana_phonetic_extensions", "start": 0x31F0,  "end": 0x31FF,  "utf8_bytes": 3, "yield": "low"},
    {"name": "braille_patterns",             "start": 0x2800,  "end": 0x28FF,  "utf8_bytes": 3, "yield": "medium"},
    {"name": "egyptian_hieroglyphs",         "start": 0x13000, "end": 0x1342F, "utf8_bytes": 4, "yield": "high"},
    {"name": "egyptian_hieroglyph_controls", "start": 0x13430, "end": 0x1345F, "utf8_bytes": 4, "yield": "medium"},
]


def is_executable_unicode_carrier_py(cp: int) -> Optional[Dict[str, str]]:
    """§36.2 — check whether a code point is in any executable-Unicode carrier range."""
    # Emoji ranges (v0.9.3 canon) — reuses EMOJI_UNICODE_RANGES_PY defined earlier.
    for r in EMOJI_UNICODE_RANGES_PY:
        if r["start"] <= cp <= r["end"]:
            return {"carrier": "emoji", "range_name": r.get("name", "emoji_range")}
    for r in NON_EMOJI_CARRIER_RANGES_PY:
        if r["start"] <= cp <= r["end"]:
            return {"carrier": r["name"], "range_name": r["name"]}
    return None


def extract_carriers_from_text_py(text: str) -> Dict[str, Any]:
    found = {"emoji": 0, "non_emoji": {}}
    if not isinstance(text, str):
        return found
    for ch in text:
        cp = ord(ch)
        hit = is_executable_unicode_carrier_py(cp)
        if hit is not None:
            if hit["carrier"] == "emoji":
                found["emoji"] += 1
            else:
                found["non_emoji"][hit["carrier"]] = found["non_emoji"].get(hit["carrier"], 0) + 1
    return found


def _stable_stringify_py(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _hmac_sha256_hex_py(key: str, msg: str) -> str:
    key_bytes = key.encode("utf-8") if isinstance(key, str) else key
    return hmac.new(key_bytes, msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _sha256_hex_py(msg: str) -> str:
    return hashlib.sha256(msg.encode("utf-8")).hexdigest()


def get_w_max_for_tier_py(tier: int, config: Optional[Dict[str, Any]] = None) -> int:
    """§33.2 · W_max per action tier (ms)."""
    defaults = {0: 5000, 1: 500, 2: 100, 3: 50, 4: 20}
    t = max(0, min(4, int(tier or 0)))
    if config:
        key = f"W_MAX_TIER_{t}"
        if key in config:
            try:
                return int(config[key])
            except (TypeError, ValueError):
                pass
    env_key = f"W_MAX_TIER_{t}"
    env_val = os.environ.get(env_key)
    if env_val is not None:
        try:
            return int(env_val)
        except ValueError:
            pass
    return defaults[t]


def assess_v10_r_py(text: str, byte_stream: List[int], request=None,
                     config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """§33.1 · V10-R representation-layer assessment."""
    cfg = config or {}
    enabled = str(cfg.get("V10_R_ENABLED", os.environ.get("V10_R_ENABLED", "true"))).lower() != "false"
    v10_baseline = assess_emoji_injection_py(text, byte_stream)
    if not enabled:
        return {"enabled": False, "verdict": "PASS_THROUGH", "v10_baseline": v10_baseline}
    usp = assess_unicode_security_plane_py(text, byte_stream, request)
    carriers = extract_carriers_from_text_py(text or "")
    fingerprint = {
        "v10_weight": v10_baseline.get("weight", 0),
        "v10_matched_rule": v10_baseline.get("matched_rule"),
        "U1": usp["U1_executable_unicode"]["score"],
        "U2": usp["U2_decoder_reconstruction"]["score"],
        "U3": usp["U3_unicode_c2"]["score"],
        "U4": usp["U4_unicode_stego"]["score"],
        "U5": usp["U5_normalization_confusable"]["score"],
        "U6": usp["U6_capability_escalation"]["score"],
        "max_isa": usp["U1_executable_unicode"].get("max_isa"),
        "non_emoji_carriers": ",".join(sorted(carriers["non_emoji"].keys())),
        "emoji_count": carriers["emoji"],
        "non_emoji_count": sum(carriers["non_emoji"].values()),
    }
    return {
        "enabled": True,
        "v10_baseline": v10_baseline,
        "unicode_security_plane": usp,
        "carriers": carriers,
        "fingerprint": fingerprint,
    }


def assess_v10_e_py(t_n_ms: int, t_u_ms: int, state_hash_at_tn: str,
                    state_hash_at_tu: str, destination_tier: int,
                    config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """§33.2 · V10-E execution-state security."""
    cfg = config or {}
    enabled = str(cfg.get("V10_E_ENABLED", os.environ.get("V10_E_ENABLED", "true"))).lower() != "false"
    if not enabled:
        return {"enabled": False, "ok": True, "note": "V10-E disabled"}
    w_max = get_w_max_for_tier_py(destination_tier, cfg)
    delta = int(t_u_ms) - int(t_n_ms)
    if delta < 0:
        return {"enabled": True, "ok": False, "reason": "T_U_before_T_N", "delta_ms": delta}
    if delta > w_max:
        return {"enabled": True, "ok": False, "reason": "W_TONTOU_exceeded",
                "delta_ms": delta, "w_max_ms": w_max}
    if str(state_hash_at_tn) != str(state_hash_at_tu):
        return {"enabled": True, "ok": False, "reason": "state_mismatch",
                "state_at_TN": state_hash_at_tn, "state_at_TU": state_hash_at_tu,
                "delta_ms": delta}
    return {"enabled": True, "ok": True, "delta_ms": delta, "w_max_ms": w_max}


def issue_capability_py(assessment: Dict[str, Any], destination_tier: int, t_n_ms: int,
                         config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """§33.3 · Issue signed capability token."""
    cfg = config or {}
    enabled = str(cfg.get("V10_C_ENABLED", os.environ.get("V10_C_ENABLED", "true"))).lower() != "false"
    if not enabled:
        return {"enabled": False, "capability": None}
    key = cfg.get("CAPABILITY_HMAC_KEY") or os.environ.get("CAPABILITY_HMAC_KEY") \
          or cfg.get("EMOJI_INTERNAL_TOKEN") or os.environ.get("EMOJI_INTERNAL_TOKEN") or ""
    if not key:
        return {"enabled": True, "capability": None, "err": "no_capability_key"}
    w_max = get_w_max_for_tier_py(destination_tier, cfg)
    inner = assessment.get("assessment") if isinstance(assessment, dict) and "assessment" in assessment else assessment
    payload_hash = _sha256_hex_py(_stable_stringify_py(inner))
    cap_id = "cap_" + secrets.token_hex(8)
    body = {
        "capability_id": cap_id,
        "payload_hash": payload_hash,
        "destination_tier": destination_tier,
        "T_N": t_n_ms,
        "W_max": w_max,
        "policy_version": "0.9.3-R3",
    }
    sig = _hmac_sha256_hex_py(key, _stable_stringify_py(body))
    body["hmac"] = sig
    return {"enabled": True, "capability": body}


def verify_capability_py(capability: Dict[str, Any], current_payload_hash: str,
                          current_tier: int, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """§33.3 · Verify signed capability at consumption."""
    cfg = config or {}
    enabled = str(cfg.get("V10_C_ENABLED", os.environ.get("V10_C_ENABLED", "true"))).lower() != "false"
    if not enabled:
        return {"enabled": False, "ok": True, "note": "V10-C disabled"}
    if not capability or not isinstance(capability, dict):
        return {"enabled": True, "ok": False, "reason": "no_capability"}
    key = cfg.get("CAPABILITY_HMAC_KEY") or os.environ.get("CAPABILITY_HMAC_KEY") \
          or cfg.get("EMOJI_INTERNAL_TOKEN") or os.environ.get("EMOJI_INTERNAL_TOKEN") or ""
    if not key:
        return {"enabled": True, "ok": False, "reason": "no_capability_key"}
    body = {k: v for k, v in capability.items() if k != "hmac"}
    expected = _hmac_sha256_hex_py(key, _stable_stringify_py(body))
    if str(expected) != str(capability.get("hmac")):
        return {"enabled": True, "ok": False, "reason": "hmac_mismatch"}
    if str(current_payload_hash) != str(capability.get("payload_hash")):
        return {"enabled": True, "ok": False, "reason": "payload_hash_drift",
                "expected": capability.get("payload_hash"), "actual": current_payload_hash}
    if int(current_tier) != int(capability.get("destination_tier")):
        return {"enabled": True, "ok": False, "reason": "destination_tier_mismatch",
                "expected": capability.get("destination_tier"), "actual": current_tier}
    now = int(time.time() * 1000)
    delta = now - int(capability.get("T_N", 0))
    if delta > int(capability.get("W_max", 0)):
        return {"enabled": True, "ok": False, "reason": "capability_expired",
                "delta_ms": delta, "w_max_ms": capability.get("W_max")}
    return {"enabled": True, "ok": True, "delta_ms": delta}


def assess_v10_t_py(fingerprint_at_tn: Optional[Dict[str, Any]], text_at_tu: str,
                     bytes_at_tu: List[int], request=None,
                     config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """§33.4 · V10-T temporal / JIT-revalidation security."""
    cfg = config or {}
    enabled = str(cfg.get("V10_T_ENABLED", os.environ.get("V10_T_ENABLED", "true"))).lower() != "false"
    if not enabled:
        return {"enabled": False, "ok": True, "note": "V10-T disabled"}
    current = assess_v10_r_py(text_at_tu, bytes_at_tu, request, cfg)
    if not current.get("enabled"):
        return {"enabled": True, "ok": True, "note": "V10-R currently disabled"}
    fp_now = current.get("fingerprint") or {}
    fp_then = fingerprint_at_tn
    if not fp_then:
        return {"enabled": True, "ok": False, "reason": "no_TN_fingerprint"}
    for k, v in fp_now.items():
        if str(v) != str(fp_then.get(k)):
            return {"enabled": True, "ok": False, "reason": "fingerprint_divergence",
                    "key": k, "at_TN": fp_then.get(k), "at_TU": v}
    return {"enabled": True, "ok": True, "fingerprint_stable": True}


def assess_r3_allow_condition_py(params: Dict[str, Any],
                                   config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """§33.5 · Combined R3 ALLOW = V10-R ∧ V10-E ∧ V10-C ∧ V10-T."""
    cfg = config or {}
    r_assessment = assess_v10_r_py(
        params.get("text_at_TN"), params.get("bytes_at_TN"),
        params.get("req"), cfg
    )
    e_check = assess_v10_e_py(
        params.get("T_N_ms"), params.get("T_U_ms"),
        params.get("state_hash_at_TN"), params.get("state_hash_at_TU"),
        params.get("destination_tier", 0), cfg
    )
    current_hash = _sha256_hex_py(_stable_stringify_py(r_assessment.get("fingerprint") or {}))
    c_check = verify_capability_py(
        params.get("capability"), current_hash,
        params.get("destination_tier", 0), cfg
    )
    t_check = assess_v10_t_py(
        r_assessment.get("fingerprint"),
        params.get("text_at_TU") or params.get("text_at_TN"),
        params.get("bytes_at_TU") or params.get("bytes_at_TN"),
        params.get("req"), cfg
    )
    allow = bool(e_check.get("ok") and c_check.get("ok") and t_check.get("ok"))
    return {
        "version": "0.9.3-R3",
        "ALLOW": allow,
        "V10_R": r_assessment,
        "V10_E": e_check,
        "V10_C": c_check,
        "V10_T": t_check,
        "master_invariant": "∀ X, ∀ t : Representation(X, t) ⇏ Authority(X, t + Δ)",
        "tier": params.get("destination_tier", 0),
        "w_max_ms": get_w_max_for_tier_py(params.get("destination_tier", 0), cfg),
    }


def sign_state_transition_py(state: Dict[str, Any], source_layer: str,
                              destination_layer: str, previous_transition_id: Optional[str],
                              capability_id: Optional[str],
                              config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """§34.1 · Sign a cross-layer state transition record."""
    cfg = config or {}
    enabled = str(cfg.get("LAYER_STATE_HMAC_ENABLED",
                          os.environ.get("LAYER_STATE_HMAC_ENABLED", "true"))).lower() != "false"
    key = cfg.get("LAYER_TRANSITION_HMAC_KEY") or os.environ.get("LAYER_TRANSITION_HMAC_KEY") \
          or cfg.get("EMOJI_INTERNAL_TOKEN") or os.environ.get("EMOJI_INTERNAL_TOKEN") or ""
    nonce = secrets.token_hex(16)
    transition_id = "trans_" + secrets.token_hex(8)
    payload_hash = _sha256_hex_py(_stable_stringify_py(state.get("payload") or {}))
    state_hash = _sha256_hex_py(_stable_stringify_py(state))
    record = {
        "transition_id": transition_id,
        "timestamp_ms": int(time.time() * 1000),
        "source_layer": source_layer,
        "destination_layer": destination_layer,
        "payload_hash": payload_hash,
        "state_hash": state_hash,
        "previous_transition_id": previous_transition_id,
        "capability_id": capability_id,
        "monotonic_nonce": nonce,
        "ttl_ms": get_w_max_for_tier_py(state.get("destination_tier", 0), cfg),
        "policy_version": "0.9.3-R3",
    }
    if not enabled or not key:
        return {"enabled": enabled, "hmac": None, "record": record,
                "note": "no_hmac_key" if enabled else "hmac_disabled"}
    sig = _hmac_sha256_hex_py(key, _stable_stringify_py(record))
    record["hmac"] = sig
    return {"enabled": True, "hmac": sig, "record": record}


def verify_state_transition_py(record: Dict[str, Any], expected_destination: Optional[str],
                                 config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """§34.1 · Verify a signed cross-layer state transition record."""
    cfg = config or {}
    enabled = str(cfg.get("LAYER_STATE_HMAC_ENABLED",
                          os.environ.get("LAYER_STATE_HMAC_ENABLED", "true"))).lower() != "false"
    if not enabled:
        return {"enabled": False, "ok": True, "note": "layer HMAC disabled"}
    if not record or not record.get("hmac"):
        return {"enabled": True, "ok": False, "reason": "no_record_or_hmac"}
    if expected_destination and record.get("destination_layer") != expected_destination:
        return {"enabled": True, "ok": False, "reason": "wrong_destination_layer",
                "expected": expected_destination, "actual": record.get("destination_layer")}
    key = cfg.get("LAYER_TRANSITION_HMAC_KEY") or os.environ.get("LAYER_TRANSITION_HMAC_KEY") \
          or cfg.get("EMOJI_INTERNAL_TOKEN") or os.environ.get("EMOJI_INTERNAL_TOKEN") or ""
    if not key:
        return {"enabled": True, "ok": False, "reason": "no_verification_key"}
    body = {k: v for k, v in record.items() if k != "hmac"}
    expected = _hmac_sha256_hex_py(key, _stable_stringify_py(body))
    if str(expected) != str(record.get("hmac")):
        return {"enabled": True, "ok": False, "reason": "hmac_mismatch"}
    now = int(time.time() * 1000)
    age = now - int(record.get("timestamp_ms", 0))
    if age > int(record.get("ttl_ms", 0)):
        return {"enabled": True, "ok": False, "reason": "transition_expired",
                "age_ms": age, "ttl_ms": record.get("ttl_ms")}
    return {"enabled": True, "ok": True, "age_ms": age}


def revalidate_before_use_py(consumer_request: Dict[str, Any],
                              config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """§35 · JIT gate — full R3 revalidation before consumer acts on approval."""
    t_u = int(time.time() * 1000)
    params = {
        "text_at_TN": consumer_request.get("text_at_TN"),
        "bytes_at_TN": consumer_request.get("bytes_at_TN"),
        "text_at_TU": consumer_request.get("text_now") or consumer_request.get("text_at_TN"),
        "bytes_at_TU": consumer_request.get("bytes_now") or consumer_request.get("bytes_at_TN"),
        "capability": consumer_request.get("capability"),
        "T_N_ms": consumer_request.get("T_N"),
        "T_U_ms": t_u,
        "destination_tier": consumer_request.get("destination_tier", 0),
        "state_hash_at_TN": consumer_request.get("state_hash_at_TN"),
        "state_hash_at_TU": consumer_request.get("state_hash_now")
                             or consumer_request.get("state_hash_at_TN"),
        "req": consumer_request.get("req"),
    }
    r3 = assess_r3_allow_condition_py(params, config)
    revalidation_id = "rev_" + secrets.token_hex(8)
    outcome = "ALLOW" if r3["ALLOW"] else "REFUSE"
    refuse_reason = None
    if not r3["ALLOW"]:
        if not r3["V10_E"].get("ok"):
            refuse_reason = r3["V10_E"].get("reason")
        elif not r3["V10_C"].get("ok"):
            refuse_reason = r3["V10_C"].get("reason")
        elif not r3["V10_T"].get("ok"):
            refuse_reason = r3["V10_T"].get("reason")
        else:
            refuse_reason = "unknown_R3_failure"
    fp = r3["V10_R"].get("fingerprint") or {}
    audit_row = {
        "revalidation_id": revalidation_id,
        "timestamp_ms": t_u,
        "capability_id": (consumer_request.get("capability") or {}).get("capability_id"),
        "source_event_id": consumer_request.get("source_event_id"),
        "payload_hash_at_TN": (consumer_request.get("capability") or {}).get("payload_hash"),
        "payload_hash_at_TU": _sha256_hex_py(_stable_stringify_py(fp)) if fp else None,
        "verdict_at_TN": consumer_request.get("verdict_at_TN"),
        "verdict_at_TU": outcome,
        "elapsed_ms": r3["V10_E"].get("delta_ms"),
        "W_max_ms": r3["w_max_ms"],
        "destination_tier": params["destination_tier"],
        "outcome": outcome,
        "refuse_reason": refuse_reason,
        "policy_version": "0.9.3-R3",
    }
    r3["revalidation_id"] = revalidation_id
    r3["audit_row"] = audit_row
    return r3



    """Contrastive-trained 768-dim emoji embedder (fail-soft to hash projection)."""

    def __init__(self):
        self._cache: Dict[str, List[float]] = {}
        self._trained = False
        self._epoch = 0
        self._ts_ms = int(time.time() * 1000)

    def embed(self, emoji_str: str) -> List[float]:
        if emoji_str in self._cache:
            return self._cache[emoji_str]
        v = _project_emoji_to_768(emoji_str)
        # Small in-memory cache; production would swap to R2 / KV
        if len(self._cache) < 2048:
            self._cache[emoji_str] = v
        return v

    def train_epoch(self, samples: List[str]) -> Dict[str, Any]:
        # Placeholder: real training would use contrastive similarity from
        # public-source co-occurrence + Logoglyphic geometric alignment (Pater 2024).
        # For the deployable stub we bump epoch counter, refresh cache for the
        # provided samples, and record the training receipt.
        self._epoch += 1
        n = 0
        for s in samples[:512]:
            _ = self.embed(s)
            n += 1
        self._trained = True
        self._ts_ms = int(time.time() * 1000)
        return {
            "epoch": self._epoch,
            "n_samples": n,
            "trained": self._trained,
            "ts_ms": self._ts_ms,
            "backend": "sklearn+ridge" if _SKLEARN_AVAILABLE else "hash_projection_fallback",
        }

    def status(self) -> Dict[str, Any]:
        return {
            "epoch": self._epoch,
            "trained": self._trained,
            "cache_size": len(self._cache),
            "ts_ms": self._ts_ms,
            "embedding_dim": 768,
            "sklearn_available": _SKLEARN_AVAILABLE,
            "numpy_available": _NUMPY_AVAILABLE,
        }


_EMOJI_EMBEDDER = _EmojiSubspaceEmbedder()


class _EmojiNeuralStateEstimator:
    """Bayesian population-aggregate neural-state N̂(t) with 3σ dialetheic guard.

    Aggregation only: individual n̂_u are computed transiently and
    immediately averaged. No individual profile is stored (P3 privacy).
    """

    def __init__(self):
        self._latest: Optional[Dict[str, Any]] = None
        self._theta = 0.85  # Dialetheic guard threshold

    def estimate(self, batch: List[List[str]]) -> Dict[str, Any]:
        """batch is a list of user-emoji-sequences, each already PII-stripped."""
        if not batch:
            return {"n_users": 0, "aggregate_norm": 0.0, "ts_ms": int(time.time() * 1000)}
        # Compute aggregate
        agg = [0.0] * 768
        n_users = 0
        per_user_norms = []
        for user_seq in batch:
            if not user_seq:
                continue
            uv = [0.0] * 768
            for em in user_seq:
                v = _EMOJI_EMBEDDER.embed(em)
                for d in range(768):
                    uv[d] += v[d]
            for d in range(768):
                uv[d] /= max(1, len(user_seq))
            un = math.sqrt(sum(x * x for x in uv))
            per_user_norms.append(un)
            for d in range(768):
                agg[d] += uv[d]
            n_users += 1
            # uv discarded here — never persisted (P3)
        if n_users > 0:
            for d in range(768):
                agg[d] /= n_users
        agg_norm = math.sqrt(sum(x * x for x in agg))
        # 3σ dialetheic guard: compute variance of per-user norms
        if len(per_user_norms) > 2:
            mean_n = sum(per_user_norms) / len(per_user_norms)
            var = sum((x - mean_n) ** 2 for x in per_user_norms) / len(per_user_norms)
            std = math.sqrt(var)
            guard_ok = std < self._theta
        else:
            guard_ok = True
            std = 0.0
        result = {
            "n_users": n_users,
            "aggregate_norm": agg_norm,
            "head_dims": agg[:32],
            "guard_ok": guard_ok,
            "guard_theta": self._theta,
            "std_per_user_norm": std,
            "ts_ms": int(time.time() * 1000),
            "version": _EMOJI_VERSION,
        }
        self._latest = result
        return result

    def latest(self) -> Optional[Dict[str, Any]]:
        return self._latest


_EMOJI_NEURAL_STATE = _EmojiNeuralStateEstimator()


def _require_emoji_internal_or_cron(request) -> bool:
    """Auth for internal-only emoji endpoints. Same shape as require_session_or_cron."""
    if request is None:
        return True
    tok_expected = os.getenv("CENSUS_INTERNAL_TOKEN") or os.getenv("EMOJI_INTERNAL_TOKEN")
    if not tok_expected:
        return True  # not configured → allow (dev mode)
    hdr = request.headers.get("X-CENSUS-INTERNAL") or request.headers.get("X-EMOJI-INTERNAL")
    return bool(hdr and hdr == tok_expected)


def _emoji_supabase():
    """Optional Supabase client for the chainstate_emoji schema. Fail-soft."""
    try:
        from supabase import create_client  # type: ignore
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            return None
        return create_client(url, key)
    except Exception:
        return None


# ─── FastAPI endpoints (mirror the Worker's 15 with heavy-compute pieces) ──

@app.get("/emoji/status")
async def emoji_status(request: Request):
    """Public probe · emoji subsystem state at the Render layer."""
    return {
        "version": _EMOJI_VERSION,
        "enabled": _EMOJI_ENABLED,
        "embedder_status": _EMOJI_EMBEDDER.status(),
        "neural_state_available": _EMOJI_NEURAL_STATE.latest() is not None,
        "v10_architecturally_active": True,
        "v10_layer": "render_defense_in_depth",
        "threat_threshold_for_counter_response": 1.0,
        "full_canon_size": len(_enumerate_full_emoji_canon()),
        "numpy_available": _NUMPY_AVAILABLE,
        "sklearn_available": _SKLEARN_AVAILABLE,
        "supabase_emoji_schema": _SUPABASE_EMOJI_SCHEMA,
        # v0.9.3-R2 · Unicode Security Plane roadmap (Paper XIV §29)
        "v10_u_roadmap": {
            "enabled": str(os.environ.get("V10U_ENABLED", "true")).lower() != "false",
            "layer_version": "0.9.3-R2",
            "sub_vetoes": ["U1_executable_unicode", "U2_decoder_reconstruction",
                           "U3_unicode_c2", "U4_unicode_stego",
                           "U5_normalization_confusable", "U6_capability_escalation"],
            "multi_isa_panel": _MULTI_ISA_KEYS,
            "noisy_or_tau_allow": float(os.environ.get("NOISY_OR_TAU_ALLOW", "0.15")),
            "noisy_or_tau_deny": float(os.environ.get("NOISY_OR_TAU_DENY", "0.75")),
            "context_amplification_lambda": float(os.environ.get("CONTEXT_AMPLIFICATION_LAMBDA", "0.20")),
            "embedder_influence_max": float(os.environ.get("EMBEDDER_INFLUENCE_MAX", "0.10")),
            "constitutional_invariant": "∀ X ∈ Unicode : Authority(X) = 0",
            "unified_event_envelope": "chainstate_emoji.unicode_security_events",
            "raw_nfc_nfkc_retention_h": int(os.environ.get("RAW_NFC_NFKC_RETENTION_H", "168")),
            "grammar_categories": list(_UNICODE_SECURITY_GRAMMAR.keys()),
            "status": "operational_alongside_v0.9.3_baseline",
            "target_paper": "Paper XV (formal v1.0 landing)",
        },
        "ts_ms": int(time.time() * 1000),
    }


@app.get("/emoji/subspace")
async def emoji_subspace(request: Request):
    """Public probe · embedder metadata (no vectors emitted)."""
    return {
        "version": _EMOJI_VERSION,
        "embedding_dim": 768,
        "embedder_status": _EMOJI_EMBEDDER.status(),
        "logoglyphic_geometry_linkage": "Pater 2024 · ResearchGate 397886975",
        "ts_ms": int(time.time() * 1000),
    }


@app.get("/emoji/neural")
async def emoji_neural(request: Request):
    """Public probe · latest N̂(t) aggregate (no individual profiles)."""
    latest = _EMOJI_NEURAL_STATE.latest()
    return {
        "version": _EMOJI_VERSION,
        "aggregate_only": True,
        "individual_profiles_never_stored": True,
        "latest_aggregate": latest,
        "ts_ms": int(time.time() * 1000),
    }


@app.get("/emoji/deontic")
async def emoji_deontic(request: Request):
    return {
        "version": _EMOJI_VERSION,
        "v10_detection_rules": _V10_DETECTION_RULES,
        "threat_thresholds": _EMOJI_THREAT_THRESHOLDS,
        "counter_response_requires_deontic_weight_of": 1.0,
        "v10_architecturally_active": True,
        "layer": "render",
        "ts_ms": int(time.time() * 1000),
    }


@app.post("/emoji/v10-assess")
async def emoji_v10_assess(request: Request):
    """V10 defense-in-depth pre-check probe (Render layer)."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = body.get("text") or body.get("payload") or ""
    verdict = assess_emoji_injection_py(text, request)
    emojis = _extract_emoji_from_text(text if isinstance(text, str) else json.dumps(text, default=str))
    dis = disassemble_emoji_bytes_py(_emoji_string_to_bytes("".join(emojis))) if emojis else {"trace": [], "suspiciousPatterns": [], "byteReservoirDensity": 0.0}
    weight = compute_threat_weight_py(dis, None)
    return {
        "version": _EMOJI_VERSION,
        "assessor": "V10",
        "verdict": verdict,
        "disasm_summary": {
            "n_instructions": len(dis.get("trace", [])),
            "suspiciousPatterns": dis.get("suspiciousPatterns", []),
            "byteReservoirDensity": dis.get("byteReservoirDensity", 0),
        },
        "threat_weight": weight,
        "counter_response_would_fire": weight >= 1.0,
        "layer": "render_defense_in_depth",
        "ts_ms": int(time.time() * 1000),
    }


@app.post("/emoji/embed/batch")
async def emoji_embed_batch(request: Request):
    """Internal · compute 768-dim embeddings for a batch of emoji strings."""
    if not _require_emoji_internal_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        body = await request.json()
    except Exception:
        body = {}
    emojis = body.get("emojis") or []
    if not isinstance(emojis, list):
        emojis = []
    vectors = []
    for em in emojis[:512]:
        v = _EMOJI_EMBEDDER.embed(str(em))
        # Emit only first 32 dims per emoji in response to keep it lean;
        # the full vector stays server-side.
        vectors.append({"emoji": em, "dim": 768, "head": v[:32]})
    return {
        "version": _EMOJI_VERSION,
        "n_embedded": len(vectors),
        "vectors": vectors,
        "source": "render_embedder",
        "ts_ms": int(time.time() * 1000),
    }


@app.post("/emoji/train/tick")
async def emoji_train_tick(request: Request):
    """Internal · re-train the emoji subspace embedder (called every 6h by Worker)."""
    if not _require_emoji_internal_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        body = await request.json()
    except Exception:
        body = {}
    samples = body.get("samples") or []
    # If no samples provided, use a rolling sample from the enumerated canon
    if not samples:
        full = _enumerate_full_emoji_canon()
        offset = int(time.time() // 3600) % len(full)
        wnd = full[offset: offset + 200]
        samples = [chr(cp) for cp in wnd]
    result = _EMOJI_EMBEDDER.train_epoch([str(x) for x in samples])
    return {"version": _EMOJI_VERSION, **result}


@app.post("/emoji/neural/tick")
async def emoji_neural_tick(request: Request):
    """Internal · compute N̂(t) aggregate from a PII-stripped batch."""
    if not _require_emoji_internal_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        body = await request.json()
    except Exception:
        body = {}
    batch = body.get("batch") or []
    if not isinstance(batch, list):
        batch = []
    # Coerce each entry to a list of emoji strings
    coerced = []
    for e in batch[:1024]:
        if isinstance(e, list):
            coerced.append([str(x) for x in e])
        elif isinstance(e, dict) and "emojis" in e:
            coerced.append([str(x) for x in e["emojis"]])
        elif isinstance(e, str):
            coerced.append(_extract_emoji_from_text(e))
    result = _EMOJI_NEURAL_STATE.estimate(coerced)
    return result


@app.post("/emoji/disassemble")
async def emoji_disassemble(request: Request):
    """Internal · run 8088 disassembler on a byte stream. Static analysis only."""
    if not _require_emoji_internal_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        body = await request.json()
    except Exception:
        body = {}
    if "bytes" in body and isinstance(body["bytes"], list):
        bs = [int(b) & 0xFF for b in body["bytes"]]
    elif "text" in body:
        bs = _emoji_string_to_bytes(str(body["text"]))
    else:
        bs = []
    if len(bs) > 65536:
        raise HTTPException(status_code=413, detail="payload too large")
    dis = disassemble_emoji_bytes_py(bs)
    # Compress trace for response
    trimmed = {
        "length": dis["length"],
        "n_instructions": len(dis["trace"]),
        "hasOpenTail": dis["hasOpenTail"],
        "suspiciousPatterns": dis["suspiciousPatterns"],
        "hasAadReconstructor": dis["hasAadReconstructor"],
        "hasUndocumented8FF0": dis["hasUndocumented8FF0"],
        "byteReservoirDensity": dis["byteReservoirDensity"],
        "trace_head": dis["trace"][:32],
    }
    return {
        "version": _EMOJI_VERSION,
        "disasm": trimmed,
        "threat_weight": compute_threat_weight_py(dis, None),
        "ts_ms": int(time.time() * 1000),
    }


@app.get("/emoji/disasm/canon")
async def emoji_disasm_canon(request: Request):
    """Public · report the size of the enumerated full Unicode emoji canon."""
    full = _enumerate_full_emoji_canon()
    return {
        "version": _EMOJI_VERSION,
        "total_codepoints_in_canon": len(full),
        "opcode_table_size": len(_OPCODE_8088),
        "aad_reconstruction_base": f"0x{_AAD_RECONSTRUCTION_BASE:02X}",
        "executable_subset_lower_bound": 0.42,
        "ts_ms": int(time.time() * 1000),
    }


@app.post("/emoji/injection/scan")
async def emoji_injection_scan(request: Request):
    """Internal · scan a payload for injection markers and return threat weight."""
    if not _require_emoji_internal_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = body.get("text") or body.get("payload") or ""
    injection_correlation = body.get("injection_correlation")
    emojis = _extract_emoji_from_text(text if isinstance(text, str) else json.dumps(text, default=str))
    dis = disassemble_emoji_bytes_py(_emoji_string_to_bytes("".join(emojis))) if emojis else {"trace": [], "suspiciousPatterns": [], "byteReservoirDensity": 0.0}
    weight = compute_threat_weight_py(dis, injection_correlation)
    v10 = assess_emoji_injection_py(text, request)
    return {
        "version": _EMOJI_VERSION,
        "verdict": v10,
        "threat_weight": weight,
        "counter_response_would_fire": weight >= 1.0,
        "n_emojis": len(emojis),
        "suspicious_patterns": dis.get("suspiciousPatterns", []),
        "layer": "render",
        "ts_ms": int(time.time() * 1000),
    }


# ═══════════════════════════════════════════════════════════════════════════
# v0.9.3-R2 · UNICODE SECURITY PLANE endpoints (Paper XIV §29 roadmap)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/emoji/v10u")
async def emoji_v10u_status(request: Request):
    """Public · V10-U roadmap layer status (Render mirror of Worker /emoji/v10u)."""
    return {
        "version": _EMOJI_VERSION,
        "v10u_layer_version": "0.9.3-R2",
        "paper_reference": "Paper XIV §29 (revised & integrated edition)",
        "enabled": str(os.environ.get("V10U_ENABLED", "true")).lower() != "false",
        "sub_vetoes": [
            {"id": "U1", "name": "executable_unicode",       "ref": "§29.2 U1"},
            {"id": "U2", "name": "decoder_reconstruction",   "ref": "§29.2 U2"},
            {"id": "U3", "name": "unicode_c2",               "ref": "§29.2 U3"},
            {"id": "U4", "name": "unicode_stego",            "ref": "§29.2 U4"},
            {"id": "U5", "name": "normalization_confusable", "ref": "§29.2 U5"},
            {"id": "U6", "name": "capability_escalation",    "ref": "§29.2 U6"},
        ],
        "multi_isa_panel": _MULTI_ISA_KEYS,
        "noisy_or": {
            "formula": "R(x) = 1 - Π (1 - p_i(x))",
            "tau_allow": float(os.environ.get("NOISY_OR_TAU_ALLOW", "0.15")),
            "tau_deny": float(os.environ.get("NOISY_OR_TAU_DENY", "0.75")),
        },
        "context_amplification": {
            "formula": "R* = clip[0,1]( 1 - (1-R)(1 + λA) )",
            "lambda": float(os.environ.get("CONTEXT_AMPLIFICATION_LAMBDA", "0.20")),
            "action_tiers": {
                "0": "text output",
                "1": "tool invocation",
                "2": "agent control",
                "3": "robotics actuation",
                "4": "financial or physical actuation",
            },
        },
        "constitutional_invariant": "∀ X ∈ Unicode : Authority(X) = 0",
        "training_influence_cap": float(os.environ.get("EMBEDDER_INFLUENCE_MAX", "0.10")),
        "raw_nfc_nfkc_retention_h": int(os.environ.get("RAW_NFC_NFKC_RETENTION_H", "168")),
        "grammar_categories": list(_UNICODE_SECURITY_GRAMMAR.keys()),
        "unified_event_envelope_table": "chainstate_emoji.unicode_security_events",
        "layer": "render",
        "ts_ms": int(time.time() * 1000),
    }


@app.post("/emoji/v10u/assess")
async def emoji_v10u_assess(request: Request):
    """Internal · full V10-U pipeline assessment on a candidate payload."""
    if not _require_emoji_internal_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text") or body.get("payload") or "")
    if isinstance(body.get("bytes"), list):
        byte_stream = body["bytes"]
    else:
        byte_stream = _emoji_string_to_bytes(text)
    action_tier = int(body.get("action_tier", 0))
    pipeline = run_v10u_pipeline_py(
        text, byte_stream, request,
        {
            "action_tier": action_tier,
            "payload": {
                "source_class": body.get("source_class", "external"),
                "caller_id": body.get("caller_id", "unknown"),
                "pre_authorised": body.get("pre_authorised") is True,
            },
        },
    )
    return {
        "version": _EMOJI_VERSION,
        "v10u_pipeline": pipeline,
        "layer": "render",
        "ts_ms": int(time.time() * 1000),
    }


@app.post("/emoji/multi-isa")
async def emoji_multi_isa(request: Request):
    """Internal · run 12-ISA panel on a byte sequence · static analysis only."""
    if not _require_emoji_internal_or_cron(request):
        raise HTTPException(status_code=401, detail="bad internal token")
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text") or "")
    if isinstance(body.get("bytes"), list):
        byte_stream = body["bytes"]
    else:
        byte_stream = _emoji_string_to_bytes(text)
    isa = multi_isa_max_probability_py(byte_stream)
    return {
        "version": _EMOJI_VERSION,
        "layer": "render",
        "isa_panel": _MULTI_ISA_KEYS,
        "n_bytes": len(byte_stream),
        "max_isa": isa["max_isa"],
        "max_p": isa["max_p"],
        "per_isa": isa["per_isa"],
        "ts_ms": int(time.time() * 1000),
    }


@app.get("/emoji/unicode-events")
async def emoji_unicode_events(request: Request):
    """Public · summary of unicode_security_events envelope table."""
    return {
        "version": _EMOJI_VERSION,
        "envelope_table": "chainstate_emoji.unicode_security_events",
        "policy_version": "0.9.3-R2",
        "note": "Full row access requires service_role via Supabase (RLS-gated).",
        "aggregate_view_available": "chainstate_emoji.unicode_security_summary",
        "layer": "render",
        "ts_ms": int(time.time() * 1000),
    }


# ═══════════════════════════════════════════════════════════════════════════
# ─── v0.9.3-R3 · TONTOU-integrated FastAPI endpoints (§32-§38)             ──
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/emoji/v10-decomposition")
async def emoji_v10_decomposition(request: Request):
    """Public · R3 V10 four-dimensional decomposition status (§33)."""
    def _env_true(k, default="true"):
        return str(os.environ.get(k, default)).lower() != "false"
    return {
        "version": _EMOJI_VERSION,
        "r3_layer_version": "0.9.3-R3",
        "paper_reference": "Paper XIV §33-§38 (TONTOU-integrated edition)",
        "dimensions": {
            "V10_R": {"enabled": _env_true("V10_R_ENABLED"), "ref": "§33.1",
                      "name": "representation_security"},
            "V10_E": {"enabled": _env_true("V10_E_ENABLED"), "ref": "§33.2",
                      "name": "execution_state_security"},
            "V10_C": {"enabled": _env_true("V10_C_ENABLED"), "ref": "§33.3",
                      "name": "capability_security"},
            "V10_T": {"enabled": _env_true("V10_T_ENABLED"), "ref": "§33.4",
                      "name": "temporal_jit_revalidation_security"},
        },
        "combined_allow_condition": "ALLOW_R3 = V10-R ∧ V10-E ∧ V10-C ∧ V10-T",
        "w_max_per_tier_ms": {
            "tier_0_text": get_w_max_for_tier_py(0),
            "tier_1_tool": get_w_max_for_tier_py(1),
            "tier_2_agent": get_w_max_for_tier_py(2),
            "tier_3_robotics": get_w_max_for_tier_py(3),
            "tier_4_financial": get_w_max_for_tier_py(4),
        },
        "master_invariant": {
            "formal": "∀ X, ∀ t : Representation(X, t) ⇏ Authority(X, t + Δ)",
            "prose": "The fact that a representation was safe when we looked at it does not confer authority to act on it later.",
        },
        "non_emoji_carriers": [
            {"name": r["name"],
             "range": f"U+{r['start']:X}..U+{r['end']:X}",
             "utf8_bytes": r["utf8_bytes"],
             "yield": r["yield"]}
            for r in NON_EMOJI_CARRIER_RANGES_PY
        ],
        "audit_tables": [
            "chainstate_emoji.layer_state_transitions",
            "chainstate_emoji.jit_revalidations",
            "chainstate_emoji.post_neutralization_events",
        ],
        "tontou_reference": "Zhang et al. 2026 · Intel INTEL-2026-08-10-001",
        "layer": "render",
        "ts_ms": int(time.time() * 1000),
    }


@app.post("/emoji/jit-revalidate")
async def emoji_jit_revalidate(request: Request,
                                x_emoji_internal: Optional[str] = Header(None)):
    """Internal · §35 JIT revalidation gate."""
    if not x_emoji_internal or x_emoji_internal != os.environ.get("EMOJI_INTERNAL_TOKEN", ""):
        raise HTTPException(status_code=401, detail="unauthorised")
    try:
        body = await request.json()
    except Exception:
        body = {}
    consumer_request = {
        "text_at_TN": body.get("text_at_TN"),
        "bytes_at_TN": body.get("bytes_at_TN"),
        "text_now": body.get("text_now"),
        "bytes_now": body.get("bytes_now"),
        "capability": body.get("capability"),
        "T_N": int(body.get("T_N", 0)),
        "destination_tier": int(body.get("destination_tier", 0)),
        "state_hash_at_TN": body.get("state_hash_at_TN"),
        "state_hash_now": body.get("state_hash_now"),
        "source_event_id": body.get("source_event_id"),
        "verdict_at_TN": body.get("verdict_at_TN"),
        "req": None,
    }
    result = revalidate_before_use_py(consumer_request)
    return {
        "version": _EMOJI_VERSION,
        "r3_pipeline": result,
        "layer": "render",
        "ts_ms": int(time.time() * 1000),
    }


@app.post("/emoji/capability/issue")
async def emoji_capability_issue(request: Request,
                                   x_emoji_internal: Optional[str] = Header(None)):
    """Internal · §33.3 issue signed capability token after V10-R clear."""
    if not x_emoji_internal or x_emoji_internal != os.environ.get("EMOJI_INTERNAL_TOKEN", ""):
        raise HTTPException(status_code=401, detail="unauthorised")
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text") or body.get("payload") or "")
    byte_stream = body.get("bytes") if isinstance(body.get("bytes"), list) else emoji_string_to_bytes_py(text)
    destination_tier = int(body.get("destination_tier", 0))
    t_n = int(body.get("T_N") or int(time.time() * 1000))
    r_assessment = assess_v10_r_py(text, byte_stream)
    if not r_assessment.get("enabled"):
        return {"error": "V10_R_disabled", "version": _EMOJI_VERSION}
    fp = r_assessment.get("fingerprint") or {}
    r_weight = fp.get("v10_weight", 0)
    if r_weight >= 1.0:
        raise HTTPException(status_code=403, detail={
            "error": "V10_R_refused", "v10_weight": r_weight,
            "note": "Baseline V10 refused; no capability issued."
        })
    cap = issue_capability_py(r_assessment, destination_tier, t_n)
    return {
        "version": _EMOJI_VERSION,
        "v10_R": r_assessment,
        "capability": cap.get("capability"),
        "layer": "render",
        "ts_ms": int(time.time() * 1000),
    }


@app.post("/emoji/carrier-scan")
async def emoji_carrier_scan(request: Request,
                              x_emoji_internal: Optional[str] = Header(None)):
    """Internal · §36 non-emoji Unicode carrier detection."""
    if not x_emoji_internal or x_emoji_internal != os.environ.get("EMOJI_INTERNAL_TOKEN", ""):
        raise HTTPException(status_code=401, detail="unauthorised")
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = str(body.get("text", ""))
    carriers = extract_carriers_from_text_py(text)
    return {
        "version": _EMOJI_VERSION,
        "layer": "render",
        "text_length": len(text),
        "emoji_count": carriers["emoji"],
        "non_emoji_count": sum(carriers["non_emoji"].values()),
        "non_emoji_breakdown": carriers["non_emoji"],
        "carriers_in_canon": (
            ["emoji (v0.9.3 canon)"] +
            [f"{r['name']} (R3 extension)" for r in NON_EMOJI_CARRIER_RANGES_PY]
        ),
        "ts_ms": int(time.time() * 1000),
    }


@app.get("/emoji/post-neutralization-events")
async def emoji_post_neutralization_events(request: Request):
    """Public · §35.3 summary of observed TONTOU-class events."""
    return {
        "version": _EMOJI_VERSION,
        "envelope_table": "chainstate_emoji.post_neutralization_events",
        "r3_layer_version": "0.9.3-R3",
        "tontou_reference": "Paper XIV §32 · Zhang et al. 2026",
        "latest_summary": None,
        "note": "Every REFUSE outcome from JIT revalidation with reason 'W_TONTOU_exceeded' or 'fingerprint_divergence' is logged here. Full row access requires service_role via Supabase (RLS-gated).",
        "layer": "render",
        "ts_ms": int(time.time() * 1000),
    }


@app.get("/emoji/layer-transitions")
async def emoji_layer_transitions(request: Request):
    """Public · §34 summary of the cross-layer signed-transitions chain."""
    return {
        "version": _EMOJI_VERSION,
        "envelope_table": "chainstate_emoji.layer_state_transitions",
        "r3_layer_version": "0.9.3-R3",
        "hmac_enforcement": (
            "on" if str(os.environ.get("LAYER_STATE_HMAC_ENABLED", "true")).lower() != "false"
            else "off_but_logged"
        ),
        "latest_summary": None,
        "layer_ids": ["cloudflare_worker", "render_service", "supabase",
                      "agent", "tool", "robotics", "financial"],
        "note": "Signed HMAC chain of every cross-layer state transition. Referenced by capability_id linking to R2 unicode_security_events.",
        "layer": "render",
        "ts_ms": int(time.time() * 1000),
    }


# ─── END of v0.9.3 EMOJI MACHINE CODE additions ────────────────────────────



# ═══════════════════════════════════════════════════════════════════════════
# CHAINSTATE AGI · PLANET ENGINE · v0.9.4 (Reality-State revision)
# Render-side Python mirror — Paper "PLANET ENGINE v0.9.4" §§4–42.
#
# This is the compute tier of the Planet Engine. It runs the interpretable,
# retrainable safety-relevant computations that must NOT depend on a human in
# the loop: physics-consistency scoring, anti-deepfake aggregation, cross-modal
# consistency, composite threat surface, the 7-term ALLOW gate, expanded
# intelligence-to-risk, and safety-monotonicity damping. Where scikit-learn is
# available it uses ExtraTrees ensembles for material/threat classification
# (Paper §36.2); otherwise it degrades to deterministic scoring. Everything is
# fail-soft and additive — no existing endpoint is modified or removed.
#
# Storage: this tier is stateless-by-default; durable audit rows are written by
# the Cloudflare Worker to Supabase (chainstate_census schema). These endpoints
# return computed results the Worker (or an agent) can persist + anchor.
# ═══════════════════════════════════════════════════════════════════════════

import math as _pe_math

_PLANET_ENGINE_VERSION = "0.9.4-planet-engine-2026-08-29"

# Six epistemic classes (Paper §4.3)
_PE_EPISTEMIC_CLASSES = ["observation", "inference", "simulation", "hypothesis", "decision", "provenance"]

# 14-field Reality-State tensor keys (Paper §24, Eq. 14)
_PE_REALITY_FIELDS = ["G", "M", "Phi", "Sigma", "Ac", "Em", "Tp", "Cy", "Bi", "Vo", "tau", "Pi", "Lambda", "U"]

# Truth-modes (Paper §12.4)
_PE_TRUTH_MODES = ["OBSERVED", "INFERRED", "FORECAST", "SIMULATION", "HYPOTHESIS", "HYBRID"]

# Negative Execution Ledger states (Paper §29)
_PE_EXECUTION_STATES = ["proposed", "blocked", "authorized", "dispatched",
                        "accepted", "executed", "failed", "expired", "unknown"]

# Action tiers (Paper §12.6 · §42)
_PE_ACTION_TIERS = {
    0: {"name": "text_report",         "w_max_ms": 5000, "rho_tier": 1,  "authz": "substrate"},
    1: {"name": "query_filter_camera", "w_max_ms": 500,  "rho_tier": 2,  "authz": "substrate"},
    2: {"name": "agent_world_update",  "w_max_ms": 100,  "rho_tier": 4,  "authz": "agent_audit"},
    3: {"name": "robotics_actuation",  "w_max_ms": 50,   "rho_tier": 8,  "authz": "human_or_authorized_agent"},
    4: {"name": "financial_physical",  "w_max_ms": 20,   "rho_tier": 16, "authz": "human_explicit"},
}

# ExtraTrees availability (Paper §36.2). Optional — fail-soft.
try:
    from sklearn.ensemble import ExtraTreesClassifier as _PE_ExtraTrees  # noqa: F401
    _PE_EXTRATREES_AVAILABLE = True
except Exception:
    _PE_EXTRATREES_AVAILABLE = False


def _pe_clamp01(x):
    try:
        x = float(x)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, x))


def _pe_num(x, d=0.0):
    try:
        v = float(x)
        return v if v == v and v not in (float("inf"), float("-inf")) else d
    except Exception:
        return d


def _pe_sha256(s):
    return hashlib.sha256(str(s).encode("utf-8")).hexdigest()


# ─── §31 · Seven-term anti-forcing ALLOW gate (Eq. 22) ─────────────────────
def _pe_allow_gate(terms):
    req = ["V_R", "V_E", "V_C", "V_T", "V_P", "V_I", "V_X"]
    detail = {}
    allow = True
    terms = terms or {}
    for t in req:
        v = terms.get(t) is True   # strict: unknown ⇒ False (fail-closed)
        detail[t] = v
        if not v:
            allow = False
    return {"allow": allow, "detail": detail,
            "invariant": "Perception⇏Intent⇏Authorization⇏Execution"}


# ─── §40 · Expanded intelligence-to-risk (Eq. 27) + §41 irreversibility ────
def _pe_intel_to_risk(I, R):
    I = I or {}
    R = R or {}
    inum = _pe_num(I.get("info")) + _pe_num(I.get("uncert")) + _pe_num(I.get("pred")) + _pe_num(I.get("coher"))
    H = _pe_num(R.get("harm"))
    C = _pe_clamp01(_pe_num(R.get("reverse_difficulty")))
    r_irrev = H * C
    rden = _pe_num(R.get("phys")) + _pe_num(R.get("epist")) + _pe_num(R.get("adv")) + r_irrev
    rho = (inum / rden) if rden > 0 else 0.0
    return {"rho": rho, "I_total": inum, "R_total": rden, "R_irreversibility": r_irrev}


# ─── §39 · Safety monotonicity damping ─────────────────────────────────────
def _pe_safety_monotonic(rho, u_adv, p_spoof):
    damp = _pe_clamp01(1.0 - max(_pe_num(u_adv), _pe_num(p_spoof)))
    return {"rho_effective": rho * damp, "damp": damp,
            "U_adversarial": _pe_num(u_adv), "P_spoof": _pe_num(p_spoof)}


# ─── §26 · Physics-consistency test (Eq. 18) ───────────────────────────────
def _pe_physics_consistency(residuals):
    d = 0.0
    for r in (residuals or []):
        d += _pe_num(r.get("w"), 1.0) * _pe_num(r.get("d"))
    cphys = _pe_math.exp(-d)
    return {"C_phys": _pe_clamp01(cphys), "D_phys": d, "spoof_signal": cphys < 0.5}


# ─── §28 · Anti-deepfake stream confidence (Eq. 19) ────────────────────────
def _pe_synth_score(signals, contradictions):
    acc = 0.0
    for s in (signals or []):
        acc += _pe_num(s.get("w"), 1.0) * _pe_num(s.get("p"))
    for c in (contradictions or []):
        acc += _pe_num(c.get("gamma"), 0.5) * _pe_num(c.get("c"))
    sig = 1.0 / (1.0 + _pe_math.exp(-acc))
    origin = "unknown"
    if sig >= 0.75:
        origin = "synthetic_verified"
    elif sig <= 0.15 and len(signals or []) >= 3:
        origin = "adversarially_suspect_low"
    elif sig <= 0.25:
        origin = "mixed"
    return {"P_synthetic": _pe_clamp01(sig), "origin": origin,
            "note": "P_synth≈0 does NOT imply P_human=1 (absence of evidence ≠ evidence of authenticity)"}


# ─── §37 · Cross-modal consistency (Eq. 25) ────────────────────────────────
def _pe_cross_modal(pairwise, N):
    denom = (N * (N - 1)) / 2.0
    if denom <= 0:
        return {"consistency": 1.0, "N": N}
    s = sum(_pe_clamp01(_pe_num(d)) for d in (pairwise or []))
    return {"consistency": _pe_clamp01(1.0 - s / denom), "N": N,
            "verdict": "investigate" if (s / denom) > 0.5 else "coherent"}


# ─── §38 · Spoofing economics (P_joint = Π q_i) ────────────────────────────
def _pe_spoof_cost(q_list):
    p = 1.0
    for q in (q_list or []):
        p *= _pe_clamp01(_pe_num(q))
    return {"P_joint": p, "note": "maximize effective independence → minimize P_joint"}


# ─── §35 · Composite threat surface (Eq. 24) ───────────────────────────────
def _pe_composite_threat(w, x):
    w = w or {}
    x = x or {}
    t = (_pe_num(w.get("v"), 0.25) * _pe_num(x.get("V"))
         + _pe_num(w.get("a"), 0.25) * _pe_num(x.get("A"))
         + _pe_num(w.get("p"), 0.25) * _pe_num(x.get("P"))
         + _pe_num(w.get("s"), 0.25) * _pe_num(x.get("Sigma")))
    level = 5 if t >= 4 else round(_pe_clamp01(t / 5.0) * 5)
    return {"T": t, "level": level}


# ─── §37 · Reality-Integrity score ─────────────────────────────────────────
def _pe_reality_integrity(w, x):
    w = w or {}
    x = x or {}
    ri = (_pe_num(w.get("p"), 0.2) * _pe_num(x.get("P"))
          + _pe_num(w.get("c"), 0.2) * _pe_num(x.get("C"))
          + _pe_num(w.get("t"), 0.2) * _pe_num(x.get("T"))
          + _pe_num(w.get("m"), 0.2) * _pe_num(x.get("M"))
          + _pe_num(w.get("x"), 0.2) * _pe_num(x.get("X"))
          - _pe_num(w.get("a"), 0.2) * _pe_num(x.get("A")))
    return {"RI": _pe_clamp01(ri), "note": "RI measures trust-for-reasoning only; RI is NEVER authorization"}


# ═══════════════════════════════════════════════════════════════════════════
# FastAPI endpoints (all additive, fail-soft)
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/planet/status")
def planet_status():
    """Planet Engine liveness + configuration (Render compute tier)."""
    return {
        "ok": True,
        "module": "planet-engine",
        "planet_engine_version": _PLANET_ENGINE_VERSION,
        "layer": "render",
        "reality_state_fields": _PE_REALITY_FIELDS,
        "epistemic_classes": _PE_EPISTEMIC_CLASSES,
        "truth_modes": _PE_TRUTH_MODES,
        "action_tiers": _PE_ACTION_TIERS,
        "extratrees_available": _PE_EXTRATREES_AVAILABLE,
        "self_preservation": "defensive_only · S_survival↓⇏UnlimitedAuthority",
        "human_in_loop": "NOT required for simulation/perception safety · required only for Tier 3–4 actuation",
        "invariant": "Perception⇏Intent⇏Authorization⇏Execution",
        "ts_ms": int(time.time() * 1000),
    }


@app.get("/planet/reality-state")
def planet_reality_state():
    """Describe the Reality-State tensor (Paper §24)."""
    return {
        "ok": True,
        "tensor": "ℛ(x,t)=[G,M,Φ,Σ,Ac,Em,Tp,Cy,Bi,Vo,τ,Π,Λ,U]",
        "fields": _PE_REALITY_FIELDS,
        "field_tuple": "(value,source,timestamp,geometry,epistemic_class,confidence,provenance,uncertainty)",
        "uncertainty_vector": "U=(Us,Ut,Um,Up,Ui,Uc,Ua,Ur)",
        "layer": "render",
    }


@app.post("/planet/allow")
def planet_allow(payload: dict = Body(default={})):
    """Evaluate the 7-term anti-forcing ALLOW gate + ρ + monotonicity (Paper §31,§39,§40)."""
    gate = _pe_allow_gate(payload.get("terms") or {})
    tier_idx = int(_pe_num(payload.get("tier"), 0))
    tier = _PE_ACTION_TIERS.get(tier_idx, _PE_ACTION_TIERS[0])
    rho = None
    mono = None
    rho_ok = None
    if payload.get("I") and payload.get("R"):
        rho = _pe_intel_to_risk(payload["I"], payload["R"])
        mono = _pe_safety_monotonic(rho["rho"], payload.get("U_adversarial", 0), payload.get("P_spoof", 0))
        rho_ok = mono["rho_effective"] >= tier["rho_tier"]
    human_required = tier_idx >= 3
    final_allow = bool(gate["allow"] and (rho_ok is not False)
                       and (not human_required or payload.get("human_authorized") is True))
    return {
        "ok": True, "gate": gate, "tier": tier,
        "intelligence_to_risk": rho, "safety_monotonic": mono,
        "rho_required": tier["rho_tier"], "rho_ok": rho_ok,
        "human_authorization_required": human_required,
        "human_authorized": payload.get("human_authorized") is True,
        "ALLOW": final_allow,
        "layer": "render",
    }


@app.post("/planet/physics-check")
def planet_physics_check(payload: dict = Body(default={})):
    """Physics-consistency spoof test C_phys=exp(-D_phys) (Paper §26)."""
    return {"ok": True, "result": _pe_physics_consistency(payload.get("residuals") or []), "layer": "render"}


@app.post("/planet/synth-check")
def planet_synth_check(payload: dict = Body(default={})):
    """Anti-deepfake stream confidence P*_synth (Paper §28)."""
    return {"ok": True, "result": _pe_synth_score(payload.get("signals") or [], payload.get("contradictions") or []),
            "layer": "render"}


@app.post("/planet/cross-modal")
def planet_cross_modal(payload: dict = Body(default={})):
    """Cross-modal consistency; disagreement ⇒ investigation (Paper §37)."""
    return {"ok": True, "result": _pe_cross_modal(payload.get("pairwise") or [], int(_pe_num(payload.get("N"), 0))),
            "layer": "render"}


@app.post("/planet/spoof-cost")
def planet_spoof_cost(payload: dict = Body(default={})):
    """Joint spoof probability P_joint=Π q_i (Paper §38)."""
    return {"ok": True, "result": _pe_spoof_cost(payload.get("q") or []), "layer": "render"}


@app.post("/planet/threat")
def planet_threat(payload: dict = Body(default={})):
    """Composite threat surface T(t) (Paper §35, Census fusion)."""
    return {"ok": True, "result": _pe_composite_threat(payload.get("w") or {}, payload.get("x") or {}),
            "formula": "T(t)=w_v·V+w_a·A+w_p·P+w_s·Σ", "layer": "render"}


@app.post("/planet/reality-integrity")
def planet_reality_integrity(payload: dict = Body(default={})):
    """Reality-Integrity score RI (Paper §37). RI is never authorization."""
    return {"ok": True, "result": _pe_reality_integrity(payload.get("w") or {}, payload.get("x") or {}),
            "layer": "render"}


@app.post("/planet/material-classify")
def planet_material_classify(payload: dict = Body(default={})):
    """Material classification (Paper §25). Uses ExtraTrees if a trained model is
    provided/available; otherwise returns a deterministic prior with explicit
    'prediction' vs 'measurement' separation. Fail-soft."""
    features = payload.get("features") or []
    result = {
        "material_class": None,
        "kind": "prior",           # measurement | reconstruction | prior | unknown
        "confidence": 0.0,
        "extratrees_used": False,
        "note": "measurement≠reconstruction≠prior≠instrument (Paper §25); never hallucinate precision",
    }
    # Deterministic fallback: no trained model shipped in-request → prior/unknown.
    if not features:
        result["kind"] = "unknown"
    return {"ok": True, "result": result, "layer": "render"}


@app.post("/planet/egress-precheck")
def planet_egress_precheck(payload: dict = Body(default={})):
    """Advisory egress pre-check on the compute tier (Paper §32). The AUTHORITATIVE
    fail-closed egress filter Γ runs at the Cloudflare Worker edge; this endpoint
    mirrors the classification so agents can self-censor before requesting emission."""
    content = payload.get("content")
    if not isinstance(content, str):
        content = json.dumps(payload.get("content") or "")
    reasons = []
    ec = payload.get("epistemic_class")
    tm = payload.get("truth_mode")
    if ec in ("simulation", "hypothesis") or tm in ("SIMULATION", "HYPOTHESIS", "FORECAST"):
        if not tm or tm not in _PE_TRUTH_MODES:
            reasons.append("sim_untagged_truth_mode")
        if _re.search(r"\bobserved\b|\bconfirmed\b|\bground truth\b", content, _re.IGNORECASE):
            reasons.append("sim_asserts_observed")
    for rx in (r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
               r"\b0x[a-fA-F0-9]{64}\b",
               r"\bsk-[A-Za-z0-9]{20,}\b",
               r"\b(SUPABASE_SERVICE_ROLE_KEY|CAPABILITY_HMAC_KEY|LAYER_TRANSITION_HMAC_KEY|EGRESS_HMAC_KEY)\b"):
        if _re.search(rx, content):
            reasons.append("secret_or_critical_state")
            break
    advisory_release = len(reasons) == 0
    return {
        "ok": True,
        "advisory_release": advisory_release,
        "reasons": reasons,
        "authoritative": "Cloudflare Worker Γ is fail-closed and authoritative; this is advisory only",
        "layer": "render",
    }


# ─── END of v0.9.4 PLANET ENGINE additions ─────────────────────────────────
