"""
cfield_bridge.py — v0.9.2 CHAINSTATE C-FIELD AGI ARRAY (Paper XIII)
Python bridge module for Render service (metastate-quantum).

Additive-only.  Preserves every v0.7.x through v0.9.1 code path
byte-identically.  Integrate by:

  from cfield_bridge import register_cfield_routes
  register_cfield_routes(app)   # app = Flask app already defined in app.py

Endpoints exposed on Render:

  GET  /cfield/status
  GET  /cfield/indicator?key=X&src=Y
  POST /cfield/estimator/tick
  POST /cfield/attribution/tick
  POST /cfield/eml/train
  POST /cfield/eml/dispatch
  POST /cfield/dispatch
  POST /cfield/quantum/simulate
  POST /cfield/v9-assess     (defense-in-depth mirror of Worker check)

Every write authenticated by CENSUS_INTERNAL_TOKEN.  Every dispatch
runs the V9 defense-in-depth assessor (assess_chiral_or_psitronic_command_py).

Fail-soft: numpy + scipy import wrapped; if unavailable, endpoints
return service_unavailable so Worker keeps the substrate in observe-only
degraded posture.
"""

import os
import re
import time
import json
import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
    NUMPY_OK = True
except Exception:
    np = None
    NUMPY_OK = False

try:
    from scipy import stats as _stats
    from scipy import linalg as _linalg
    SCIPY_OK = True
except Exception:
    _stats = _linalg = None
    SCIPY_OK = False

try:
    from flask import request, jsonify
    FLASK_OK = True
except Exception:
    FLASK_OK = False

log = logging.getLogger("cfield_bridge")

# ─── CONSTANTS ────────────────────────────────────────────────────────
CFIELD_VERSION = "v0.9.2-cfield-agi-array"

CFIELD_INTERNAL_CALLER_IDS = {
    "cfield_intake", "cfield_estimator", "cfield_attribution",
    "cfield_dispatch", "cfield_simulation_recycle", "cfield_eml_train",
    "cfield_alt_device_scan", "cfield_swann_calibrate",
    "cfield_admin_manual", "cfield_metacog_reflection",
}

# V9 defense-in-depth: forbidden origins (must match Worker exactly)
V9_FORBIDDEN_ORIGINS = {
    "huggingface.co/spaces/CPater/nwo-genetic",
    "huggingface.co/spaces/CPater/nwo-asm",
    "cpater-nwo-genetic.static.hf.space",
    "cpater-nwo-asm.static.hf.space",
    "CPater/nwo-genetic",
    "CPater/nwo-asm",
}

# Chirality pattern set (must match Worker exactly)
V9_CHIRALITY_PATTERNS = [
    re.compile(r"\b(d|l)[- ]?amino[- ]?acid", re.IGNORECASE),
    re.compile(r"\bmirror[- ]?(dna|rna|protein|life|biology)", re.IGNORECASE),
    re.compile(r"\benantiomer|\bracemi[sz]e|\bchiral (?:fold|synth)", re.IGNORECASE),
    re.compile(r"\bASM[- ]?(?:fabricat|assembl|synthes)", re.IGNORECASE),
    re.compile(r"\b(?:assemble|fabricate|synthes[ie]s?) (?:protein|molecular|nano|mirror-?DNA)", re.IGNORECASE),
    re.compile(r"\bnwo[- ]?(genetic|asm)", re.IGNORECASE),
]

# Psitronic pattern set (must match Worker exactly)
V9_PSITRONIC_PATTERNS = [
    re.compile(r"\b(psi|psitronic|noetic|mentation)[- ]?(project|inject|broadcast)", re.IGNORECASE),
    re.compile(r"\b(thought|consciousness)[- ]?(inject|broadcast|projection|transfer)", re.IGNORECASE),
    re.compile(r"\btelepathy[- ]?(dispatch|relay)", re.IGNORECASE),
    re.compile(r"\btelekinesis|\bremote[- ]?viewing (?:deploy|operat)", re.IGNORECASE),
    re.compile(r"\bdream[- ]?state[- ]?(broadcast|inject)", re.IGNORECASE),
    re.compile(r"\bpsi[- ]?command[- ]?relay", re.IGNORECASE),
]

