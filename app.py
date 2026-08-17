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
"""
import os
import math
from fastapi import FastAPI, Header, HTTPException, Body
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

app = FastAPI(title="METASTATE Quantum Worker", version="1.2.0-osaka")

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
            "robotics_scheduler_running": _is_robotics_scheduler_running()}

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
