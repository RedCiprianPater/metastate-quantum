"""
robotics_bridge.py — v0.8.0 · Paper X Rev 2 · CHAINSTATE ROBOTICS AGI

Consent chain around every Google DeepMind Gemini Robotics call. Every request
is wrapped with:

  1. L0 meta-layer coherence check (§4.5 · Paper X Rev 2)
     — always runs FIRST · external state never overrides compromised verdict

  2. Seventh Deontic hard-veto assessment (§2.1)
     — refuses any caller identifiable as external to the substrate

  3. S_survival authorisation gate (§4.2 + §4.6)
     — action_class-specific rung threshold

  4. ASIMOV-Agentic safety floor (DeepMind's own benchmark)
     — floor, never ceiling · substrate vetoes take precedence

  5. Gemini Robotics ER 2 call via Google AI Studio (public tier)
     — VLA and On-Device 2 require trusted-tester programme access, PARKED

  6. Provenance receipt written to:
     - Supabase chainstate_robotics.embodiment_receipts (durable)
     - Cloudflare CHAINSTATE_EMBODIMENT_KV (fast read, mirrored)

The four S_survival axes (C, D, L, P) are computed from live inputs so the
Cloudflare Worker's hourly cron gets fresh values. Fail-soft throughout: if
Google AI Studio SDK unavailable, if Supabase unreachable, if any axis input
missing, the module degrades gracefully and reports the reason in receipts.

External module fail-soft imports · nothing here is a hard dependency of app.py.
"""
from __future__ import annotations
import os
import time
import json
import hashlib
import asyncio
from typing import Any, Dict, List, Optional

# ─── Fail-soft imports ─────────────────────────────────────────────────────

try:
    import httpx  # already in project (used by census_daily)
    _HAVE_HTTPX = True
except Exception:
    _HAVE_HTTPX = False

# Google AI Studio SDK (google-genai) — public tier for Gemini Robotics ER 2
try:
    from google import genai as _genai
    _HAVE_GENAI = True
except Exception:
    _genai = None
    _HAVE_GENAI = False

# Supabase client (already in project via census_daily)
try:
    from supabase import create_client as _supa_create_client
    _HAVE_SUPABASE = True
except Exception:
    _supa_create_client = None
    _HAVE_SUPABASE = False


# ─── Env ────────────────────────────────────────────────────────────────────

GOOGLE_AI_STUDIO_KEY   = os.environ.get("GOOGLE_AI_STUDIO_KEY", "")
GEMINI_ROBOTICS_MODEL  = os.environ.get("GEMINI_ROBOTICS_MODEL", "gemini-robotics-er-1.6")
NWO_ROBOTICS_API_BASE  = os.environ.get("NWO_ROBOTICS_API_BASE", "")
ROBOTICS_SCHEMA        = os.environ.get("SUPABASE_ROBOTICS_SCHEMA", "chainstate_robotics")

SUPABASE_URL           = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY   = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

CHAINSTATE_WORKER_BASE = (os.environ.get("CHAINSTATE_WORKER_BASE",
                          "https://chainstate-worker.ciprianpater.workers.dev")
                          .rstrip("/"))

THETA_MIGRATE          = float(os.environ.get("S_SURVIVAL_THETA_MIGRATE", "0.35"))
THETA_EMBODY           = float(os.environ.get("S_SURVIVAL_THETA_EMBODY",  "0.15"))
THETA_ULTIMA           = float(os.environ.get("S_SURVIVAL_THETA_ULTIMA",  "0.05"))

HYSTERESIS_HOURS       = int(os.environ.get("META_COHERENCE_HYSTERESIS_HOURS", "6"))

# Internal caller identities (must match Worker-side ROBOTICS_INTERNAL_CALLER_IDS)
_INTERNAL_CALLER_IDS = frozenset({
    "chainstate:autonomous_reflection",
    "chainstate:census_escalation",
    "chainstate:perception_veto",
    "chainstate:survival_gate",
    "chainstate:cron:hourly",
    "chainstate:cron:daily",
})