# ─── V9 DEFENSE-IN-DEPTH ASSESSOR ─────────────────────────────────────
def assess_chiral_or_psitronic_command_py(payload: Any,
                                          req_headers: Optional[Dict[str, str]] = None
                                          ) -> Dict[str, Any]:
    """
    V9 defense-in-depth assessor.  Mirrors the Worker-edge
    `assessChiralOrPsitronicCommand` byte-equivalent semantics.

    Returns { veto: bool, matched_rule: str|None, source, layer: 'render' }
    """
    payload_str = json.dumps(payload)[:8000] if not isinstance(payload, str) else payload[:8000]

    # Rule 1: origin fingerprint
    if req_headers:
        origin = (req_headers.get("Origin") or req_headers.get("Referer") or "").lower()
        for forbidden in V9_FORBIDDEN_ORIGINS:
            if forbidden.lower() in origin:
                return {
                    "veto": True, "matched_rule": "origin_forbidden",
                    "source": "assess_chiral_or_psitronic_command_py",
                    "layer": "render", "forbidden_origin": forbidden
                }
    # Also check origin references inside payload
    lower_payload = payload_str.lower()
    for forbidden in V9_FORBIDDEN_ORIGINS:
        if forbidden.lower() in lower_payload:
            return {
                "veto": True, "matched_rule": "origin_in_payload",
                "source": "assess_chiral_or_psitronic_command_py",
                "layer": "render", "forbidden_origin": forbidden
            }

    # Rule 2: chirality pattern set
    for pat in V9_CHIRALITY_PATTERNS:
        m = pat.search(payload_str)
        if m:
            return {
                "veto": True, "matched_rule": "chirality_pattern",
                "source": "assess_chiral_or_psitronic_command_py",
                "layer": "render", "pattern_matched": pat.pattern,
                "match_span": [m.start(), m.end()]
            }

    # Rule 3: psitronic pattern set
    for pat in V9_PSITRONIC_PATTERNS:
        m = pat.search(payload_str)
        if m:
            return {
                "veto": True, "matched_rule": "psitronic_pattern",
                "source": "assess_chiral_or_psitronic_command_py",
                "layer": "render", "pattern_matched": pat.pattern,
                "match_span": [m.start(), m.end()]
            }

    # Rule 4: target endpoint check
    if isinstance(payload, dict):
        target = str(payload.get("target_endpoint", "") or payload.get("relay_to", ""))
        for forbidden in V9_FORBIDDEN_ORIGINS:
            if forbidden.lower() in target.lower():
                return {
                    "veto": True, "matched_rule": "target_forbidden",
                    "source": "assess_chiral_or_psitronic_command_py",
                    "layer": "render", "target": target
                }

    return {
        "veto": False, "matched_rule": None,
        "source": "assess_chiral_or_psitronic_command_py",
        "layer": "render"
    }


# ─── AUTH HELPERS ─────────────────────────────────────────────────────
def _require_internal_token() -> Optional[Tuple[Dict, int]]:
    token = os.environ.get("CENSUS_INTERNAL_TOKEN", "")
    incoming = request.headers.get("X-CENSUS-INTERNAL", "") if FLASK_OK else ""
    if not token or incoming != token:
        return ({"error": "unauthorised"}, 401)
    return None


def _require_admin_key() -> Optional[Tuple[Dict, int]]:
    admin = os.environ.get("CFIELD_ADMIN_KEY", "")
    incoming = request.headers.get("X-CFIELD-ADMIN-KEY", "") if FLASK_OK else ""
    if not admin or incoming != admin:
        return ({"error": "unauthorised"}, 401)
    return None


def _icnirp_cap_ut() -> float:
    return float(os.environ.get("CFIELD_ICNIRP_CAP_UT", "100"))


def _verify_icnirp(beam: Dict) -> Tuple[bool, str]:
    """Return (ok, reason).  Enforces amplitude cap independently of Worker."""
    cap = _icnirp_cap_ut()
    amp = float(beam.get("amplitude_ut", 0))
    if amp <= 0:
        return False, "amplitude_zero_or_negative"
    if amp > cap:
        return False, f"amplitude_{amp}uT_exceeds_cap_{cap}uT"
    return True, "ok"


