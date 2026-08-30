"""
glowtbook.aip
=============
The AIP (Archival Information Package) side of OAIS: the full-fidelity originals,
wrapped as a **BagIt** bag with fixity (sha256 + sha512), optionally pushed to
**MinIO**/S3 object storage. This mirrors the Kopia/BagIt/MinIO preservation
pattern so an object's archival copy is a real preservation package, not just
files in a folder.

BagIt (Library of Congress) is used via the `bagit` library, so the output
validates with any BagIt tool. MinIO is S3-compatible, so we talk to it with
boto3 (`endpoint_url`), which also means the push path is testable offline.

Config (all optional — absent = local bags only):
  MINIO_ENDPOINT   host:port (e.g. minio.hsl.virginia.edu:9000)
  MINIO_ACCESS_KEY / MINIO_SECRET_KEY
  MINIO_BUCKET     target bucket (created if missing)
  MINIO_SECURE     "1"/"0" — https vs http (default https)
"""
from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import bagit


# --- MinIO / S3 -----------------------------------------------------------
def minio_config() -> dict | None:
    ep = os.environ.get("MINIO_ENDPOINT", "").strip()
    ak = os.environ.get("MINIO_ACCESS_KEY", "").strip()
    sk = os.environ.get("MINIO_SECRET_KEY", "").strip()
    bucket = os.environ.get("MINIO_BUCKET", "").strip()
    if not (ep and ak and sk and bucket):
        return None
    secure = os.environ.get("MINIO_SECURE", "1").lower() not in ("0", "false", "no")
    return {"endpoint": ep, "access_key": ak, "secret_key": sk,
            "bucket": bucket, "secure": secure}


def _s3_client(cfg: dict):
    import boto3
    scheme = "https" if cfg["secure"] else "http"
    return boto3.client("s3", endpoint_url=f"{scheme}://{cfg['endpoint']}",
                        aws_access_key_id=cfg["access_key"],
                        aws_secret_access_key=cfg["secret_key"])


def push_file(path: str | Path, key: str, cfg: dict | None = None) -> dict:
    """Upload one file to the configured bucket (creating it if needed)."""
    cfg = cfg or minio_config()
    if not cfg:
        raise RuntimeError("MinIO/S3 is not configured (set MINIO_* env vars).")
    from botocore.exceptions import ClientError
    s3 = _s3_client(cfg)
    try:
        s3.head_bucket(Bucket=cfg["bucket"])
    except ClientError:
        s3.create_bucket(Bucket=cfg["bucket"])
    s3.upload_file(str(path), cfg["bucket"], key,
                   ExtraArgs={"ContentType": "application/x-tar"})
    head = s3.head_object(Bucket=cfg["bucket"], Key=key)
    scheme = "https" if cfg["secure"] else "http"
    return {"bucket": cfg["bucket"], "key": key,
            "etag": head.get("ETag", "").strip('"'),
            "size": head.get("ContentLength"),
            "url": f"{scheme}://{cfg['endpoint']}/{cfg['bucket']}/{key}"}


# --- BagIt ----------------------------------------------------------------
def build_bag(payload_files: list[tuple[str, Path]], metadata: dict,
              bag_info: dict, workdir: Path) -> Path:
    """Assemble a BagIt bag. payload_files = [(arcname, source_path), ...].
    metadata is written as data/metadata.json. Returns the bag directory."""
    workdir.mkdir(parents=True, exist_ok=True)
    for arcname, src in payload_files:
        dest = workdir / arcname
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    (workdir / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    bagit.make_bag(str(workdir), bag_info=bag_info, checksums=["sha256", "sha512"])
    return workdir


def serialize_bag(bag_dir: Path, out_tar: Path) -> Path:
    """Serialize a bag directory to a single .tar (a serialized BagIt bag)."""
    out_tar.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_tar, "w") as tar:
        tar.add(str(bag_dir), arcname=bag_dir.name)
    return out_tar


# --- high-level: archive one object's AIP ---------------------------------
def archive_object_aip(object_id: str, content_hash: str, originals: list[Path],
                       metadata: dict, out_root: Path, push: bool = False) -> dict:
    """Bag an object's originals + metadata, serialize to tar, optionally push to MinIO.
    Returns a receipt dict (paths, sizes, fixity, minio ref)."""
    out_root = Path(out_root); out_root.mkdir(parents=True, exist_ok=True)
    bag_id = f"{object_id}-{content_hash[:8]}"
    payload = [(f"originals/{p.name}", p) for p in originals if Path(p).exists()]
    bag_info = {
        "Source-Organization": os.environ.get("AIP_ORG", "Glass Database"),
        "External-Identifier": content_hash,
        "Bag-Group-Identifier": "glassdb-object-aip",
        "Bagging-Date": datetime.now(timezone.utc).date().isoformat(),
        "Internal-Sender-Description":
            f"Full-fidelity AIP for object {object_id} ({len(payload)} original file(s)).",
    }
    with tempfile.TemporaryDirectory() as tmp:
        bag_dir = build_bag(payload, metadata, bag_info, Path(tmp) / bag_id)
        # read fixity back from the manifest for the receipt
        fixity = {}
        man = bag_dir / "manifest-sha256.txt"
        if man.exists():
            for line in man.read_text().splitlines():
                h, _, name = line.partition("  ")
                fixity[name] = h
        tar_path = out_root / f"{bag_id}.tar"
        serialize_bag(bag_dir, tar_path)

    receipt = {
        "bag_id": bag_id,
        "tar_path": str(tar_path),
        "tar_bytes": tar_path.stat().st_size,
        "payload_files": [a for a, _ in payload],
        "fixity_sha256": fixity,
        "minio": None,
    }
    if push:
        key = f"aip/{object_id}/{bag_id}.tar"
        receipt["minio"] = push_file(tar_path, key)
    return receipt
