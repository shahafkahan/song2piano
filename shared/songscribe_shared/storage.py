from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
import shutil
from typing import BinaryIO

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from songscribe_shared.config import Settings, get_settings


class StorageBackend(ABC):
    """Abstract object storage interface for S3/R2/MinIO and local filesystem."""

    @abstractmethod
    def put_file(self, key: str, local_path: Path, content_type: str = "application/octet-stream") -> None:
        ...

    @abstractmethod
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        ...

    @abstractmethod
    def get_file(self, key: str, dest_path: Path) -> None:
        ...

    @abstractmethod
    def get_size(self, key: str) -> int:
        ...

    @abstractmethod
    def read_range(self, key: str, start: int, end: int) -> Iterator[bytes]:
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None:
        ...


class LocalStorage(StorageBackend):
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        clean = key.lstrip("/").replace("\\", "/")
        target = (self.root / clean).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError(f"Unsafe storage key: {key}")
        return target

    def put_file(self, key: str, local_path: Path, content_type: str = "application/octet-stream") -> None:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, target)

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        target = self._resolve(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get_file(self, key: str, dest_path: Path) -> None:
        src = self._resolve(key)
        if not src.exists():
            raise FileNotFoundError(key)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_path)

    def get_size(self, key: str) -> int:
        src = self._resolve(key)
        if not src.exists():
            raise FileNotFoundError(key)
        return src.stat().st_size

    def read_range(self, key: str, start: int, end: int) -> Iterator[bytes]:
        src = self._resolve(key)
        if not src.exists():
            raise FileNotFoundError(key)
        chunk_size = 64 * 1024
        with src.open("rb") as fh:
            fh.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = fh.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    def exists(self, key: str) -> bool:
        return self._resolve(key).is_file()

    def delete_prefix(self, prefix: str) -> None:
        target = self._resolve(prefix.rstrip("/"))
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.is_file():
            target.unlink(missing_ok=True)


class S3Storage(StorageBackend):
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        endpoint = settings.s3_endpoint_url.strip() or None
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            use_ssl=settings.s3_use_ssl,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        self._bucket_verified = False

    def _ensure_bucket(self) -> None:
        if self._bucket_verified:
            return
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except ClientError:
            try:
                self.client.create_bucket(Bucket=self.bucket)
            except ClientError:
                pass
        self._bucket_verified = True

    def put_file(self, key: str, local_path: Path, content_type: str = "application/octet-stream") -> None:
        self._ensure_bucket()
        self.client.upload_file(
            str(local_path),
            self.bucket,
            key.lstrip("/"),
            ExtraArgs={"ContentType": content_type},
        )

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._ensure_bucket()
        self.client.put_object(
            Bucket=self.bucket,
            Key=key.lstrip("/"),
            Body=data,
            ContentType=content_type,
        )

    def get_file(self, key: str, dest_path: Path) -> None:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key.lstrip("/"), str(dest_path))

    def get_size(self, key: str) -> int:
        head = self.client.head_object(Bucket=self.bucket, Key=key.lstrip("/"))
        return int(head["ContentLength"])

    def read_range(self, key: str, start: int, end: int) -> Iterator[bytes]:
        resp = self.client.get_object(
            Bucket=self.bucket,
            Key=key.lstrip("/"),
            Range=f"bytes={start}-{end}",
        )
        body: BinaryIO = resp["Body"]
        try:
            for chunk in iter(lambda: body.read(64 * 1024), b""):
                yield chunk
        finally:
            body.close()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key.lstrip("/"))
            return True
        except ClientError:
            return False

    def delete_prefix(self, prefix: str) -> None:
        clean_prefix = prefix.lstrip("/")
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=clean_prefix):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objects})


@lru_cache
def get_storage() -> StorageBackend:
    settings = get_settings()
    if settings.storage_backend == "local":
        return LocalStorage(settings.local_storage_path)
    return S3Storage(settings)