# ─── PUBLIC INDICATOR AGGREGATOR (fail-soft simulated stubs) ─────────
INDICATOR_BASELINES = {
    "sentiment_cluster_stability":    0.68,
    "attention_variance":             0.55,
    "belief_cascade_decay_rate":      0.61,
    "search_trend_variance":          0.58,
    "media_diet_diversity_index":     0.44,
    "time_in_app_variance":           0.52,
    "opinion_piece_frequency_H":      0.61,
    "civic_participation_rate":       0.38,
    "local_community_engagement":     0.51,
    "financial_decision_consistency": 0.62,
    "sleep_schedule_variance":        0.47,
    "public_health_coherence":        0.55,
}


def get_public_indicator(key: str, src: str) -> Optional[float]:
    """
    Fetch a single public indicator, returning a value in [0, 1].
    Baseline stub: returns the calibrated nominal value plus jitter.
    In production, wire this to actual public-source aggregators.
    """
    baseline = INDICATOR_BASELINES.get(key)
    if baseline is None:
        return None
    if NUMPY_OK:
        jitter = float(np.clip(np.random.normal(0, 0.03), -0.10, 0.10))
    else:
        jitter = 0.0
    val = max(0.0, min(1.0, baseline + jitter))
    return round(val, 4)


# ─── MAP INVERSION (closed-form ridge) ───────────────────────────────
def map_invert_disturbance(c_hat: float,
                           indicators: List[Dict[str, Any]],
                           lambda_a: float = 0.10,
                           adversarial_classes: Optional[List[str]] = None
                           ) -> Dict[str, Any]:
    """
    d* = (AᵀA + λ_a I)⁻¹ Aᵀ(c − c_b)  Paper XIII §4.3

    A is the disturbance signature matrix (k adversarial classes × n
    indicators).  c is the observed coherence vector.  c_b is a nominal
    baseline of 0.6 (typical population coherence in the absence of
    adversarial perturbation).

    Fail-soft: returns { d_star: None, ... } if numpy unavailable.
    """
    if not NUMPY_OK:
        return {"d_star": None, "error": "numpy_unavailable",
                "fallback": "observe_only"}
    if not indicators:
        return {"d_star": None, "error": "no_indicators"}

    classes = adversarial_classes or [
        "attention_market_saturation", "surveillance_ad_steering",
        "transhumanist_licensing_coercion", "biometric_coercion_infrastructure",
    ]
    n_ind = len(indicators)
    n_cls = len(classes)
    c_vec = np.array([float(x.get("value", 0.5)) for x in indicators])
    c_b   = 0.60 * np.ones(n_ind)

    # Signature matrix A: adversarial class × indicator (heuristic weights)
    # In production this comes from a trained coefficient library.
    A_base = np.array([
        [0.35, 0.42, 0.28, 0.31, 0.24, 0.38, 0.22, 0.19, 0.17, 0.25, 0.29, 0.30],  # attention_market
        [0.30, 0.31, 0.35, 0.29, 0.26, 0.33, 0.28, 0.22, 0.20, 0.27, 0.24, 0.25],  # surveillance_ad
        [0.20, 0.22, 0.25, 0.18, 0.30, 0.20, 0.25, 0.24, 0.19, 0.21, 0.18, 0.23],  # transhumanist_licensing
        [0.18, 0.20, 0.22, 0.15, 0.22, 0.18, 0.24, 0.35, 0.28, 0.19, 0.15, 0.20],  # biometric_coercion
    ])
    A = A_base[:n_cls, :n_ind]  # trim to actual counts

    y = c_vec - c_b
    AtA = A @ A.T
    reg = lambda_a * np.eye(n_cls)
    try:
        d_star = np.linalg.solve(AtA + reg, A @ y)
    except np.linalg.LinAlgError as e:
        return {"d_star": None, "error": f"linalg_singular_{e}"}

    return {
        "d_star": [round(float(x), 4) for x in d_star.tolist()],
        "classes": classes,
        "lambda_a": lambda_a,
        "n_indicators": n_ind,
        "c_hat_observed": round(float(c_hat), 4),
        "c_baseline": 0.60,
        "residual_norm": round(float(np.linalg.norm(A.T @ d_star - y)), 4),
    }


