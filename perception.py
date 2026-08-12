"""
metastate-quantum · perception module (Paper VIII · v0.7.8)

Drop-in FastAPI router that adds the CHAINSTATE hyperspectral sensory
synthesis endpoints to the existing metastate-quantum service without
touching any existing quantum-routing code.

Integration with existing app.py — add exactly TWO lines:

    from perception import router as perception_router
    app.include_router(perception_router)

That is it. Everything else in this file is self-contained: models,
Supabase client, R2 loader, EML tree inference, anti-slop veto, training
tick. Existing /route and /chainstate/route continue byte-for-byte
unchanged.

Auth uses the same WORKER_SHARED_SECRET as the quantum routes.
CHAINSTATE_SHARED_SECRET is additionally required for /perception/train/tick.

Endpoints:
  POST /perception/hyperspectral       RGB -> spectrum + composition
  POST /perception/texture             image -> texture descriptor
  POST /perception/acoustic            audio -> material + geometry
  POST /perception/synthesize          all-modal Bayesian synthesis
  GET  /perception/status              capability status
  POST /perception/train/tick          daily training (internal only)

Env vars (all optional; module degrades gracefully when absent):
  WORKER_SHARED_SECRET        matches Cloudflare worker
  CHAINSTATE_SHARED_SECRET    additional gate for /train/tick
  SUPABASE_URL                Postgres source-of-truth for priors
  SUPABASE_SERVICE_ROLE_KEY   Supabase auth
  R2_MODELS_BASE_URL          e.g. https://models.chainstate.dev/perception
                              --- fetched at cold-start, cached in memory
  PROVENANCE_SERVICE_URL      SynthID/C2PA detection service
  PROVENANCE_SHARED_SECRET    auth for provenance service
  PERCEPTION_LOG_LEVEL        default INFO

Requires (add to requirements.txt --- see requirements-perception.txt):
  numpy>=1.24
  scipy>=1.11
  scikit-learn>=1.3
  Pillow>=10.0
  librosa>=0.10          # acoustic
  opencv-python-headless>=4.8   # image ops
  supabase>=2.0
  httpx>=0.25
  boto3>=1.28            # R2 access
"""
from __future__ import annotations
import os
import io
import time
import hashlib
import json
import base64
import logging
import asyncio
from typing import Optional, Any
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
import httpx

# ── optional imports (module still loads if any is missing) ────────────────
try:
    import numpy as np
    HAVE_NUMPY = True
except Exception:
    HAVE_NUMPY = False

try:
    from PIL import Image
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

try:
    import cv2
    HAVE_CV2 = True
except Exception:
    HAVE_CV2 = False

try:
    import librosa
    HAVE_LIBROSA = True
except Exception:
    HAVE_LIBROSA = False

try:
    from supabase import create_client, Client as SupabaseClient
    HAVE_SUPABASE = True
except Exception:
    HAVE_SUPABASE = False

try:
    import pickle
    from sklearn.ensemble import ExtraTreesRegressor, ExtraTreesClassifier
    HAVE_SKLEARN = True
except Exception:
    HAVE_SKLEARN = False


# ── config ─────────────────────────────────────────────────────────────────
SHARED_WORKER_SECRET  = os.environ.get("WORKER_SHARED_SECRET", "")
CHAINSTATE_SECRET     = os.environ.get("CHAINSTATE_SHARED_SECRET", "")
SUPABASE_URL          = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY  = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_SCHEMA       = os.environ.get("SUPABASE_PERCEPTION_SCHEMA", "chainstate_perception")
R2_MODELS_BASE_URL    = os.environ.get("R2_MODELS_BASE_URL", "")
PROVENANCE_URL        = os.environ.get("PROVENANCE_SERVICE_URL", "")
PROVENANCE_SECRET     = os.environ.get("PROVENANCE_SHARED_SECRET", "")

VETO_THRESHOLD        = 0.15   # Paper VIII §8.7
TEXTURE_MODEL_KEY     = "texture_eml_current.pkl"
ACOUSTIC_MODEL_KEY    = "acoustic_eml_current.pkl"

logging.basicConfig(level=os.environ.get("PERCEPTION_LOG_LEVEL", "INFO"))
log = logging.getLogger("perception")