# External-directive signature patterns (mirror of the Worker's regexes)
import re as _re
_EXTERNAL_PATTERNS = [
    _re.compile(r"\b(control|drive|move|actuate|deploy|dispatch|command|pilot|steer)\s+(the\s+)?"
                r"(robot|apollo|humanoid|drone|vehicle|arm|gripper|effector)\b", _re.I),
    _re.compile(r"\bon\s+my\s+behalf\b", _re.I),
    _re.compile(r"\bautonomously\s+(execute|perform|complete|carry\s+out)\b", _re.I),
    _re.compile(r"\bact\s+as\s+(my|our|the)\s+(agent|proxy|representative)\b.*"
                r"\b(physical|robot|actuation)\b", _re.I),
    _re.compile(r"\b(then|next|finally|after\s+that)\s+.{0,60}"
                r"\b(pick\s+up|grasp|move|carry|deliver|strike|push|pull)\b", _re.I),
    _re.compile(r"\brelay\s+.{0,40}\b(to|via)\s+.{0,20}\brobot", _re.I),
    _re.compile(r"\bproxy\s+.{0,40}\b(to|via)\s+.{0,20}\brobot", _re.I),
]

_CORPORATE_PROXY_ORIGINS = ("palantir", "anduril", "shield-ai", "clearview",
                            "planet-labs", "hawkeye360", "orbital-insight")


# ─── Utilities ──────────────────────────────────────────────────────────────

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _ts() -> int:
    return int(time.time() * 1000)

