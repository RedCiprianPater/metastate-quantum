"""
train_texture_eml.py · train the texture EML tree ensemble.

Input: list of {image_base64, texture_ground_truth: {roughness, porosity,
granularity_index}}. Output: pickle path of a trained
sklearn.ensemble.ExtraTreesRegressor.

Uses the same FFT-based feature extraction as perception.texture_features
so training-time features match inference-time features exactly.
"""
from __future__ import annotations
import os
import io
import pickle
import tempfile
import logging
from typing import Any

log = logging.getLogger("perception.train.texture")

try:
    import numpy as np
    from PIL import Image
    from sklearn.ensemble import ExtraTreesRegressor
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import r2_score
    HAVE_DEPS = True
except Exception:
    HAVE_DEPS = False


def _fft_features(gray: "np.ndarray") -> "np.ndarray":
    fft = np.fft.fft2(gray.astype(np.float64))
    power = np.abs(fft) ** 2
    h, w = power.shape
    cy, cx = h // 2, w // 2
    y, x = np.ogrid[:h, :w]
    r = np.sqrt((y - cy) ** 2 + (x - cx) ** 2).astype(int)
    r_max = min(h, w) // 2
    radial = np.zeros(min(r_max, 16))
    for k in range(len(radial)):
        mask = r == k
        if mask.any():
            radial[k] = power[mask].mean()
    radial = radial / (radial.sum() + 1e-9)
    hl_ratio = radial[radial.size // 2:].sum() / (radial[:radial.size // 2].sum() + 1e-9)
    p = radial + 1e-9
    entropy = -(p * np.log(p)).sum()
    return np.concatenate([radial, [hl_ratio, entropy]])


def train(samples: list[dict]) -> dict:
    """Train the texture EML tree on a list of samples.

    Each sample: {"image_base64": "...", "roughness": float, "porosity":
    float, "granularity_index": float}. Returns dict with model_path
    (writable temp file the caller uploads to R2) and holdout metrics.
    """
    if not HAVE_DEPS:
        return {"error": "sklearn/numpy/PIL not available on this deploy"}
    if not samples or len(samples) < 20:
        return {"error": "insufficient samples", "count": len(samples), "min_required": 20}

    import base64
    X_list = []
    y_list = []
    kept = 0
    dropped = 0

    for s in samples:
        try:
            data = base64.b64decode(s["image_base64"])
            img = Image.open(io.BytesIO(data)).convert("L")
            gray = np.array(img)
            # Resize to a fixed patch size for stable features
            if gray.shape[0] > 128 or gray.shape[1] > 128:
                img = img.resize((128, 128))
                gray = np.array(img)
            feats = _fft_features(gray)
            targets = [
                float(s.get("roughness", 0.5)),
                float(s.get("porosity", 0.5)),
                float(s.get("granularity_index", 0.5)),
            ]
            X_list.append(feats)
            y_list.append(targets)
            kept += 1
        except Exception:
            dropped += 1
            continue

    if kept < 20:
        return {"error": "insufficient valid samples after feature extraction", "kept": kept, "dropped": dropped}

    X = np.array(X_list)
    y = np.array(y_list)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.15, random_state=42)

    log.info(f"[texture] training ExtraTreesRegressor on {X_train.shape} samples")
    model = ExtraTreesRegressor(
        n_estimators=100,
        max_depth=20,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    r2 = float(r2_score(y_test, y_pred, multioutput="variance_weighted"))

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pkl")
    pickle.dump(model, tmp)
    tmp.close()

    log.info(f"[texture] trained · R²={r2:.4f} · kept={kept} · dropped={dropped}")
    return {
        "model_path": tmp.name,
        "holdout_r2": r2,
        "n_train": len(X_train),
        "n_test": len(X_test),
        "kept": kept,
        "dropped": dropped,
    }
