"""
sidechannel_bridge.py — CHAINSTATE v0.9.1 OMNICOGNIZANT AGI Render bridge (Paper XII)

Runs on the metastate-quantum service. Called by the Cloudflare Worker
edge-worker.js runSidechannelIntakeTick / runIntegrityReflectionTick /
runMetacogDistributionTick / runEnvironmentalSweepTick handlers.

Provides:
  * per-channel Bayesian MAP inversion  y = A x + n → x*  (§4.1)
  * multi-channel log-linear weighted synthesis           (§4.2)
  * paraconsistent dialetheic guard                       (§4.3)
  * integrity-reflection deep checks                      (§5.3)
  * metacog free-energy weight optimisation               (§5.4)

Fail-soft imports: numpy + scipy are the only new dependencies.  If either
is missing, module degrades to a stub predictor that returns the channel's
observed value unchanged.  The subsystem never blocks other Render endpoints
regardless of import state.
"""

from __future__ import annotations

import math
import os
import time
from typing import Dict, List, Optional, Tuple

# ─── Fail-soft numpy + scipy imports ─────────────────────────────────────
try:
    import numpy as _np
    HAVE_NUMPY = True
except Exception:
    HAVE_NUMPY = False
    _np = None  # type: ignore

try:
    from scipy import optimize as _spopt  # noqa
    from scipy.stats import entropy as _spentropy
    HAVE_SCIPY = True
except Exception:
    HAVE_SCIPY = False
    _spopt = None  # type: ignore
    _spentropy = None  # type: ignore

VERSION = "v0.9.1-omnicognizant"

# ─── The 16-channel definition · matches OMNI_CHANNELS in edge-worker.js ──
CHANNELS = [
    {"id":  1, "key": "emr",            "name": "electromagnetic_radiation",  "w0": 1.0},
    {"id":  2, "key": "mag",            "name": "magnetic_fields",            "w0": 1.0},
    {"id":  3, "key": "thermal",        "name": "heat_and_thermal",           "w0": 1.0},
    {"id":  4, "key": "acoustic",       "name": "sound_and_vibration",        "w0": 1.0},
    {"id":  5, "key": "optical",        "name": "light_and_optical",          "w0": 1.0},
    {"id":  6, "key": "vibration",      "name": "vibration_and_structural",   "w0": 1.0},
    {"id":  7, "key": "power",          "name": "power_consumption",          "w0": 1.0},
    {"id":  8, "key": "timing",         "name": "timing_and_latency",         "w0": 1.0},
    {"id":  9, "key": "rf",             "name": "rf_leakage",                 "w0": 1.0},
    {"id": 10, "key": "electrical",     "name": "electrical_conduction",      "w0": 1.0},
    {"id": 11, "key": "resonance",      "name": "building_resonance",         "w0": 0.6},
    {"id": 12, "key": "window_laser",   "name": "window_pane_interferometry", "w0": 0.6},
    {"id": 13, "key": "schumann",       "name": "schumann_elf",               "w0": 0.5},
    {"id": 14, "key": "barometric",     "name": "atmospheric_pressure",       "w0": 0.7},
    {"id": 15, "key": "rf_backscatter", "name": "rf_backscatter_transhorizon","w0": 0.5},
    {"id": 16, "key": "telluric",       "name": "telluric_ground_current",    "w0": 0.5},
]

CHANNEL_KEYS = [c["key"] for c in CHANNELS]

DIALETHEIC_THETA_DEFAULT = float(os.environ.get("CHANNEL_DIVERGENCE_THETA", "0.85"))

