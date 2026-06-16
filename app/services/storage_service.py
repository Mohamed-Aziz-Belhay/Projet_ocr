"""
app/services/storage_service.py — Enterprise Edition
Pluggable storage: MinIO/S3 in production, local /tmp in dev.
All stored files are optionally encrypted at rest (Fernet).
"""
from __future__ import annotations
import json
import uuid
from pathlib import Path
from typing import Optional

from app.core.settings import get_settings
from app.core.encryption import encrypt_bytes, decrypt_bytes, is_encryption_enabled
from app.core.errors import UnsupportedFileTypeError, FileTooLargeError
from app.core.logging import get_logger

log = get_logger(__name__)
settings = get_settings()


class StorageService:

    def __init__(self):
        self._use_s3 = bool(settings.S3_ENDPOINT_URL and settings.S3_ACCESS_KEY)
        self._s3_client = None
        self._local_upload = Path(settings.UPLOAD_DIR)
        self._local_result = Path(settings.RESULT_DIR)
        self._local_upload.mkdir(parents=True, exist_ok=True)
        self._local_result.mkdir(parents=True, exist_ok=True)
        if self._use_s3:
            self._init_s3()

    def _init_s3(self):
        try:
            import boto3
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=settings.S3_ENDPOINT_URL,
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                region_name=settings.S3_REGION,
                use_ssl=settings.S3_USE_SSL,
            )
            for bucket in [settings.S3_BUCKET_UPLOADS, settings.S3_BUCKET_RESULTS]:
                try:
                    self._s3_client.head_bucket(Bucket=bucket)
                except Exception:
                    self._s3_client.create_bucket(Bucket=bucket)
            log.info("S3/MinIO storage initialized", extra={"endpoint": settings.S3_ENDPOINT_URL})
        except ImportError:
            log.warning("boto3 not installed — falling back to local storage")
            self._use_s3 = False

    def save_upload(self, file_content: bytes, filename: str, org_id: str = "default") -> str:
        ext = Path(filename).suffix.lstrip(".").lower()

        if ext not in settings.ALLOWED_EXTENSIONS:
            raise UnsupportedFileTypeError(f"Extension '{ext}' not allowed")

        size_mb = len(file_content) / (1024 * 1024)

        if size_mb > settings.MAX_UPLOAD_MB:
            raise FileTooLargeError(
                f"File {size_mb:.1f}MB exceeds {settings.MAX_UPLOAD_MB}MB limit"
            )

        key = f"{org_id}/uploads/{uuid.uuid4()}_{filename}"

        if self._use_s3 and self._s3_client:
            data = encrypt_bytes(file_content) if settings.ENCRYPT_STORED_FILES else file_content

            self._s3_client.put_object(
                Bucket=settings.S3_BUCKET_UPLOADS,
                Key=key,
                Body=data,
                Metadata={
                    "org_id": org_id,
                    "encrypted": str(is_encryption_enabled()),
                },
            )

            return f"s3://{settings.S3_BUCKET_UPLOADS}/{key}"

        # Important:
        # En local, le pipeline OCR lit directement ce chemin avec OpenCV.
        # Il faut donc écrire l'image brute, pas l'image chiffrée.
        local_path = self._local_upload / key.replace("/", "_")
        local_path.write_bytes(file_content)

        return str(local_path)

    def load_upload(self, path: str) -> bytes:
        data = self._read(path, settings.S3_BUCKET_UPLOADS)
        return decrypt_bytes(data) if settings.ENCRYPT_STORED_FILES else data

    def delete_upload(self, path: str) -> None:
        self._delete(path, settings.S3_BUCKET_UPLOADS)

    def save_result(self, job_id: str, data: dict, org_id: str = "default") -> str:
        key = f"{org_id}/results/{job_id}.json"
        raw = json.dumps(data, ensure_ascii=False, indent=2).encode()
        payload = encrypt_bytes(raw) if settings.ENCRYPT_STORED_FILES else raw
        if self._use_s3 and self._s3_client:
            self._s3_client.put_object(
                Bucket=settings.S3_BUCKET_RESULTS, Key=key, Body=payload,
                ContentType="application/json",
            )
            return f"s3://{settings.S3_BUCKET_RESULTS}/{key}"
        else:
            local_path = self._local_result / f"{org_id}_{job_id}.json"
            local_path.write_bytes(payload)
            return str(local_path)

    async def save_result_async(self, org_id: str, job_id: str, data: dict) -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.save_result, job_id, data, org_id)

    def load_result(self, path_or_job_id: str, org_id: str = "default") -> Optional[dict]:
        try:
            raw = self._read(path_or_job_id, settings.S3_BUCKET_RESULTS)
            payload = decrypt_bytes(raw) if settings.ENCRYPT_STORED_FILES else raw
            return json.loads(payload.decode())
        except Exception as exc:
            log.error("Failed to load result", extra={"path": path_or_job_id, "error": str(exc)})
            return None

    async def delete_object_async(self, path: str) -> None:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._delete, path, settings.S3_BUCKET_RESULTS)

    def _read(self, path: str, bucket: str) -> bytes:
        if path.startswith("s3://") and self._s3_client:
            key = path.split("/", 3)[-1]
            resp = self._s3_client.get_object(Bucket=bucket, Key=key)
            return resp["Body"].read()
        return Path(path).read_bytes()

    def _delete(self, path: str, bucket: str) -> None:
        try:
            if path.startswith("s3://") and self._s3_client:
                key = path.split("/", 3)[-1]
                self._s3_client.delete_object(Bucket=bucket, Key=key)
            else:
                Path(path).unlink(missing_ok=True)
        except Exception as exc:
            log.warning("Delete failed", extra={"path": path, "error": str(exc)})


_storage: Optional[StorageService] = None


def get_storage_service() -> StorageService:
    global _storage
    if _storage is None:
        _storage = StorageService()
    return _storage
