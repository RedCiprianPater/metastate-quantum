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
"""
import os
import math
from fastapi import FastAPI, Header, HTTPException
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
            "census_scheduler_running": _is_census_scheduler_running()}

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