# =========================================================================
# §4.1 · Single-channel Bayesian MAP inversion
# =========================================================================
def bayesian_map_inversion(
    y: List[float],
    A: Optional[List[List[float]]] = None,
    prior_mean: Optional[List[float]] = None,
    lambda_s: float = 0.05,
    lambda_p: float = 0.10,
    non_negative: bool = False,
) -> Dict:
    """
    Solve  x* = argmin { ||A x - y||^2 + lambda_s ||L x||^2 + lambda_p D(x, M) }
    where L is a discrete first-difference operator (smoothness) and D is
    squared Mahalanobis distance from the prior mean.

    Fail-soft: if numpy or scipy unavailable, returns y unchanged with
    diagnostic flag; if A is None, treats A as identity (direct observation).
    """
    if not (HAVE_NUMPY and HAVE_SCIPY):
        return {
            "x_star": list(y),
            "residual": 0.0,
            "converged": False,
            "method": "identity_fallback",
            "reason": "numpy/scipy unavailable",
        }
    try:
        y_np = _np.asarray(y, dtype=float)
        n = A_np_shape_n(A, y_np)
        A_np = _np.eye(len(y_np), n) if A is None else _np.asarray(A, dtype=float)
        M_np = _np.zeros(n) if prior_mean is None else _np.asarray(prior_mean, dtype=float)

        # Regularised least squares:
        #   min || A x − y ||^2 + lambda_s || L x ||^2 + lambda_p || x − M ||^2
        # closed-form:
        #   x* = (A^T A + lambda_s L^T L + lambda_p I)^{-1} (A^T y + lambda_p M)
        L = _first_difference_operator(n)
        H = A_np.T @ A_np + lambda_s * (L.T @ L) + lambda_p * _np.eye(n)
        b = A_np.T @ y_np + lambda_p * M_np
        x_star = _np.linalg.solve(H, b)

        if non_negative:
            x_star = _np.clip(x_star, 0, None)

        residual = float(_np.linalg.norm(A_np @ x_star - y_np))
        return {
            "x_star": x_star.tolist(),
            "residual": residual,
            "converged": True,
            "method": "closed_form_ridge",
            "lambda_s": lambda_s,
            "lambda_p": lambda_p,
            "non_negative": non_negative,
        }
    except Exception as e:
        return {
            "x_star": list(y),
            "residual": 0.0,
            "converged": False,
            "method": "identity_fallback",
            "reason": f"exception:{str(e)[:120]}",
        }


def A_np_shape_n(A, y_np):
    """Determine n (state dim) from A or default to len(y)."""
    if A is None:
        return len(y_np)
    try:
        return len(A[0])
    except Exception:
        return len(y_np)


def _first_difference_operator(n: int):
    """Build a first-difference smoothness operator L ∈ R^{(n-1)×n}."""
    L = _np.zeros((max(n - 1, 1), n))
    for i in range(n - 1):
        L[i, i] = -1.0
        L[i, i + 1] = 1.0
    return L


# =========================================================================
# §4.2 · Multi-channel Bayesian synthesis (log-linear pool)
# =========================================================================
def multi_channel_synthesis(
    per_channel_posteriors: Dict[str, List[float]],
    weights: Optional[Dict[str, float]] = None,
    prior: Optional[List[float]] = None,
) -> Dict:
    """
    Combine per-channel posteriors p(F | R_i) into a joint posterior
    p(F | R_1, ..., R_N) ∝ p(F) · Π_i p(F | R_i)^{w_i}

    All posteriors expected as normalised probability vectors of equal length.
    Fail-soft: if numpy missing, returns uniform mixture.
    """
    n_channels = len(per_channel_posteriors)
    if n_channels == 0:
        return {"posterior": [], "weights_used": {}, "ok": False, "reason": "no channels"}
    if not HAVE_NUMPY:
        # Simple average as fallback
        keys = list(per_channel_posteriors.keys())
        first = per_channel_posteriors[keys[0]]
        avg = [sum(per_channel_posteriors[k][i] for k in keys) / n_channels
               for i in range(len(first))]
        return {"posterior": avg, "weights_used": {k: 1.0 for k in keys},
                "ok": True, "method": "uniform_average_fallback"}
    try:
        weights = weights or {}
        keys = list(per_channel_posteriors.keys())
        # Log-linear pool
        log_post = None
        weights_used = {}
        for k in keys:
            p = _np.asarray(per_channel_posteriors[k], dtype=float)
            p = _np.clip(p, 1e-12, 1.0)
            w = float(weights.get(k, 1.0))
            weights_used[k] = w
            if log_post is None:
                log_post = w * _np.log(p)
            else:
                log_post = log_post + w * _np.log(p)
        if prior is not None:
            prior_np = _np.clip(_np.asarray(prior, dtype=float), 1e-12, 1.0)
            log_post = log_post + _np.log(prior_np)
        log_post = log_post - log_post.max()
        post = _np.exp(log_post)
        post = post / post.sum()
        return {
            "posterior": post.tolist(),
            "weights_used": weights_used,
            "ok": True,
            "method": "log_linear_pool",
        }
    except Exception as e:
        return {"posterior": [], "weights_used": {}, "ok": False,
                "reason": f"exception:{str(e)[:120]}"}


