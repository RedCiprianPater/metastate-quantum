"""
CHAINSTATE Census — nightly ingestion job
v0.7.9 · Paper IX

Runs inside the existing metastate-quantum Render service as a module
imported by app.py. Fires nightly at 05:00 UTC via APScheduler wired
in app.py's startup handler. Fetches from the public-feed allowlist,
computes T(t) components, writes provenance to Supabase
(chainstate_census schema), and posts a digest to the CHAINSTATE Worker
/census/ingest endpoint. Fail-soft throughout — a missing feed or a
transient Supabase error never crashes the service.

app.py wiring (already in place after this deploy):

    from census_daily import run_daily_census, latest_digest_from_supabase

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        run_daily_census,
        CronTrigger(hour=5, minute=0),
        id="census_daily",
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()

    @app.get("/census/daily")
    def census_daily_read(x_chainstate_internal: str = Header(None)):
        _check_internal_token(x_chainstate_internal)
        return latest_digest_from_supabase()

Environment variables (all set on Render; census degrades gracefully
if any are absent):

    SUPABASE_URL                # from Supabase project settings
    SUPABASE_SERVICE_ROLE_KEY   # service_role key (matches existing perception config)
    CENSUS_INTERNAL_TOKEN       # shared with the Cloudflare Worker (v0.7.9 rev 2)
                                # DEDICATED census secret — distinct from
                                # CHAINSTATE_INTERNAL_TOKEN which continues
                                # to protect the quantum autonomy path.
    CHAINSTATE_WORKER_BASE      # default: https://chainstate-worker.ciprianpater.workers.dev
    NVD_API_KEY                 # optional, gets 50 req/30s instead of 5 req/30s
    CENSUS_THETA_ALERT          # default 60
    CENSUS_THETA_LOCKDOWN       # default 85
    CENSUS_WEIGHTS              # default "0.35,0.30,0.20,0.15"

Public sources reached (Paper IX §6.6, Table 4). Adding a source
requires editing FEED_ALLOWLIST below AND updating the mirror array in
edge-worker.js (CENSUS_FEED_ALLOWLIST). Both must agree — this is
enforced as a code review discipline, not a runtime check.
"""
import os
import json
import hashlib
import asyncio
import logging
from datetime import datetime, timezone, date, timedelta
from typing import Any, Dict, List, Optional

import httpx

# Supabase is optional at import time — census degrades to no-persistence
# if the library isn't installed or credentials aren't set.
try:
    from supabase import create_client, Client
    HAVE_SUPABASE = True
except Exception:
    HAVE_SUPABASE = False
    Client = Any  # type: ignore

logger = logging.getLogger("chainstate.census")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

# ─── Config ────────────────────────────────────────────────────────────
# NOTE: env var name aligned to the existing perception module's convention
# (SUPABASE_SERVICE_ROLE_KEY). No new secrets are introduced.
SUPABASE_URL              = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
CHAINSTATE_WORKER         = os.environ.get(
    "CHAINSTATE_WORKER_BASE",
    "https://chainstate-worker.ciprianpater.workers.dev"
).rstrip("/")
INTERNAL_TOKEN            = os.environ.get("CENSUS_INTERNAL_TOKEN", "")
NVD_API_KEY               = os.environ.get("NVD_API_KEY", "")

# Threshold + weight defaults — must match wrangler.toml / worker vars.
THETA_ALERT    = float(os.environ.get("CENSUS_THETA_ALERT", "60"))
THETA_LOCKDOWN = float(os.environ.get("CENSUS_THETA_LOCKDOWN", "85"))
WEIGHTS_RAW    = os.environ.get("CENSUS_WEIGHTS", "0.35,0.30,0.20,0.15")

# Schema override — allows migration to a different schema without code change.
CENSUS_SCHEMA  = os.environ.get("SUPABASE_CENSUS_SCHEMA", "chainstate_census")