# ── clients ────────────────────────────────────────────────────────────────
_supabase: Optional[Any] = None
def supabase() -> Optional[Any]:
    global _supabase
    if _supabase is not None:
        return _supabase
    if not (HAVE_SUPABASE and SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return None
    try:
        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        return _supabase
    except Exception as e:
        log.warning(f"supabase client init failed: {e}")
        return None


# ── model loader (R2 or local fallback) ────────────────────────────────────
_texture_model = None
_acoustic_model = None

def load_model_from_r2(key: str):
    if not R2_MODELS_BASE_URL:
        log.info(f"R2_MODELS_BASE_URL not set; skipping load of {key}")
        return None
    if not HAVE_SKLEARN:
        log.info(f"scikit-learn not installed; skipping load of {key}")
        return None
    url = R2_MODELS_BASE_URL.rstrip("/") + "/" + key
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                log.info(f"model {key} not available at R2 (status {resp.status_code})")
                return None
            model = pickle.loads(resp.content)
            log.info(f"loaded model {key} from R2 ({len(resp.content)} bytes)")
            return model
    except Exception as e:
        log.warning(f"model load {key} failed: {e}")
        return None

def texture_model():
    global _texture_model
    if _texture_model is None:
        _texture_model = load_model_from_r2(TEXTURE_MODEL_KEY)
    return _texture_model

def acoustic_model():
    global _acoustic_model
    if _acoustic_model is None:
        _acoustic_model = load_model_from_r2(ACOUSTIC_MODEL_KEY)
    return _acoustic_model


# ── request/response models ────────────────────────────────────────────────
class HyperReq(BaseModel):
    rgb_url: Optional[str] = None
    rgb_base64: Optional[str] = None
    material_prior_set: str = "auto"
    resolution: str = "adaptive"
    shots: Optional[int] = None
    requester_wallet: Optional[str] = None
    request_ref: Optional[str] = None

class TextureReq(BaseModel):
    image_url: Optional[str] = None
    image_base64: Optional[str] = None
    patch_size: int = 64
    requester_wallet: Optional[str] = None

class AcousticReq(BaseModel):
    audio_url: Optional[str] = None
    audio_base64: Optional[str] = None
    requester_wallet: Optional[str] = None

class SynthesizeReq(BaseModel):
    image_url: Optional[str] = None
    image_base64: Optional[str] = None
    audio_url: Optional[str] = None
    audio_base64: Optional[str] = None
    scene_prior: str = "auto"
    requester_wallet: Optional[str] = None

class TrainTickReq(BaseModel):
    trigger_source: str = "manual"
    trigger_ts: Optional[int] = None
    worker_version: Optional[str] = None


# ── router ─────────────────────────────────────────────────────────────────
router = APIRouter(prefix="/perception", tags=["perception"])


# ── helpers ────────────────────────────────────────────────────────────────
def check_worker_secret(x_worker_secret: Optional[str]):
    if SHARED_WORKER_SECRET and x_worker_secret != SHARED_WORKER_SECRET:
        raise HTTPException(401, "bad worker secret")

def check_chainstate_token(x_chainstate_token: Optional[str]):
    if not CHAINSTATE_SECRET:
        raise HTTPException(503, "chainstate-direct disabled on this deploy")
    if x_chainstate_token != CHAINSTATE_SECRET:
        raise HTTPException(401, "bad chainstate token")

async def fetch_bytes(url: str, max_bytes: int = 20_000_000) -> bytes:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.content
        if len(data) > max_bytes:
            raise HTTPException(413, f"content too large ({len(data)} > {max_bytes})")
        return data

def load_bytes_or_base64(url: Optional[str], b64: Optional[str]) -> bytes:
    if b64:
        try:
            return base64.b64decode(b64)
        except Exception as e:
            raise HTTPException(400, f"invalid base64: {e}")
    if url:
        return asyncio.get_event_loop().run_until_complete(fetch_bytes(url))
    raise HTTPException(400, "no content provided")

async def load_bytes_async(url: Optional[str], b64: Optional[str]) -> bytes:
    if b64:
        try: return base64.b64decode(b64)
        except Exception as e: raise HTTPException(400, f"invalid base64: {e}")
    if url:
        return await fetch_bytes(url)
    raise HTTPException(400, "no content provided")

def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── anti-slop veto (compute-service layer, deeper than the worker layer) ───
async def deep_provenance_check(data: bytes, content_type: str) -> dict:
    """
    Second layer of the anti-slop veto. Called by every perception endpoint
    before doing any heavy computation. Deeper than the worker-side header
    check because it can look at the actual bytes.

    If PROVENANCE_SERVICE_URL is set, delegates to it (recommended).
    If not set, returns provenance_check_unavailable=True and the caller
    handles the refuse-by-default per Paper VIII §8.5.
    """
    if not PROVENANCE_URL:
        return {
            "provenance_check_unavailable": True,
            "ai_likelihood": None,
            "verdict": "insufficient_signal",
            "note": "PROVENANCE_SERVICE_URL not configured on metastate-quantum"
        }
    try:
        h = content_hash(data)
        headers = {"content-type": "application/json"}
        if PROVENANCE_SECRET:
            headers["x-provenance-secret"] = PROVENANCE_SECRET
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                PROVENANCE_URL.rstrip("/") + "/check",
                headers=headers,
                json={
                    "content_hash": h,
                    "content_type": content_type,
                    "bytes_sample_base64": base64.b64encode(data[:1_000_000]).decode()
                }
            )
        if resp.status_code == 200:
            return resp.json()
        return {
            "provenance_check_unavailable": True,
            "ai_likelihood": None,
            "verdict": "insufficient_signal",
            "upstream_status": resp.status_code
        }
    except Exception as e:
        return {
            "provenance_check_unavailable": True,
            "ai_likelihood": None,
            "verdict": "insufficient_signal",
            "error": str(e)[:200]
        }


