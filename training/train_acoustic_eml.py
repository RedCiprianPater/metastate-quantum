"""
train_acoustic_eml.py · train the acoustic material classifier.

Input: list of {audio_base64, material_class: str}. Output: pickle path
of a trained sklearn.ensemble.ExtraTreesClassifier.

Uses librosa STFT + MFCC + spectral coloration features.
"""
from __future__ import annotations
import os
import io
import pickle
import tempfile
import base64
import logging

log = logging.getLogger("perception.train.acoustic")

try:
    import numpy as np
    import librosa
    from sklearn.ensemble import ExtraTreesClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score
    HAVE_DEPS = True
except Exception:
    HAVE_DEPS = False


def _acoustic_features(waveform: "np.ndarray", sr: int) -> "np.ndarray":
    stft = np.abs(librosa.stft(waveform, n_fft=1024))
    mfcc = librosa.feature.mfcc(y=waveform, sr=sr, n_mfcc=13)
    centroid = librosa.feature.spectral_centroid(y=waveform, sr=sr).mean()
    freq_bins = librosa.fft_frequencies(sr=sr, n_fft=1024)
    low_mask = freq_bins < 500
    high_mask = freq_bins > 2000
    low_e = stft[low_mask].mean() if low_mask.any() else 0
    high_e = stft[high_mask].mean() if high_mask.any() else 0
    coloration = float(low_e / (high_e + 1e-9))
    rms = librosa.feature.rms(y=waveform)[0]
    rms_db = 20 * np.log10(rms + 1e-9)
    peak = rms_db.max()
    below = np.where(rms_db < peak - 60)[0]
    rt60 = float(below[0] * 512 / sr) if len(below) > 0 else -1.0
    return np.concatenate([mfcc.mean(axis=1), [centroid, coloration, rt60]])


def train(samples: list[dict]) -> dict:
    if not HAVE_DEPS:
        return {"error": "sklearn/numpy/librosa not available"}
    if not samples or len(samples) < 20:
        return {"error": "insufficient samples", "count": len(samples), "min_required": 20}

    X_list = []
    y_list = []
    kept = 0
    dropped = 0

    for s in samples:
        try:
            data = base64.b64decode(s["audio_base64"])
            waveform, sr = librosa.load(io.BytesIO(data), sr=22050, mono=True)
            if len(waveform) < sr * 0.5:
                dropped += 1
                continue
            feats = _acoustic_features(waveform, sr)
            X_list.append(feats)
            y_list.append(s.get("material_class", "unknown"))
            kept += 1
        except Exception:
            dropped += 1
            continue

    if kept < 20:
        return {"error": "insufficient valid samples", "kept": kept, "dropped": dropped}

    X = np.array(X_list)
    y = np.array(y_list)

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.15, random_state=42, stratify=y if len(set(y)) > 1 else None)

    log.info(f"[acoustic] training ExtraTreesClassifier on {X_train.shape} samples · {len(set(y))} classes")
    model = ExtraTreesClassifier(
        n_estimators=200,
        max_depth=20,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    acc = float(accuracy_score(y_test, y_pred))

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pkl")
    pickle.dump(model, tmp)
    tmp.close()

    log.info(f"[acoustic] trained · acc={acc:.4f} · kept={kept} · dropped={dropped}")
    return {
        "model_path": tmp.name,
        "holdout_accuracy": acc,
        "n_train": len(X_train),
        "n_test": len(X_test),
        "n_classes": len(set(y)),
        "classes": sorted(set(y.tolist())),
        "kept": kept,
        "dropped": dropped,
    }