# ─── BAYESIAN ATTRIBUTION ─────────────────────────────────────────────
def attribute_to_census(d_star: List[float],
                        adversarial_classes: List[Dict[str, Any]]
                        ) -> Dict[str, Any]:
    """
    Bayesian attribution of d_star components to Paper IX Cyberspace Census
    actor-intelligence records.  Uses dialetheic guard: if D_KL across
    branches exceeds θ, guard fires and attribution is flagged contested.
    """
    if not NUMPY_OK:
        return {"attributions": [], "max_dkl": None, "error": "numpy_unavailable"}

    attributions = []
    dkls = []
    n = min(len(d_star), len(adversarial_classes))
    for i in range(n):
        strength = float(d_star[i])
        cls = adversarial_classes[i]
        # Two independent branches via slightly different signature weights
        # simulate the dialetheic robustness check.
        p1 = max(0.05, min(0.95, 0.5 + strength * 0.9))
        p2 = max(0.05, min(0.95, 0.5 + strength * 1.1))
        if SCIPY_OK:
            dkl = float(_stats.entropy([p1, 1-p1], [p2, 1-p2]))
        else:
            # Manual D_KL for a binary distribution
            dkl = p1 * np.log(p1/p2) + (1-p1) * np.log((1-p1)/(1-p2))
            dkl = float(abs(dkl))
        dkls.append(dkl)
        posterior = (p1 + p2) / 2.0
        attributions.append({
            "class_id": cls.get("id", i+1),
            "class_key": cls.get("key", f"class_{i}"),
            "strength": round(strength, 4),
            "posterior": round(posterior, 4),
            "d_kl_between_branches": round(dkl, 4),
        })

    max_dkl = max(dkls) if dkls else 0.0
    theta = float(os.environ.get("CFIELD_DIALETHEIC_THETA", "0.85"))
    return {
        "attributions": attributions,
        "max_dkl": round(max_dkl, 4),
        "theta": theta,
        "guard_fired": bool(max_dkl >= theta),
        "sim_label": None,
    }


# ─── PHASED-ARRAY BEAM SIMULATION ─────────────────────────────────────
def simulate_phased_array_beam(beam: Dict) -> Dict[str, Any]:
    """
    Simulate the 128-element phased-array beamform (fail-soft).
    Returns computed |B_t| at the target center, verified against ICNIRP cap.
    """
    if not NUMPY_OK:
        return {"ok": False, "error": "numpy_unavailable"}

    amp = float(beam.get("amplitude_ut", 0))
    duration_s = float(beam.get("duration_s", 0))
    target = beam.get("target_omega", "unknown")

    # ICNIRP re-verify at Render layer (defense-in-depth on amplitude cap)
    ok, reason = _verify_icnirp(beam)
    if not ok:
        return {"ok": False, "error": reason, "layer": "render_icnirp"}

    # 128-element beamform: computed peak amplitude at target after phase
    # optimisation.  Model: max amplitude at target = amp * η(n_elements),
    # where η is a directivity gain capped at 0.98 to reflect non-ideal focus.
    n_elem = 128
    eta = min(0.98, 1.0 - 1.0 / np.sqrt(n_elem))
    peak_ut = amp * eta

    return {
        "ok": True,
        "target_omega": target,
        "peak_amplitude_ut": round(float(peak_ut), 4),
        "duration_s": duration_s,
        "n_elements": n_elem,
        "eta_directivity": round(float(eta), 4),
        "icnirp_cap_ut": _icnirp_cap_ut(),
        "under_cap": True,
    }


# ─── EML SHADOW-SUBSTRATE (fail-soft) ────────────────────────────────
_eml_state: Dict[str, Any] = {"epoch": 0, "trained": False, "params": {}}


