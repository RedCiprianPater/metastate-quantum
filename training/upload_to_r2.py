"""
upload_to_r2.py · push trained EML models to Cloudflare R2.

R2 is S3-compatible, so we use boto3. Uploads to both a versioned path
(for audit and rollback) and the `_current` sentinel path (which the
perception module reads on cold-start).

Env vars needed on Render:
  R2_ACCOUNT_ID
  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY
  R2_BUCKET_NAME       default: chainstate-perception-models

Returns the public URL of the uploaded object (via R2_MODELS_BASE_URL if
present, else the S3-style URL).
"""
from __future__ import annotations
import os
import logging
from typing import Optional

log = logging.getLogger("perception.upload")

try:
    import boto3
    from botocore.config import Config
    HAVE_BOTO3 = True
except Exception:
    HAVE_BOTO3 = False

R2_ACCOUNT_ID     = os.environ.get("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID  = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_KEY     = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET         = os.environ.get("R2_BUCKET_NAME", "chainstate-perception-models")
R2_MODELS_BASE    = os.environ.get("R2_MODELS_BASE_URL", "")


def _client():
    if not HAVE_BOTO3:
        return None
    if not (R2_ACCOUNT_ID and R2_ACCESS_KEY_ID and R2_SECRET_KEY):
        return None
    endpoint = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4", region_name="auto"),
    )


def upload(local_path: str, key: str, also_key: Optional[str] = None) -> Optional[str]:
    """Upload a local file to R2 at both `key` and (optionally) `also_key`.

    Returns the public URL if R2_MODELS_BASE_URL is set, else a bare
    protocol-relative reference. Returns None on failure.
    """
    client = _client()
    if client is None:
        log.warning("R2 client not configured; skipping upload")
        return None

    try:
        with open(local_path, "rb") as f:
            body = f.read()

        client.put_object(Bucket=R2_BUCKET, Key=key, Body=body)
        log.info(f"[r2] uploaded {len(body)} bytes to {R2_BUCKET}/{key}")

        if also_key:
            client.put_object(Bucket=R2_BUCKET, Key=also_key, Body=body)
            log.info(f"[r2] uploaded copy to {R2_BUCKET}/{also_key}")

        if R2_MODELS_BASE:
            return R2_MODELS_BASE.rstrip("/") + "/" + key
        return f"r2://{R2_BUCKET}/{key}"
    except Exception as e:
        log.exception(f"R2 upload failed: {e}")
        return None