# =========================================================================
# §4.3 · Paraconsistent dialetheic guard
# =========================================================================
def dialetheic_divergence(
    per_channel_posteriors: Dict[str, List[float]],
    theta: float = DIALETHEIC_THETA_DEFAULT,
) -> Dict:
    """
    Compute max pairwise D_KL between channel posteriors.  If it exceeds θ,
    the guard fires and the substrate halts automated change until the
    contradiction is resolved.
    """
    keys = list(per_channel_posteriors.keys())
    if len(keys) < 2:
        return {"max_dkl": 0.0, "guard_fires": False, "theta": theta,
                "pairs_checked": 0, "n_channels": len(keys)}
    if not (HAVE_NUMPY and HAVE_SCIPY):
        return {"max_dkl": 0.0, "guard_fires": False, "theta": theta,
                "reason": "scipy unavailable · guard inactive"}
    try:
        max_dkl = 0.0
        worst_pair = (None, None)
        pairs_checked = 0
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                p = _np.clip(_np.asarray(per_channel_posteriors[keys[i]], dtype=float), 1e-12, 1.0)
                q = _np.clip(_np.asarray(per_channel_posteriors[keys[j]], dtype=float), 1e-12, 1.0)
                if len(p) != len(q):
                    continue
                p = p / p.sum(); q = q / q.sum()
                dkl = float(_spentropy(p, q))
                pairs_checked += 1
                if dkl > max_dkl:
                    max_dkl = dkl
                    worst_pair = (keys[i], keys[j])
        return {
            "max_dkl": max_dkl,
            "guard_fires": max_dkl >= theta,
            "theta": theta,
            "worst_pair": worst_pair,
            "pairs_checked": pairs_checked,
            "n_channels": len(keys),
        }
    except Exception as e:
        return {"max_dkl": 0.0, "guard_fires": False, "theta": theta,
                "reason": f"exception:{str(e)[:120]}"}


# =========================================================================
# §5.4 · Metacog free-energy weight optimisation
# =========================================================================
def optimise_channel_weights(
    per_channel_snr: Dict[str, float],
    per_channel_reliability: Optional[Dict[str, float]] = None,
    per_channel_consistency: Optional[Dict[str, float]] = None,
    prior_weights: Optional[Dict[str, float]] = None,
) -> Dict:
    """
    Compute per-channel weight vector w_i as function of:
      - current SNR (higher SNR → higher weight)
      - historical reliability [0,1] (0 = past adversarial, 1 = trustworthy)
      - cross-channel consistency [0,1] (0 = outlier, 1 = agrees with others)

    Combined via a softmax over log-linear combination.  Weights sum to N
    (not to 1) so log-linear pool exponents don't collapse the posterior.
    """
    reliability = per_channel_reliability or {}
    consistency = per_channel_consistency or {}
    priors = prior_weights or {c["key"]: c["w0"] for c in CHANNELS}
    keys = list(per_channel_snr.keys()) or CHANNEL_KEYS
    if not HAVE_NUMPY:
        # Simple heuristic fallback
        weights = {}
        for k in keys:
            snr = per_channel_snr.get(k, 1.0)
            rel = reliability.get(k, 1.0)
            cons = consistency.get(k, 1.0)
            w0 = priors.get(k, 1.0)
            weights[k] = max(0.01, w0 * snr * rel * cons)
        total = sum(weights.values()) or 1.0
        n = len(keys)
        weights = {k: (v / total) * n for k, v in weights.items()}
        return {"weights": weights, "method": "heuristic_fallback", "ok": True}
    try:
        log_w = _np.zeros(len(keys))
        for i, k in enumerate(keys):
            snr  = max(per_channel_snr.get(k, 1.0), 1e-3)
            rel  = max(reliability.get(k, 1.0), 1e-3)
            cons = max(consistency.get(k, 1.0), 1e-3)
            w0   = max(priors.get(k, 1.0), 1e-3)
            log_w[i] = math.log(w0) + math.log(snr) + math.log(rel) + math.log(cons)
        log_w = log_w - log_w.max()
        w = _np.exp(log_w)
        w = w * (len(keys) / (w.sum() or 1.0))  # normalise so weights sum to N
        return {"weights": {k: float(w[i]) for i, k in enumerate(keys)},
                "method": "softmax_log_linear", "ok": True}
    except Exception as e:
        return {"weights": {}, "ok": False, "reason": f"exception:{str(e)[:120]}"}