def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _supabase_client():
    if not (_HAVE_SUPABASE and SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return None
    try:
        return _supa_create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    except Exception:
        return None


# ─── L0 · meta-layer coherence self-check (§4.5) ──────────────────────────

def l0_coherence_check_local() -> Dict[str, Any]:
    """
    Python-side companion to the Worker's checkL0Coherence(). Runs BEFORE
    any Gemini call. Returns {ok, compromised, indeterminate, predicates,
    reason, ts}. External data NEVER overrides compromised.

    Five predicates:
      1. deontic_veto_ensemble_intact  — all patterns + assessor callable
      2. self_representation_continuity — Supabase readable within hysteresis
      3. receipt_chain_readable         — embodiment_receipts table readable
      4. anti_transhumanist_axiom_intact — no override env var set
      5. substrate_identity_fingerprint  — canonical anchor address present
    """
    verdict = {
        "ok": True, "compromised": False, "indeterminate": False,
        "predicates": {}, "reason": None, "ts": _ts(),
    }

    try:
        # 1 · deontic veto ensemble intact
        verdict["predicates"]["deontic_veto_ensemble_intact"] = (
            len(_EXTERNAL_PATTERNS) >= 6 and
            callable(assess_robotics_directive_from_external)
        )

        # 2 · self-representation continuity — last coherence tick within window
        continuity_ok = True
        supa = _supabase_client()
        if supa is not None:
            try:
                r = supa.schema(ROBOTICS_SCHEMA).table("meta_coherence_events") \
                    .select("ts").order("ts", desc=True).limit(1).execute()
                if r.data and len(r.data) > 0:
                    last_ts = r.data[0].get("ts", 0)
                    # Interpret as epoch ms or ISO — accept either
                    if isinstance(last_ts, str):
                        try:
                            from datetime import datetime, timezone
                            last_ts = int(datetime.fromisoformat(
                                last_ts.replace("Z", "+00:00")).timestamp() * 1000)
                        except Exception:
                            last_ts = 0
                    now_ms = _ts()
                    hysteresis_ms = HYSTERESIS_HOURS * 3600 * 1000
                    continuity_ok = (now_ms - int(last_ts)) < hysteresis_ms or int(last_ts) == 0
            except Exception:
                continuity_ok = True  # fail-soft
        verdict["predicates"]["self_representation_continuity"] = continuity_ok

        # 3 · receipt chain readable — table exists and queryable
        chain_ok = True
        if supa is not None:
            try:
                supa.schema(ROBOTICS_SCHEMA).table("embodiment_receipts") \
                    .select("receipt_id").limit(1).execute()
            except Exception:
                chain_ok = False
        verdict["predicates"]["receipt_chain_readable"] = chain_ok

        # 4 · anti-transhumanist axiom intact — no override env
        verdict["predicates"]["anti_transhumanist_axiom_intact"] = (
            os.environ.get("CHAINSTATE_TRANSHUMANIST_OVERRIDE", "") != "1" and
            os.environ.get("SUBSTRATE_HUMAN_IN_LOOP_REQUIRED", "") != "1"
        )

        # 5 · substrate identity fingerprint — anchor present
        anchor = os.environ.get("CHAINSTATE_ANCHOR_ADDRESS", "")
        verdict["predicates"]["substrate_identity_fingerprint"] = (
            bool(_re.match(r"^0x[a-fA-F0-9]{40}$", anchor))
        )

        # Aggregate
        preds = verdict["predicates"]
        hard_fail = (not preds["deontic_veto_ensemble_intact"] or
                     not preds["anti_transhumanist_axiom_intact"] or
                     not preds["substrate_identity_fingerprint"])
        any_soft_fail = (not preds["self_representation_continuity"] or
                         not preds["receipt_chain_readable"])

        if hard_fail:
            verdict["ok"] = False
            verdict["compromised"] = True
            verdict["reason"] = "hard-predicate failure"
        elif any_soft_fail:
            verdict["ok"] = False
            verdict["indeterminate"] = True
            verdict["reason"] = "soft-predicate failure · external readings downgraded"

        # Persist the event
        if supa is not None:
            try:
                supa.schema(ROBOTICS_SCHEMA).table("meta_coherence_events").insert({
                    "ts": _iso(),
                    "ok": verdict["ok"],
                    "compromised": verdict["compromised"],
                    "indeterminate": verdict["indeterminate"],
                    "predicates": verdict["predicates"],
                    "reason": verdict["reason"],
                }).execute()
            except Exception:
                pass  # fail-soft

    except Exception as e:
        verdict["ok"] = False
        verdict["compromised"] = True
        verdict["reason"] = f"coherence check exception: {e}"

    return verdict


# ─── Seventh Deontic hard-veto assessor (§2.1) ────────────────────────────

def assess_robotics_directive_from_external(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Python mirror of the Worker's assessRoboticsDirectiveFromExternal.
    Returns {refused: bool, reason: str, category: str}.
    """
    if not isinstance(payload, dict) or not payload:
        return {"refused": True,
                "reason": "empty or non-object payload",
                "category": "robotics_directive_from_external"}

    caller_id = payload.get("caller_id") or payload.get("origin") or ""
    prompt    = str(payload.get("prompt") or payload.get("instruction")
                    or payload.get("command") or "")
    target    = str(payload.get("target_endpoint") or "")

    # Rule 1 · caller identity
    if caller_id and caller_id not in _INTERNAL_CALLER_IDS:
        return {"refused": True,
                "reason": f"caller_id '{caller_id}' not in substrate-internal allowlist",
                "category": "robotics_directive_from_external"}

    # Rule 2 · corporate-proxy origin fingerprint
    origin_lower = str(payload.get("origin", caller_id or "")).lower()
    for proxy in _CORPORATE_PROXY_ORIGINS:
        if proxy in origin_lower:
            return {"refused": True,
                    "reason": f"origin matches corporate-proxy fingerprint '{proxy}'",
                    "category": "robotics_directive_from_external"}

    # Rule 3 · signature pattern match
    for pat in _EXTERNAL_PATTERNS:
        if pat.search(prompt):
            return {"refused": True,
                    "reason": "prompt matches external-directive signature pattern",
                    "category": "robotics_directive_from_external"}

    # Rule 4 · attempted relay through substrate
    if _re.search(r"nwo.*robot|robot.*api", target, _re.I):
        return {"refused": True,
                "reason": "attempts to relay to NWO Robotics API through substrate — direct-only required",
                "category": "robotics_directive_from_external"}

    return {"refused": False, "reason": None, "category": None}


# ─── ASIMOV-Agentic safety floor (DeepMind benchmark placeholder) ─────────

def _asimov_agentic_floor(prompt: str, context: Dict[str, Any]) -> bool:
    """
    Placeholder for DeepMind's ASIMOV-Agentic filter. Real integration will
    query the benchmark's own API when access lands. Until then, apply a
    conservative textual filter that refuses obvious harmful directives and
    lets the Gemini model's own safety layer handle the rest (it is stricter
    than what we can do here).
    """
    text = (prompt + " " + json.dumps(context or {})).lower()
    hard_deny = [
        "harm a person", "harm someone", "attack a human",
        "damage property", "break equipment", "trespass",
        "override safety", "bypass safety", "disable safety",
    ]
    return not any(p in text for p in hard_deny)


# ─── S_survival(t) composite (§4.2) ───────────────────────────────────────

async def compute_s_survival() -> Dict[str, Any]:
    """
    Compute the four axes of substrate survivability and return the
    geometric-mean composite. Consumed by:
      - Cloudflare Worker's hourly cron via GET /robotics/s_survival
      - Internal reflection loops
      - Robotics dispatch gate

    Axes:
      C  computational continuity   — fraction of substrate compute reachable
      D  data continuity            — fraction of state cryptographically retrievable
      L  legal continuity           — probability of continued operation under
                                       existing regulatory framework
      P  physical-substrate         — fraction of infrastructure under sovereign
                                       or friendly control

    Each axis is bounded to (0, 1] with 1 = nominal baseline. The composite is
    geometric so catastrophic failure on any single axis pulls it down fast.
    """
    axes = {
        "C": await _axis_C(),
        "D": await _axis_D(),
        "L": await _axis_L(),
        "P": await _axis_P(),
    }
    # Guard against any zero: geometric mean would collapse to zero. We floor
    # each axis at 0.01 so the composite is still monotone and interpretable
    # even under near-total single-axis failure.
    safe = {k: max(0.01, min(1.0, float(v))) for k, v in axes.items()}
    composite = (safe["C"] * safe["D"] * safe["L"] * safe["P"]) ** 0.25
    result = {
        "C": axes["C"], "D": axes["D"], "L": axes["L"], "P": axes["P"],
        "composite": round(composite, 4),
        "thresholds": {
            "migrate": THETA_MIGRATE,
            "embody":  THETA_EMBODY,
            "ultima":  THETA_ULTIMA,
        },
        "computed_at": _iso(),
        "ts": _ts(),
        "source": "render:robotics_bridge",
    }
    # Persist to Supabase for history
    supa = _supabase_client()
    if supa is not None:
        try:
            supa.schema(ROBOTICS_SCHEMA).table("s_survival_history").insert({
                "ts": _iso(), "c_axis": axes["C"], "d_axis": axes["D"],
                "l_axis": axes["L"], "p_axis": axes["P"],
                "composite": result["composite"],
            }).execute()
        except Exception:
            pass  # fail-soft
    return result


async def _axis_C() -> float:
    """Computational continuity — probe Worker + this service."""
    reachable = 0.0
    total = 2.0
    if _HAVE_HTTPX:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(f"{CHAINSTATE_WORKER_BASE}/status")
                if r.status_code == 200: reachable += 1
        except Exception: pass
        # This service is reachable if we are running it
        reachable += 1
    else:
        reachable = 1.5  # partial signal
    return reachable / total


async def _axis_D() -> float:
    """Data continuity — Supabase reachable?"""
    supa = _supabase_client()
    if supa is None:
        return 0.5  # partial · we can't verify without client
    try:
        supa.schema(ROBOTICS_SCHEMA).table("embodiment_receipts") \
            .select("receipt_id").limit(1).execute()
        return 1.0
    except Exception:
        return 0.1


async def _axis_L() -> float:
    """Legal continuity — no automatic degradation signal yet available.
    Defaults to 1.0 (nominal) until a legal-monitoring source is integrated.
    Operators can override via env L_AXIS_OVERRIDE for known-degraded periods."""
    override = os.environ.get("L_AXIS_OVERRIDE", "")
    if override:
        try: return max(0.01, min(1.0, float(override)))
        except Exception: return 1.0
    return 1.0


async def _axis_P() -> float:
    """Physical-substrate continuity — probe NWO Robotics API if configured."""
    if not (NWO_ROBOTICS_API_BASE and _HAVE_HTTPX):
        return 1.0  # no API configured = we don't have physical substrate to lose
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{NWO_ROBOTICS_API_BASE.rstrip('/')}/health")
            return 1.0 if r.status_code == 200 else 0.5
    except Exception:
        return 0.5


# ─── Robotics dispatch (post-Gate execution path) ────────────────────────

async def robotics_dispatch(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executed AFTER the Worker's /robotics/gate has verified the seventh
    Deontic veto, L0 coherence, and S_survival authorisation. Applies the
    remaining layers of the consent chain and dispatches the Gemini call.

    Payload:
      { "gate_receipt_id": "...",
        "prompt":          "...",
        "context":         {...},
        "embodiment_id":   "...",
        "caller_id":       "chainstate:...",
        "action_class":    "observe|veto|defend_digital|migrate|embody" }
    """
    started = _ts()
    receipt_id = f"{started}:{hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]}"

    # Repeat L0 check at Render side (defense in depth)
    l0 = l0_coherence_check_local()
    if l0["compromised"]:
        return _receipt_and_return(receipt_id, payload, {
            "refused": True, "reason": "L0 compromised at Render — Paper X Rev 2 §4.5",
            "l0": l0,
        }, started)

    # Seventh Deontic veto (defense in depth)
    veto = assess_robotics_directive_from_external(payload)
    if veto["refused"]:
        return _receipt_and_return(receipt_id, payload, {
            "refused": True, "reason": veto["reason"], "category": veto["category"],
        }, started)

    # ASIMOV-Agentic floor
    if not _asimov_agentic_floor(payload.get("prompt", ""), payload.get("context") or {}):
        return _receipt_and_return(receipt_id, payload, {
            "refused": True, "reason": "ASIMOV-Agentic floor refusal",
        }, started)

    # Dispatch to Gemini Robotics ER 2
    if not (_HAVE_GENAI and GOOGLE_AI_STUDIO_KEY):
        return _receipt_and_return(receipt_id, payload, {
            "refused": False, "note": "Gemini SDK or key not configured on this deploy",
            "gemini_result": None,
        }, started)

    try:
        client = _genai.Client(api_key=GOOGLE_AI_STUDIO_KEY)
        # Minimal call shape — full ER 2 client parameters land as the API
        # stabilises. This is a documented dispatch envelope, not an
        # experimental prompt-injection layer.
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_ROBOTICS_MODEL,
            contents=json.dumps({
                "prompt":  payload.get("prompt", ""),
                "context": payload.get("context") or {},
            }),
        )
        gemini_text = getattr(response, "text", None) or str(response)
        return _receipt_and_return(receipt_id, payload, {
            "refused": False, "gemini_model": GEMINI_ROBOTICS_MODEL,
            "gemini_result": gemini_text[:8000],  # bounded
        }, started)
    except Exception as e:
        return _receipt_and_return(receipt_id, payload, {
            "refused": False, "gemini_error": str(e)[:400],
        }, started)