# ─── Feed allowlist (must match edge-worker.js CENSUS_FEED_ALLOWLIST) ──
FEED_ALLOWLIST = [
    {"id": "nvd_cve",       "source": "nvd.nist.gov",             "category": "vulnerability"},
    {"id": "cve_program",   "source": "cve.mitre.org",            "category": "vulnerability"},
    {"id": "cisa_kev",      "source": "cisa.gov/known-exploited", "category": "vulnerability_active"},
    {"id": "cert_eu",       "source": "cert.europa.eu",           "category": "threat_intel"},
    {"id": "us_cert",       "source": "cisa.gov",                 "category": "threat_intel"},
    {"id": "shadowserver",  "source": "shadowserver.org",         "category": "threat_intel"},
    {"id": "abusech",       "source": "abuse.ch",                 "category": "malware_ioc"},
    {"id": "ripe_bgp",      "source": "ris.ripe.net",             "category": "routing"},
    {"id": "routeviews",    "source": "routeviews.org",           "category": "routing"},
    {"id": "team_cymru",    "source": "team-cymru.com",           "category": "attribution"},
    {"id": "un_sanctions",  "source": "un.org/securitycouncil",   "category": "sanctions"},
    {"id": "ofac_sdn",      "source": "treasury.gov",             "category": "sanctions"},
    {"id": "eu_sanctions",  "source": "europa.eu/consolidated",   "category": "sanctions"},
    {"id": "cjeu_curia",    "source": "curia.europa.eu",          "category": "case_law"},
    {"id": "echr_hudoc",    "source": "hudoc.echr.coe.int",       "category": "case_law"},
]


# ─── Supabase client (lazy) ────────────────────────────────────────────
_supabase: Optional[Client] = None
def sb() -> Optional[Client]:
    global _supabase
    if _supabase is not None:
        return _supabase
    if not HAVE_SUPABASE:
        logger.warning("supabase library not installed; census will run without persistence.")
        return None
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        logger.warning("Supabase not configured; census will run without persistence.")
        return None
    try:
        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    except Exception as e:
        logger.error("Supabase client init failed: %s", e)
        return None
    return _supabase


