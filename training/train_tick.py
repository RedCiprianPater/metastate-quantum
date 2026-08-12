"""
train_tick.py · daily training refinement orchestrator.

Called from perception.endpoint_train_tick with a Supabase client. Runs
the full refinement cycle:

    1. Fetch fresh samples from allowlisted corpora (fetch_open_source_data)
    2. Filter every sample through the anti-slop veto (rejects AI content)
    3. Train incremental EML tree updates on the accepted samples
    4. Upload new serialized models to R2 with monotonic version bump
    5. Record training_runs row in Supabase
    6. Return summary for the Worker to anchor on-chain

Bandwidth-bounded and CPU-bounded so a Render free-tier instance can
complete a tick in the allocated cron window (~4-6 min). The
TRAINING_MAX_SAMPLES_PER_TICK env caps daily intake.

All steps are best-effort; if a corpus source is unreachable, skips it
and continues. Never crashes; always returns a structured summary.
"""
from __future__ import annotations
import os
import time
import hashlib
import base64
import logging
from typing import Any, Optional
from datetime import datetime, timezone

from . import fetch_open_source_data
from . import train_texture_eml
from . import train_acoustic_eml
from . import upload_to_r2

log = logging.getLogger("perception.train_tick")

MAX_SAMPLES = int(os.environ.get("TRAINING_MAX_SAMPLES_PER_TICK", "500"))
SB_SCHEMA   = os.environ.get("SUPABASE_PERCEPTION_SCHEMA", "chainstate_perception")
ALLOWED_SOURCES = [
    s.strip() for s in os.environ.get(
        "TRAINING_ALLOWED_SOURCES", "dtd,fmd,dcase_free,audioset_free"
    ).split(",") if s.strip()
]


def _tbl(sb, name):
    """Route through the isolated chainstate_perception schema."""
    try:
        return sb.schema(SB_SCHEMA).table(name)
    except Exception:
        return sb.table(name)


async def run(
    trigger_source: str = "cron",
    worker_version: Optional[str] = None,
    supabase: Optional[Any] = None,
) -> dict:
    """Full training tick. Returns a structured summary for the worker."""
    started_at = time.time()
    started_iso = datetime.now(timezone.utc).isoformat()

    log.info(f"[train_tick] start · trigger={trigger_source} sources={ALLOWED_SOURCES}")

    # Insert 'started' row so we always have an audit record even if we crash
    run_row_id = None
    if supabase:
        try:
            resp = _tbl(supabase, "training_runs").insert({
                "started_at": started_iso,
                "trigger_source": trigger_source,
                "corpora": ALLOWED_SOURCES,
                "status": "started",
                "worker_version": worker_version,
            }).execute()
            if resp.data:
                run_row_id = resp.data[0].get("id")
        except Exception as e:
            log.warning(f"could not insert training_runs row: {e}")

    summary = {
        "started_iso": started_iso,
        "trigger_source": trigger_source,
        "samples_seen": 0,
        "samples_admitted": 0,
        "samples_rejected_ai": 0,
        "samples_rejected_quality": 0,
        "texture_model_version": None,
        "acoustic_model_version": None,
        "status": "ok",
        "corpora": ALLOWED_SOURCES,
    }

    try:
        # ─── 1. Fetch samples from allowlisted corpora ────────────────────
        fetched = await fetch_open_source_data.fetch_all(
            allowed_sources=ALLOWED_SOURCES,
            max_samples=MAX_SAMPLES,
            supabase=supabase,
        )
        summary["samples_seen"] = fetched["seen"]
        summary["samples_admitted"] = fetched["admitted"]
        summary["samples_rejected_ai"] = fetched["rejected_ai"]
        summary["samples_rejected_quality"] = fetched["rejected_quality"]

        image_samples = fetched.get("image_samples", [])
        audio_samples = fetched.get("audio_samples", [])
        spectral_samples = fetched.get("spectral_samples", [])

        # ─── 2. Train texture EML if enough image samples ─────────────────
        if image_samples:
            log.info(f"training texture EML on {len(image_samples)} samples")
            tex_result = train_texture_eml.train(image_samples)
            if tex_result.get("model_path"):
                # 3. Upload to R2 with monotonic version bump
                tex_version = _monotonic_version("texture")
                r2_url = upload_to_r2.upload(
                    local_path=tex_result["model_path"],
                    key=f"texture/{tex_version}/texture_eml.pkl",
                    also_key="texture_eml_current.pkl",
                )
                summary["texture_model_version"] = tex_version
                summary["texture_model_r2_url"] = r2_url
                summary["texture_holdout_r2"] = tex_result.get("holdout_r2")

        # ─── Train acoustic EML if enough audio samples ───────────────────
        if audio_samples:
            log.info(f"training acoustic EML on {len(audio_samples)} samples")
            ac_result = train_acoustic_eml.train(audio_samples)
            if ac_result.get("model_path"):
                ac_version = _monotonic_version("acoustic")
                r2_url = upload_to_r2.upload(
                    local_path=ac_result["model_path"],
                    key=f"acoustic/{ac_version}/acoustic_eml.pkl",
                    also_key="acoustic_eml_current.pkl",
                )
                summary["acoustic_model_version"] = ac_version
                summary["acoustic_model_r2_url"] = r2_url
                summary["acoustic_holdout_acc"] = ac_result.get("holdout_accuracy")

        # ─── Insert new spectral priors into Supabase (already verified) ──
        if spectral_samples and supabase:
            inserted = 0
            for sample in spectral_samples:
                try:
                    _tbl(supabase, "perception_priors").insert(sample).execute()
                    inserted += 1
                except Exception:
                    pass   # duplicates by payload_hash are fine
            summary["hyperspectral_prior_size"] = inserted

    except Exception as e:
        log.exception("[train_tick] failed")
        summary["status"] = "failed"
        summary["error"] = str(e)[:400]

    # ─── Finalize: update training_runs row ──────────────────────────────
    duration = time.time() - started_at
    summary["duration_s"] = round(duration, 2)
    summary["completed_iso"] = datetime.now(timezone.utc).isoformat()

    if supabase and run_row_id:
        try:
            _tbl(supabase, "training_runs").update({
                "completed_at": summary["completed_iso"],
                "samples_seen": summary["samples_seen"],
                "samples_admitted": summary["samples_admitted"],
                "samples_rejected_ai": summary["samples_rejected_ai"],
                "samples_rejected_quality": summary["samples_rejected_quality"],
                "texture_model_version": summary.get("texture_model_version"),
                "texture_model_r2_url": summary.get("texture_model_r2_url"),
                "texture_holdout_r2": summary.get("texture_holdout_r2"),
                "acoustic_model_version": summary.get("acoustic_model_version"),
                "acoustic_model_r2_url": summary.get("acoustic_model_r2_url"),
                "acoustic_holdout_acc": summary.get("acoustic_holdout_acc"),
                "hyperspectral_prior_size": summary.get("hyperspectral_prior_size"),
                "status": summary["status"],
                "error_detail": summary.get("error"),
            }).eq("id", run_row_id).execute()
        except Exception as e:
            log.warning(f"could not update training_runs row: {e}")

    log.info(f"[train_tick] done · status={summary['status']} · duration={duration:.1f}s")
    return summary


def _monotonic_version(kind: str) -> str:
    """Version string based on UTC date: YYYY.MM.DD.N where N increments
    if there are multiple ticks on the same day."""
    today = datetime.now(timezone.utc).strftime("%Y.%m.%d")
    # Simple: use hour to disambiguate; production would query Supabase for the count
    hh = datetime.now(timezone.utc).strftime("%H%M")
    return f"{today}.{hh}"