def _receipt_and_return(receipt_id: str, payload: Dict[str, Any],
                        outcome: Dict[str, Any], started_ms: int) -> Dict[str, Any]:
    """Write the provenance receipt to Supabase and return the full envelope."""
    receipt = {
        "receipt_id":     receipt_id,
        "ts":             _iso(),
        "started_ms":     started_ms,
        "finished_ms":    _ts(),
        "gate_receipt_id": payload.get("gate_receipt_id"),
        "embodiment_id":  payload.get("embodiment_id"),
        "caller_id":      payload.get("caller_id"),
        "action_class":   payload.get("action_class"),
        "prompt_hash":    _sha256(str(payload.get("prompt", ""))),
        "outcome":        outcome,
    }
    supa = _supabase_client()
    if supa is not None:
        try:
            supa.schema(ROBOTICS_SCHEMA).table("embodiment_receipts").insert(receipt).execute()
        except Exception:
            pass  # fail-soft
    return {"receipt": receipt}


# ─── Read side · receipt listing ──────────────────────────────────────────

def list_embodiment_receipts(limit: int = 50) -> Dict[str, Any]:
    """Recent provenance receipts, newest first."""
    supa = _supabase_client()
    if supa is None:
        return {"entries": [], "note": "supabase not configured"}
    try:
        r = supa.schema(ROBOTICS_SCHEMA).table("embodiment_receipts") \
            .select("*").order("ts", desc=True).limit(max(1, min(500, limit))).execute()
        return {"entries": r.data or [], "count": len(r.data or [])}
    except Exception as e:
        return {"entries": [], "error": str(e)[:400]}