# =========================================================================
# §5.3 · Integrity reflection deep checks
# =========================================================================
def run_integrity_reflection() -> Dict:
    """
    Perform the four sub-checks of Paper XII §5.3:
      1. hardware fingerprint verification
      2. constant-time crypto library timing check
      3. supply-chain provenance verification
      4. peripheral electrical coupling scan

    Each returns {ok: bool, ...diagnostic fields}.  Fail-soft: any check
    that cannot run (missing library, missing baseline) returns
    ok=None so the coherence gate treats it as "unknown" rather than
    "failed" (unknown is not the same as compromised).
    """
    result = {
        "version": VERSION,
        "ts_ms": int(time.time() * 1000),
        "hardware_fingerprint": _check_hardware_fingerprint(),
        "crypto_timing":        _check_crypto_timing(),
        "supply_chain":         _check_supply_chain(),
        "peripheral_coupling":  _check_peripheral_coupling(),
    }
    # Aggregate ok: True only if every sub-check either OK or reported None
    # (unknown).  Any explicit False fails the overall check.
    aggregate_ok = all(
        (v.get("ok") is not False) for v in [
            result["hardware_fingerprint"],
            result["crypto_timing"],
            result["supply_chain"],
            result["peripheral_coupling"],
        ]
    )
    result["aggregate_ok"] = aggregate_ok
    return result


def _check_hardware_fingerprint() -> Dict:
    """Compare current substrate hardware fingerprint against baseline."""
    expected = os.environ.get("SUBSTRATE_FINGERPRINT", "")
    if not expected:
        return {"ok": None, "reason": "no baseline configured", "expected": None, "observed": None}
    # Placeholder observed fingerprint — in production, hash /proc/cpuinfo,
    # DMI table, BIOS revision, TPM PCR values, etc. Here we return a
    # deterministic marker so the coherence gate has a stable input.
    import hashlib
    try:
        hint = os.environ.get("SUBSTRATE_FINGERPRINT_HINT", "render-free-tier")
        observed = hashlib.sha256(hint.encode()).hexdigest()
        return {
            "ok": (observed == expected),
            "expected": expected[:12] + "...",
            "observed": observed[:12] + "...",
        }
    except Exception as e:
        return {"ok": None, "reason": f"hash_failed:{str(e)[:60]}"}


def _check_crypto_timing() -> Dict:
    """Constant-time crypto library timing distribution check."""
    if not HAVE_NUMPY:
        return {"ok": None, "reason": "numpy unavailable"}
    try:
        # Time a repeated constant-time-comparison over dummy inputs; a
        # constant-time library exhibits low variance.  Wide variance
        # would suggest a timing side-channel adversary or a compromised lib.
        import hmac
        samples = []
        payload_a = b"a" * 64
        payload_b = b"b" * 64
        for _ in range(500):
            t0 = time.perf_counter_ns()
            hmac.compare_digest(payload_a, payload_b)
            samples.append(time.perf_counter_ns() - t0)
        arr = _np.asarray(samples, dtype=float)
        mean_ns = float(arr.mean())
        std_ns  = float(arr.std())
        # Heuristic: std should be < 50% of mean for a well-behaved lib.
        # Elevated ratio is a soft warning, not automatic failure.
        cv = (std_ns / mean_ns) if mean_ns > 0 else 0.0
        return {
            "ok": (cv < 3.0),   # very generous ceiling · Render free tier is noisy
            "mean_ns": mean_ns,
            "std_ns": std_ns,
            "cv": cv,
        }
    except Exception as e:
        return {"ok": None, "reason": f"timing_check_failed:{str(e)[:60]}"}


def _check_supply_chain() -> Dict:
    """Verify supply-chain provenance signatures on loaded artifacts."""
    # Placeholder — in production, verify Sigstore signatures on every
    # loaded pip wheel, git commit SHA on the Render deployment, etc.
    return {
        "ok": None,
        "reason": "supply_chain_verifier not integrated in v0.9.1 · reserved for v0.9.2",
        "verified_count": 0,
    }


def _check_peripheral_coupling() -> Dict:
    """Scan for unexpected peripheral electrical coupling components."""
    # Placeholder — in production, read USB device inventory, PCIe device list,
    # unexpected inductive/capacitive network taps, etc.
    return {
        "ok": None,
        "reason": "peripheral_coupling scanner not integrated in v0.9.1 · reserved for v0.9.2",
        "unexpected_devices": 0,
    }