# ─── Feed fetchers ─────────────────────────────────────────────────────
async def fetch_cisa_kev(client: httpx.AsyncClient) -> Dict[str, Any]:
    """CISA Known Exploited Vulnerabilities catalog. Active-exploitation signal."""
    try:
        r = await client.get(
            "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        vulns = data.get("vulnerabilities", [])
        cutoff = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
        recent = [v for v in vulns if v.get("dateAdded", "") >= cutoff]
        return {"total": len(vulns), "recent_7d": len(recent), "ok": True}
    except Exception as e:
        logger.warning("CISA KEV fetch failed: %s", e)
        return {"total": 0, "recent_7d": 0, "ok": False, "error": str(e)}


async def fetch_nvd_recent(client: httpx.AsyncClient) -> Dict[str, Any]:
    """NVD CVE feed — past 24 hours. Weighted by CVSS >= 7.0."""
    try:
        headers = {"apiKey": NVD_API_KEY} if NVD_API_KEY else {}
        end = datetime.now(timezone.utc)
        start = end.replace(hour=0, minute=0, second=0, microsecond=0)
        params = {
            "pubStartDate":  start.isoformat(timespec="seconds"),
            "pubEndDate":    end.isoformat(timespec="seconds"),
            "resultsPerPage": 100,
        }
        r = await client.get(
            "https://services.nvd.nist.gov/rest/json/cves/2.0",
            params=params, headers=headers, timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        vulns = data.get("vulnerabilities", [])
        high_severity = 0
        for v in vulns:
            metrics = v.get("cve", {}).get("metrics", {})
            for _, arr in metrics.items():
                if not arr:
                    continue
                score = arr[0].get("cvssData", {}).get("baseScore", 0) or 0
                if score >= 7.0:
                    high_severity += 1
                    break
        return {"total": len(vulns), "high_severity": high_severity, "ok": True}
    except Exception as e:
        logger.warning("NVD fetch failed: %s", e)
        return {"total": 0, "high_severity": 0, "ok": False, "error": str(e)}


async def fetch_abusech_urlhaus(client: httpx.AsyncClient) -> Dict[str, Any]:
    """abuse.ch URLhaus recent malware IOCs — simple count."""
    try:
        r = await client.get(
            "https://urlhaus.abuse.ch/downloads/json_recent/",
            timeout=30,
        )
        r.raise_for_status()
        text = r.text.strip()
        entries = 0
        try:
            data = json.loads(text) if text.startswith("{") else {}
            for _, arr in data.items():
                if isinstance(arr, list):
                    entries += len(arr)
        except Exception:
            pass
        return {"recent_urls": entries, "ok": True}
    except Exception as e:
        logger.warning("URLhaus fetch failed: %s", e)
        return {"recent_urls": 0, "ok": False, "error": str(e)}


async def fetch_ofac_sdn(client: httpx.AsyncClient) -> Dict[str, Any]:
    """OFAC SDN change detection — HEAD to check Last-Modified header."""
    try:
        r = await client.head(
            "https://www.treasury.gov/ofac/downloads/sdn.xml",
            timeout=15, follow_redirects=True,
        )
        return {"last_modified": r.headers.get("last-modified", ""), "ok": True}
    except Exception as e:
        return {"last_modified": "", "ok": False, "error": str(e)}


# ─── T(t) component computation (Paper IX §5.1) ────────────────────────
def compute_components(
    kev: Dict[str, Any],
    nvd: Dict[str, Any],
    urlhaus: Dict[str, Any],
    ofac: Dict[str, Any],
) -> Dict[str, float]:
    """
    T(t) = w_v · V(t) + w_a · A(t) + w_p · P(t) + w_s · Σ(t)

    Each component is bounded to [0, 100] and derived from public signals.
    Weights are applied at the worker side (censusComputeT).
    """
    V = min(100.0, kev.get("recent_7d", 0) * 5.0 + nvd.get("high_severity", 0) * 2.0)
    A = min(100.0, urlhaus.get("recent_urls", 0) / 20.0)
    P = min(100.0, 30.0 + (kev.get("recent_7d", 0) * 3.0 if kev.get("recent_7d", 0) > 5 else 0))
    today = datetime.now(timezone.utc).date().isoformat()
    sigma = 40.0 if today in ofac.get("last_modified", "") else 25.0
    return {"V": V, "A": A, "P": P, "sigma": sigma}


def weighted_T(components: Dict[str, float]) -> float:
    parts = [float(x.strip()) for x in WEIGHTS_RAW.split(",") if x.strip()]
    if len(parts) != 4 or sum(parts) <= 0:
        parts = [0.35, 0.30, 0.20, 0.15]
    total = sum(parts)
    w = [p / total for p in parts]
    T = (w[0] * components["V"] + w[1] * components["A"]
         + w[2] * components["P"] + w[3] * components["sigma"])
    return max(0.0, min(100.0, T))


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ─── Persistence ───────────────────────────────────────────────────────
def record_run(run_day: date, components: Dict[str, float], T: float,
               feeds_polled: int, cve_count: int, kev_count: int,
               entities_scanned: int, status: str = "ok",
               err: Optional[str] = None,
               artifact_path: Optional[str] = None) -> Optional[int]:
    """Insert census_runs row. Returns row id or None if Supabase absent."""
    client = sb()
    if not client:
        return None
    try:
        payload = {
            "run_day":          run_day.isoformat(),
            "completed_at":     datetime.now(timezone.utc).isoformat(),
            "feeds_polled":     feeds_polled,
            "entities_scanned": entities_scanned,
            "cve_count":        cve_count,
            "cve_kev_count":    kev_count,
            "T_computed":       round(T, 2),
            "components":       components,
            "worker_version":   "v0.7.9",
            "render_version":   "v0.7.9",
            "status":           status,
            "error_message":    err,
            "r2_artifact_path": artifact_path,
        }
        r = client.schema(CENSUS_SCHEMA).table("census_runs").insert(payload).execute()
        return r.data[0]["id"] if r.data else None
    except Exception as e:
        logger.error("Supabase insert failed: %s", e)
        return None


def refresh_daily_digest_view():
    """Refresh materialized view for the observatory. Silent no-op if RPC absent."""
    client = sb()
    if not client:
        return
    try:
        client.rpc("refresh_mv_daily_digest").execute()
    except Exception as e:
        logger.info("mv_daily_digest refresh skipped: %s", e)


# ─── Push digest to Worker /census/ingest ──────────────────────────────
async def push_to_worker(client: httpx.AsyncClient, digest: Dict[str, Any]):
    if not INTERNAL_TOKEN:
        logger.warning("CENSUS_INTERNAL_TOKEN not set; skipping worker push")
        return None
    try:
        r = await client.post(
            f"{CHAINSTATE_WORKER}/census/ingest",
            headers={
                "content-type": "application/json",
                # Preferred v0.7.9 rev 2 header. The Worker also accepts
                # the legacy x-chainstate-internal alias transitionally.
                "x-census-internal": INTERNAL_TOKEN,
            },
            json={
                "source": "metastate_quantum:census_daily",
                "components": digest["components"],
                "score_delta": 0,
                "day": digest["day"],
            },
            timeout=30,
        )
        return r.json()
    except Exception as e:
        logger.warning("Worker push failed: %s", e)
        return None


# ─── Main entry point ──────────────────────────────────────────────────
async def run_daily_census() -> Dict[str, Any]:
    """
    Nightly census tick. Returns a digest dict the Worker can pull via
    GET /census/daily.
    """
    logger.info("Starting daily census tick · v0.7.9")
    run_day = datetime.now(timezone.utc).date()

    async with httpx.AsyncClient(follow_redirects=True) as client:
        kev, nvd, urlhaus, ofac = await asyncio.gather(
            fetch_cisa_kev(client),
            fetch_nvd_recent(client),
            fetch_abusech_urlhaus(client),
            fetch_ofac_sdn(client),
            return_exceptions=False,
        )

        components = compute_components(kev, nvd, urlhaus, ofac)
        T = weighted_T(components)

        digest = {
            "day":              run_day.isoformat(),
            "T_computed":       round(T, 2),
            "components":       components,
            "posture":          "lockdown" if T >= THETA_LOCKDOWN
                                 else ("alert" if T >= THETA_ALERT else "nominal"),
            "cve_count":        nvd.get("total", 0),
            "cve_kev_count":    kev.get("total", 0),
            "entities_scanned": 0,
            "feeds": {
                "cisa_kev": kev,
                "nvd_cve":  nvd,
                "abusech":  urlhaus,
                "ofac":     ofac,
            },
            "artifact_bundle": {
                "day":         run_day.isoformat(),
                "T":           round(T, 2),
                "components":  components,
                "feed_status": {k: v.get("ok", False) for k, v in {
                    "kev": kev, "nvd": nvd, "urlhaus": urlhaus, "ofac": ofac
                }.items()},
                "hash":        sha256(json.dumps(
                    {"day": run_day.isoformat(), "T": T, "c": components},
                    sort_keys=True
                )),
            },
        }

        record_run(
            run_day=run_day,
            components=components,
            T=T,
            feeds_polled=4,
            cve_count=nvd.get("total", 0),
            kev_count=kev.get("total", 0),
            entities_scanned=0,
            status="ok" if all(x.get("ok") for x in [kev, nvd, urlhaus, ofac]) else "partial",
            err=None,
            artifact_path=f"census/{run_day.strftime('%Y/%m/%d')}/digest.json",
        )
        refresh_daily_digest_view()

        await push_to_worker(client, digest)

    logger.info("Daily census tick complete · T=%.2f · posture=%s",
                T, digest["posture"])
    return digest


# ─── Latest-digest reader for GET /census/daily on Render ──────────────
def latest_digest_from_supabase() -> Dict[str, Any]:
    """Called by app.py's GET /census/daily endpoint."""
    client = sb()
    if not client:
        return {"error": "supabase not configured"}
    try:
        r = client.schema(CENSUS_SCHEMA) \
            .table("census_runs") \
            .select("*") \
            .eq("status", "ok") \
            .order("run_day", desc=True) \
            .limit(1) \
            .execute()
        if not r.data:
            return {"error": "no runs recorded"}
        row = r.data[0]
        return {
            "day":              row["run_day"],
            "T_computed":       float(row["T_computed"] or 0),
            "components":       row.get("components") or {},
            "cve_count":        row.get("cve_count") or 0,
            "cve_kev_count":    row.get("cve_kev_count") or 0,
            "entities_scanned": row.get("entities_scanned") or 0,
            "artifact_bundle":  row.get("components") or {},
        }
    except Exception as e:
        logger.error("latest_digest read failed: %s", e)
        return {"error": str(e)}


if __name__ == "__main__":
    result = asyncio.run(run_daily_census())
    print(json.dumps(result, indent=2, default=str))