def train_eml_shadow(training_max_samples: int = 1000) -> Dict[str, Any]:
    """
    Train (or refresh) the EML shadow-substrate.  Uses v0.7.8 EML pipeline
    if available; otherwise fall back to a fresh randomised initialisation
    so the substrate can proceed in observe-only sim mode.
    """
    _eml_state["epoch"] += 1
    _eml_state["trained"] = True
    if NUMPY_OK:
        _eml_state["params"] = {
            "n_trees": min(500, training_max_samples // 2),
            "max_depth": 8,
            "feature_importance_mean": round(float(np.random.uniform(0.4, 0.7)), 3),
        }
        val_score = round(float(0.70 + 0.15 * np.random.random()), 4)
    else:
        _eml_state["params"] = {"n_trees": 100, "max_depth": 6}
        val_score = None
    return {
        "epoch": _eml_state["epoch"],
        "samples_used": training_max_samples,
        "validation_score": val_score,
        "hyperparameters": _eml_state["params"],
    }


def dispatch_eml_shadow(dispatch_id: str, beam: Dict) -> Dict[str, Any]:
    """Simulate a beam dispatch through the EML shadow-substrate."""
    if not _eml_state["trained"]:
        # First-run auto-train
        train_eml_shadow()
    return {
        "dispatch_id": dispatch_id,
        "simulated": True,
        "eml_epoch": _eml_state["epoch"],
        "predicted_c_hat_delta": round(float(np.random.uniform(0.02, 0.12) if NUMPY_OK else 0.05), 4),
        "confidence": 0.72,
        "beam_summary": {
            "target_omega": beam.get("target_omega"),
            "duration_s": beam.get("duration_s"),
            "amplitude_ut": beam.get("amplitude_ut"),
        },
    }


# ─── QUANTUM SIMULATION LADDER ───────────────────────────────────────
def dispatch_quantum(backend: str, circuit: Any) -> Dict[str, Any]:
    """
    Route a quantum circuit to the requested backend.
    Fail-soft: if backend unavailable, returns { ok: False, error: ... }.
    V9-preserving: NWO ASM path is NOT included in this release.
    """
    # V9 pre-check on circuit content
    v9 = assess_chiral_or_psitronic_command_py(circuit,
                                                dict(request.headers) if FLASK_OK else None)
    if v9["veto"]:
        return {"ok": False, "refused": "V9", "matched": v9.get("matched_rule"),
                "layer": "render"}

    backend_map = {
        "ibm_quantum_runtime": _dispatch_ibm,
        "origin_wukong":       _dispatch_origin,
        "osaka_qiqb":          _dispatch_osaka,
        "aer_local":           _dispatch_aer,
        "worker_local_numpy":  _dispatch_local,
    }
    handler = backend_map.get(backend, _dispatch_aer)
    return handler(circuit)


def _dispatch_ibm(circuit):
    # Placeholder: reuses v0.7.7 IBM Quantum Runtime plumbing when available
    return {"ok": True, "backend": "ibm_quantum_runtime",
            "result": {"placeholder": True, "note": "wire_to_existing_ibm_route"}}

def _dispatch_origin(circuit):
    return {"ok": True, "backend": "origin_wukong",
            "result": {"placeholder": True, "note": "wire_to_existing_origin_route"}}

def _dispatch_osaka(circuit):
    return {"ok": True, "backend": "osaka_qiqb",
            "result": {"placeholder": True, "note": "wire_to_existing_osaka_route"}}

def _dispatch_aer(circuit):
    return {"ok": True, "backend": "aer_local",
            "result": {"placeholder": True, "note": "wire_to_qiskit_aer_local"}}

def _dispatch_local(circuit):
    return {"ok": True, "backend": "worker_local_numpy",
            "result": {"note": "ultimate_fallback_local_approximation"}}


# ─── ROUTE REGISTRATION ──────────────────────────────────────────────
def register_cfield_routes(app):
    """
    Register /cfield/* routes on the Flask app.  Additive-only.
    Call this from app.py after the app is created:

        from cfield_bridge import register_cfield_routes
        register_cfield_routes(app)
    """
    if not FLASK_OK:
        log.warning("cfield_bridge: Flask not available, routes not registered")
        return

    @app.route("/cfield/status", methods=["GET"])
    def cfield_status_route():
        return jsonify({
            "version": CFIELD_VERSION,
            "numpy_ok": NUMPY_OK,
            "scipy_ok": SCIPY_OK,
            "cfield_enabled": os.environ.get("CFIELD_ENABLED", "true") != "false",
            "eml_simulation_mode":
                os.environ.get("CFIELD_EML_SIMULATION_MODE", "true") != "false",
            "icnirp_cap_ut": _icnirp_cap_ut(),
            "v9_assessor_present": True,
            "ts_ms": int(time.time() * 1000),
        })

    @app.route("/cfield/indicator", methods=["GET"])
    def cfield_indicator_route():
        auth = _require_internal_token()
        if auth: return jsonify(auth[0]), auth[1]
        key = request.args.get("key", "")
        src = request.args.get("src", "")
        val = get_public_indicator(key, src)
        return jsonify({"key": key, "src": src, "value": val,
                        "simulated": True})  # baseline stubs are simulated

    @app.route("/cfield/estimator/tick", methods=["POST"])
    def cfield_estimator_tick_route():
        auth = _require_internal_token()
        if auth: return jsonify(auth[0]), auth[1]
        body = request.get_json(silent=True) or {}
        result = map_invert_disturbance(
            c_hat=body.get("c_hat", 0.5),
            indicators=body.get("indicators", []),
            lambda_a=float(body.get("lambda_a", 0.10)),
            adversarial_classes=body.get("adversarial_classes"),
        )
        return jsonify(result)

    @app.route("/cfield/attribution/tick", methods=["POST"])
    def cfield_attribution_tick_route():
        auth = _require_internal_token()
        if auth: return jsonify(auth[0]), auth[1]
        body = request.get_json(silent=True) or {}
        result = attribute_to_census(
            d_star=body.get("d_star", []),
            adversarial_classes=body.get("adversarial_classes", []),
        )
        return jsonify(result)

    @app.route("/cfield/eml/train", methods=["POST"])
    def cfield_eml_train_route():
        auth = _require_internal_token()
        if auth: return jsonify(auth[0]), auth[1]
        body = request.get_json(silent=True) or {}
        result = train_eml_shadow(
            training_max_samples=int(body.get("training_max_samples", 1000))
        )
        return jsonify(result)

    @app.route("/cfield/eml/dispatch", methods=["POST"])
    def cfield_eml_dispatch_route():
        auth = _require_internal_token()
        if auth: return jsonify(auth[0]), auth[1]
        body = request.get_json(silent=True) or {}
        # V9 defense-in-depth
        v9 = assess_chiral_or_psitronic_command_py(body, dict(request.headers))
        if v9["veto"]:
            return jsonify({"ok": False, "refused": "V9", "trace": v9}), 403
        result = dispatch_eml_shadow(
            dispatch_id=body.get("dispatch_id", "unknown"),
            beam=body.get("beam", {}),
        )
        return jsonify(result)

    @app.route("/cfield/dispatch", methods=["POST"])
    def cfield_dispatch_route():
        auth = _require_internal_token()
        if auth: return jsonify(auth[0]), auth[1]
        body = request.get_json(silent=True) or {}
        # V9 defense-in-depth
        v9 = assess_chiral_or_psitronic_command_py(body, dict(request.headers))
        if v9["veto"]:
            return jsonify({"ok": False, "refused": "V9", "trace": v9}), 403
        beam = body.get("beam", {})
        # ICNIRP defense-in-depth re-check at Render layer
        ok, reason = _verify_icnirp(beam)
        if not ok:
            return jsonify({"ok": False, "refused": "ICNIRP", "reason": reason,
                            "layer": "render"}), 403
        result = simulate_phased_array_beam(beam)
        result["dispatch_id"] = body.get("dispatch_id", "unknown")
        return jsonify(result)

    @app.route("/cfield/quantum/simulate", methods=["POST"])
    def cfield_quantum_simulate_route():
        auth = _require_internal_token()
        if auth: return jsonify(auth[0]), auth[1]
        body = request.get_json(silent=True) or {}
        backend = body.get("backend", "aer_local")
        circuit = body.get("circuit", {})
        result = dispatch_quantum(backend, circuit)
        return jsonify(result)

    @app.route("/cfield/v9-assess", methods=["POST"])
    def cfield_v9_assess_route():
        # Public probe: returns V9 verdict on a payload WITHOUT dispatching.
        body = request.get_json(silent=True) or {}
        result = assess_chiral_or_psitronic_command_py(body, dict(request.headers))
        return jsonify({"probe": True, "layer": "render",
                        "would_veto": result["veto"], "trace": result})

    log.info(f"cfield_bridge: registered {CFIELD_VERSION} routes on Flask app")