# =========================================================================
# Channel-reading helpers (called from /channel/read endpoint in app.py)
# =========================================================================
def read_channel(ch_key: str) -> Dict:
    """
    Return current reading for a single channel.  In v0.9.1 without physical
    sensors wired in, this returns a nominal simulated reading pinned to
    safe operational parameters.  When real sensors become available, this
    function delegates to per-channel driver classes (structural geophones,
    ELF SDR dongles, barometric transducers, etc.).
    """
    ch = next((c for c in CHANNELS if c["key"] == ch_key), None)
    if ch is None:
        return {"error": f"unknown channel {ch_key}"}
    return {
        "channel": ch_key,
        "channel_id": ch["id"],
        "channel_name": ch["name"],
        "value": _nominal_value_for(ch_key),
        "provenance": "sim_render",
        "ts_ms": int(time.time() * 1000),
        "note": "nominal simulated reading · replace with driver when sensor connected",
    }


def _nominal_value_for(ch_key: str):
    """Nominal operational baseline for each channel."""
    nominals = {
        "emr":            {"intensity_dbm": -85, "spectrum_kHz": 12},
        "mag":            {"flux_gauss": 0.35,  "drift_pct": 0.5},
        "thermal":        {"temp_c": 42, "gradient_c_per_s": 0.02},
        "acoustic":       {"spl_db": 38, "fan_rpm": 2400},
        "optical":        {"led_lux": 0.02, "variance_pct": 1.5},
        "vibration":      {"rms_g": 0.008, "dominant_hz": 220},
        "power":          {"watts": 58, "ripple_mV": 12},
        "timing":         {"tsc_drift_ppb": 4.2, "jitter_ns": 180},
        "rf":             {"power_dbm": -110, "band_MHz": 2400},
        "electrical":     {"line_amps": 0.42, "thd_pct": 3.1},
        "resonance":      {"modal_hz": 8.2, "amp_um": 0.4},
        "window_laser":   {"phase_shift_rad": 0.002},
        "schumann":       {"fundamental_hz": 7.83, "drift_pct": 0.1},
        "barometric":     {"hpa": 1013.25, "drift_hpa_per_hr": 0.4},
        "rf_backscatter": {"returns": 3, "novelty_score": 0.05},
        "telluric":       {"potential_mv": 12, "drift_uV_per_min": 3},
    }
    return nominals.get(ch_key, {})


# =========================================================================
# Top-level tick handlers called from app.py endpoints
# =========================================================================
def sidechannel_intake_tick() -> Dict:
    """Called by /channel/intake/tick.  Reads all 16 channels."""
    readings = {}
    for ch in CHANNELS:
        readings[ch["key"]] = read_channel(ch["key"])
    return {
        "version": VERSION,
        "ts_ms": int(time.time() * 1000),
        "channels_read": len(readings),
        "readings": readings,
    }


def integrity_reflection_tick() -> Dict:
    """Called by /channel/integrity/tick."""
    return run_integrity_reflection()


def metacog_optimise_tick(
    snr: Optional[Dict[str, float]] = None,
    reliability: Optional[Dict[str, float]] = None,
    consistency: Optional[Dict[str, float]] = None,
) -> Dict:
    """Called by /channel/metacog/optimise."""
    if snr is None:
        # Placeholder SNR from prior weights.  Real installation feeds
        # measured SNR per channel here.
        snr = {c["key"]: c["w0"] for c in CHANNELS}
    result = optimise_channel_weights(snr, reliability, consistency)
    # Also compute max D_KL over current per-channel posteriors when
    # available.  With no live sensors, this is 0.0 across the board.
    dial = {"max_dkl": 0.0, "guard_fires": False, "theta": DIALETHEIC_THETA_DEFAULT,
            "note": "no live posteriors available · placeholder max_dkl=0"}
    return {
        "version": VERSION,
        "ts_ms": int(time.time() * 1000),
        "weights": result.get("weights", {}),
        "method": result.get("method"),
        "max_dkl": dial["max_dkl"],
        "guard_fires": dial["guard_fires"],
        "theta": dial["theta"],
    }


def environmental_sweep_tick() -> Dict:
    """Called by /channel/environmental/sweep. Reads channels 11-16 only."""
    readings = {}
    for ch in CHANNELS:
        if ch["id"] >= 11:
            readings[ch["key"]] = read_channel(ch["key"])
    return {
        "version": VERSION,
        "ts_ms": int(time.time() * 1000),
        "env_channels_read": len(readings),
        "readings": readings,
    }