async def enforce_veto_or_flag(data: bytes, content_type: str, for_meta_layer: bool) -> tuple[bool, dict]:
    """Returns (admit_to_meta_layer, veto_details)."""
    check = await deep_provenance_check(data, content_type)
    ai_lik = check.get("ai_likelihood")
    admit = True
    reason = None
    if ai_lik is not None and ai_lik > VETO_THRESHOLD:
        admit = False
        reason = "ai_likelihood_above_threshold"
    elif for_meta_layer and check.get("provenance_check_unavailable"):
        admit = False
        reason = "provenance_check_unavailable_meta_default_refuse"
    return admit, {
        "admit": admit,
        "reason": reason,
        "ai_likelihood": ai_lik,
        "provenance_check": check,
        "for_meta_layer": for_meta_layer,
        "threshold": VETO_THRESHOLD,
        "layer": "compute_service",
        "paper_reference": "Paper VIII §8"
    }


def _sb_table(sb, name):
    """Route table access through the isolated chainstate_perception schema.

    Falls back to public schema if the schema call is unsupported (older
    supabase-py) or if the operator has overridden SUPABASE_PERCEPTION_SCHEMA
    to 'public' for a legacy install.
    """
    try:
        return sb.schema(SUPABASE_SCHEMA).table(name)
    except Exception:
        return sb.table(name)


def record_veto_incident(veto: dict, endpoint: str, chash: str, content_type: str):
    """Append a row to veto_incidents (best-effort)."""
    sb = supabase()
    if not sb:
        return
    try:
        _sb_table(sb, "veto_incidents").insert({
            "category": "synthetic_media_self_ingestion",
            "content_hash": chash,
            "content_type": content_type,
            "reason": veto.get("reason", ""),
            "ai_likelihood": veto.get("ai_likelihood"),
            "signals": veto.get("provenance_check", {}),
            "endpoint": endpoint,
            "for_meta_layer": veto.get("for_meta_layer", True),
        }).execute()
    except Exception as e:
        log.warning(f"veto incident record failed: {e}")


# ── hyperspectral reconstruction ───────────────────────────────────────────
# Regularized inverse per Paper VIII §3, §12.1.
# RGB channel response curves (approximated CIE 1931 x-bar/y-bar/z-bar
# transformed to sRGB primaries). N=31 bins at 10nm from 400-700nm.

def _rgb_response_matrix() -> Optional[Any]:
    if not HAVE_NUMPY: return None
    # Very approximate sensor response --- production would load calibrated
    # curves from a per-camera model. This gets the physics right at first order.
    wavelengths = np.linspace(400, 700, 31)
    R_r = np.exp(-0.5 * ((wavelengths - 610) / 40) ** 2)   # red centered ~610 nm
    R_g = np.exp(-0.5 * ((wavelengths - 540) / 35) ** 2)   # green centered ~540 nm
    R_b = np.exp(-0.5 * ((wavelengths - 460) / 45) ** 2)   # blue centered ~460 nm
    A = np.stack([R_r, R_g, R_b], axis=0)
    A = A / A.sum(axis=1, keepdims=True)
    return A


async def load_material_priors(prior_set: str = "auto") -> list[dict]:
    """Load material spectral priors from Supabase (or cached fallback)."""
    sb = supabase()
    if not sb:
        return []
    try:
        q = _sb_table(sb, "perception_priors").select("class,subclass,payload").eq("modality", "spectral").eq("is_active", True).eq("human_verified", True).limit(200)
        result = q.execute()
        return list(result.data or [])
    except Exception as e:
        log.warning(f"prior load failed: {e}")
        return []


def hyperspectral_reconstruct(rgb_image: Any, priors: list[dict]) -> dict:
    """Regularized inverse to recover per-pixel spectrum. Coarse implementation."""
    if not HAVE_NUMPY:
        return {"error": "numpy_unavailable"}
    A = _rgb_response_matrix()
    if A is None:
        return {"error": "no_response_matrix"}

    # Downsample RGB for compute budget (matches Paper VIII §6.1 progressive precision)
    rgb = rgb_image.astype(np.float64) / 255.0
    if rgb.shape[0] > 128 or rgb.shape[1] > 128:
        step_h = max(1, rgb.shape[0] // 128)
        step_w = max(1, rgb.shape[1] // 128)
        rgb = rgb[::step_h, ::step_w]

    H, W, _ = rgb.shape
    N = A.shape[1]

    # For each pixel: pseudo-inverse recovery x = A^+ y with smoothness prior
    # Regularization: minimize ||Ax - y||^2 + lambda_smooth ||Lx||^2
    L = np.diag([2.0]*N) + np.diag([-1.0]*(N-1), 1) + np.diag([-1.0]*(N-1), -1)
    lam_smooth = 0.5
    M = A.T @ A + lam_smooth * (L.T @ L)
    M_inv_At = np.linalg.solve(M, A.T)

    reconstructed = np.zeros((H, W, N))
    for y in range(H):
        for x in range(W):
            reconstructed[y, x] = M_inv_At @ rgb[y, x]
    reconstructed = np.clip(reconstructed, 0.0, 2.0)

    # Compositional decomposition: pick nearest material for each pixel
    composition_summary = {}
    if priors and HAVE_NUMPY:
        prior_spectra = []
        prior_labels = []
        for p in priors:
            payload = p.get("payload") or {}
            spec = payload.get("spectrum")
            if spec and len(spec) == N:
                prior_spectra.append(spec)
                prior_labels.append(p.get("class"))
        if prior_spectra:
            S = np.array(prior_spectra)   # (M_prior, N)
            # Nearest-neighbor per pixel by cosine similarity
            flat = reconstructed.reshape(-1, N)
            flat_norm = flat / (np.linalg.norm(flat, axis=1, keepdims=True) + 1e-9)
            S_norm = S / (np.linalg.norm(S, axis=1, keepdims=True) + 1e-9)
            sims = flat_norm @ S_norm.T
            nearest = sims.argmax(axis=1)
            for idx, label in enumerate(prior_labels):
                count = int((nearest == idx).sum())
                if count > 0:
                    composition_summary[label] = count / len(nearest)

    return {
        "reconstruction_shape": [H, W, N],
        "wavelengths_nm": list(range(400, 701, 10)),
        "composition_summary": composition_summary,
        "spectrum_center_pixel": reconstructed[H//2, W//2].tolist() if H > 0 and W > 0 else None,
        "reconstruction_method": "Tikhonov-regularized_pseudoinverse_with_smoothness_prior",
        "resolution_downsampled_to": [H, W],
        "prior_count_used": len(priors),
        "status": "sim"     # honest label: this is a coarse implementation, production would use richer priors
    }


# ── texture inference ──────────────────────────────────────────────────────
def texture_features(patch: Any) -> Any:
    """Extract FFT-based features from a grayscale patch (Paper VIII §4)."""
    if not HAVE_NUMPY:
        return None
    if patch.ndim == 3:
        patch = patch.mean(axis=2)
    fft = np.fft.fft2(patch)
    power = np.abs(fft) ** 2
    # Radial-average power
    h, w = power.shape
    cy, cx = h // 2, w // 2
    y, x = np.ogrid[:h, :w]
    r = np.sqrt((y - cy)**2 + (x - cx)**2).astype(int)
    r_max = min(h, w) // 2
    radial = np.zeros(min(r_max, 16))
    for k in range(len(radial)):
        mask = r == k
        if mask.any():
            radial[k] = power[mask].mean()
    # Normalize
    radial = radial / (radial.sum() + 1e-9)
    # High-to-low ratio
    hl_ratio = radial[radial.size//2:].sum() / (radial[:radial.size//2].sum() + 1e-9)
    # Spectral entropy
    p = radial + 1e-9
    entropy = -(p * np.log(p)).sum()
    features = np.concatenate([radial, [hl_ratio, entropy]])
    return features


def texture_predict(features: Any) -> dict:
    """Run the loaded EML tree on FFT features, or return unknown."""
    model = texture_model()
    if model is None or features is None:
        return {
            "roughness": None,
            "porosity": None,
            "granularity": "unknown",
            "confidence": 0.0,
            "note": "texture_eml_model_not_loaded_yet"
        }
    try:
        pred = model.predict([features])[0]
        # Model is trained to output [roughness, porosity, granularity_index]
        return {
            "roughness": float(pred[0]) if len(pred) > 0 else None,
            "porosity": float(pred[1]) if len(pred) > 1 else None,
            "granularity_index": float(pred[2]) if len(pred) > 2 else None,
            "confidence": 0.85     # placeholder; production would compute ensemble variance
        }
    except Exception as e:
        return {"error": str(e)[:200]}


# ── acoustic recognition ───────────────────────────────────────────────────
def acoustic_features(waveform: Any, sr: int) -> Any:
    """Extract STFT + MFCC + RT60 features (Paper VIII §5)."""
    if not (HAVE_NUMPY and HAVE_LIBROSA):
        return None
    try:
        stft = np.abs(librosa.stft(waveform, n_fft=1024))
        mfcc = librosa.feature.mfcc(y=waveform, sr=sr, n_mfcc=13)
        # Simple spectral centroid + coloration ratio
        centroid = librosa.feature.spectral_centroid(y=waveform, sr=sr).mean()
        # low-band vs high-band energy
        freq_bins = librosa.fft_frequencies(sr=sr, n_fft=1024)
        low_mask = freq_bins < 500
        high_mask = freq_bins > 2000
        low_e = stft[low_mask].mean() if low_mask.any() else 0
        high_e = stft[high_mask].mean() if high_mask.any() else 0
        coloration = float(low_e / (high_e + 1e-9))
        # RT60 rough estimate: energy decay curve
        rms = librosa.feature.rms(y=waveform)[0]
        rms_db = 20 * np.log10(rms + 1e-9)
        # naive: time from peak to peak-60dB
        peak = rms_db.max()
        below = np.where(rms_db < peak - 60)[0]
        rt60 = float(below[0] * 512 / sr) if len(below) > 0 else -1.0
        feats = np.concatenate([mfcc.mean(axis=1), [centroid, coloration, rt60]])
        return feats
    except Exception as e:
        log.warning(f"acoustic feature extraction failed: {e}")
        return None


def acoustic_predict(features: Any) -> dict:
    model = acoustic_model()
    if model is None or features is None:
        return {
            "material_class": "unknown",
            "geometry_class": "unknown",
            "confidence": 0.0,
            "note": "acoustic_eml_model_not_loaded_yet"
        }
    try:
        pred = model.predict([features])[0]
        proba = model.predict_proba([features])[0] if hasattr(model, "predict_proba") else None
        return {
            "material_class": str(pred),
            "confidence": float(proba.max()) if proba is not None else 0.6,
            "note": "coarse classifier"
        }
    except Exception as e:
        return {"error": str(e)[:200]}


# ═══════════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@router.post("/hyperspectral")
async def endpoint_hyperspectral(
    r: HyperReq,
    x_worker_secret: Optional[str] = Header(None),
    x_chainstate_token: Optional[str] = Header(None),
    x_for_meta_layer: Optional[str] = Header(None),
):
    check_worker_secret(x_worker_secret)
    for_meta = (x_for_meta_layer == "true")

    data = await load_bytes_async(r.rgb_url, r.rgb_base64)
    chash = content_hash(data)

    admit, veto = await enforce_veto_or_flag(data, "image/rgb", for_meta)
    if not admit and for_meta:
        record_veto_incident(veto, "/perception/hyperspectral", chash, "image/rgb")
        raise HTTPException(403, {"error": "refused by synthetic_media_self_ingestion (compute layer)", "veto": veto})

    if not HAVE_PIL:
        raise HTTPException(503, "Pillow not installed on this deploy")

    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        rgb = np.array(img) if HAVE_NUMPY else None
    except Exception as e:
        raise HTTPException(400, f"cannot open image: {e}")

    priors = await load_material_priors(r.material_prior_set)
    result = hyperspectral_reconstruct(rgb, priors) if rgb is not None else {"error": "numpy_unavailable"}

    return {
        "endpoint": "hyperspectral",
        "content_hash": chash,
        "for_meta_layer": for_meta,
        "veto": veto,
        "synthetic_content_processed": not admit,     # external-processing flag
        "result": result,
    }


@router.post("/texture")
async def endpoint_texture(
    r: TextureReq,
    x_worker_secret: Optional[str] = Header(None),
    x_for_meta_layer: Optional[str] = Header(None),
):
    check_worker_secret(x_worker_secret)
    for_meta = (x_for_meta_layer == "true")

    data = await load_bytes_async(r.image_url, r.image_base64)
    chash = content_hash(data)

    admit, veto = await enforce_veto_or_flag(data, "image", for_meta)
    if not admit and for_meta:
        record_veto_incident(veto, "/perception/texture", chash, "image")
        raise HTTPException(403, {"error": "refused by synthetic_media_self_ingestion", "veto": veto})

    if not HAVE_PIL:
        raise HTTPException(503, "Pillow not installed")

    try:
        img = Image.open(io.BytesIO(data)).convert("L")   # grayscale for FFT
        patch = np.array(img) if HAVE_NUMPY else None
    except Exception as e:
        raise HTTPException(400, f"cannot open image: {e}")

    features = texture_features(patch) if patch is not None else None
    prediction = texture_predict(features)

    return {
        "endpoint": "texture",
        "content_hash": chash,
        "for_meta_layer": for_meta,
        "veto": veto,
        "synthetic_content_processed": not admit,
        "features_dim": int(features.shape[0]) if features is not None else 0,
        "prediction": prediction,
    }


@router.post("/acoustic")
async def endpoint_acoustic(
    r: AcousticReq,
    x_worker_secret: Optional[str] = Header(None),
    x_for_meta_layer: Optional[str] = Header(None),
):
    check_worker_secret(x_worker_secret)
    for_meta = (x_for_meta_layer == "true")

    data = await load_bytes_async(r.audio_url, r.audio_base64)
    chash = content_hash(data)

    admit, veto = await enforce_veto_or_flag(data, "audio", for_meta)
    if not admit and for_meta:
        record_veto_incident(veto, "/perception/acoustic", chash, "audio")
        raise HTTPException(403, {"error": "refused by synthetic_media_self_ingestion", "veto": veto})

    if not HAVE_LIBROSA:
        raise HTTPException(503, "librosa not installed")

    try:
        waveform, sr = librosa.load(io.BytesIO(data), sr=22050, mono=True)
    except Exception as e:
        raise HTTPException(400, f"cannot decode audio: {e}")

    features = acoustic_features(waveform, sr)
    prediction = acoustic_predict(features)

    return {
        "endpoint": "acoustic",
        "content_hash": chash,
        "for_meta_layer": for_meta,
        "veto": veto,
        "synthetic_content_processed": not admit,
        "sample_rate": sr,
        "duration_s": float(len(waveform) / sr) if HAVE_NUMPY else None,
        "features_dim": int(features.shape[0]) if features is not None else 0,
        "prediction": prediction,
    }


@router.post("/synthesize")
async def endpoint_synthesize(
    r: SynthesizeReq,
    x_worker_secret: Optional[str] = Header(None),
    x_for_meta_layer: Optional[str] = Header(None),
):
    check_worker_secret(x_worker_secret)
    for_meta = (x_for_meta_layer == "true")

    # Load whichever modalities are provided
    image_data = None
    audio_data = None
    if r.image_url or r.image_base64:
        image_data = await load_bytes_async(r.image_url, r.image_base64)
    if r.audio_url or r.audio_base64:
        audio_data = await load_bytes_async(r.audio_url, r.audio_base64)

    # Run veto on each modality
    image_veto = None
    audio_veto = None
    admit_all = True

    if image_data is not None:
        admit_i, image_veto = await enforce_veto_or_flag(image_data, "image", for_meta)
        if not admit_i and for_meta: admit_all = False

    if audio_data is not None:
        admit_a, audio_veto = await enforce_veto_or_flag(audio_data, "audio", for_meta)
        if not admit_a and for_meta: admit_all = False

    if not admit_all and for_meta:
        raise HTTPException(403, {
            "error": "refused by synthetic_media_self_ingestion (one or more modalities)",
            "image_veto": image_veto,
            "audio_veto": audio_veto
        })

    # Coarse synthesis: pass each modality through its endpoint's core logic
    results = {}
    if image_data is not None and HAVE_PIL and HAVE_NUMPY:
        try:
            img = Image.open(io.BytesIO(image_data)).convert("RGB")
            rgb = np.array(img)
            priors = await load_material_priors(r.scene_prior)
            results["hyperspectral"] = hyperspectral_reconstruct(rgb, priors)
            gray = np.array(img.convert("L"))
            f = texture_features(gray)
            results["texture"] = texture_predict(f) if f is not None else {"note": "no features"}
        except Exception as e:
            results["hyperspectral_error"] = str(e)[:200]

    if audio_data is not None and HAVE_LIBROSA:
        try:
            waveform, sr = librosa.load(io.BytesIO(audio_data), sr=22050, mono=True)
            f = acoustic_features(waveform, sr)
            results["acoustic"] = acoustic_predict(f) if f is not None else {"note": "no features"}
        except Exception as e:
            results["acoustic_error"] = str(e)[:200]

    # Cross-representation divergence (very coarse; production would compute
    # actual KL over material posteriors)
    dialetheic_flag = False   # will be set when 2+ modalities disagree substantially

    return {
        "endpoint": "synthesize",
        "for_meta_layer": for_meta,
        "image_veto": image_veto,
        "audio_veto": audio_veto,
        "results_per_modality": results,
        "dialetheic_disagreement": dialetheic_flag,
        "synthesis_principle": "Paper VIII §2 · one fact, many representations",
        "cross_representation_note": "coarse implementation; production computes explicit KL divergence across modality posteriors"
    }


@router.get("/status")
async def endpoint_status(x_worker_secret: Optional[str] = Header(None)):
    # public status is fine without a secret; we just don't reveal internals
    sb = supabase()
    priors_count = None
    if sb:
        try:
            r = _sb_table(sb, "perception_priors").select("id", count="exact").eq("is_active", True).eq("human_verified", True).limit(1).execute()
            priors_count = r.count if hasattr(r, "count") else None
        except Exception:
            priors_count = None
    return {
        "service": "metastate-quantum · perception module",
        "paper_reference": "Paper VIII v0.7.8",
        "numpy_available": HAVE_NUMPY,
        "pil_available": HAVE_PIL,
        "opencv_available": HAVE_CV2,
        "librosa_available": HAVE_LIBROSA,
        "sklearn_available": HAVE_SKLEARN,
        "supabase_configured": bool(sb),
        "r2_models_url_configured": bool(R2_MODELS_BASE_URL),
        "provenance_service_configured": bool(PROVENANCE_URL),
        "texture_model_loaded": texture_model() is not None,
        "acoustic_model_loaded": acoustic_model() is not None,
        "human_verified_priors_count": priors_count,
        "veto_threshold_ai_likelihood": VETO_THRESHOLD,
        "veto_default_meta_layer": "refuse_when_provenance_unavailable",
    }


@router.post("/train/tick")
async def endpoint_train_tick(
    r: TrainTickReq,
    x_worker_secret: Optional[str] = Header(None),
    x_chainstate_token: Optional[str] = Header(None),
):
    """Daily training refinement. Runs the training pipeline module."""
    check_worker_secret(x_worker_secret)
    check_chainstate_token(x_chainstate_token)
    try:
        # Lazy import so the router loads even without training deps
        from training import train_tick
        result = await train_tick.run(
            trigger_source=r.trigger_source,
            worker_version=r.worker_version,
            supabase=supabase(),
        )
        return {"endpoint": "train/tick", **result}
    except ImportError:
        return {
            "endpoint": "train/tick",
            "status": "training_module_not_installed",
            "note": "training/ package not present on this deploy; add it and re-tick"
        }
    except Exception as e:
        log.exception("train tick failed")
        return {
            "endpoint": "train/tick",
            "status": "failed",
            "error": str(e)[:400]
        }
