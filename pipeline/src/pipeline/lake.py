"""Thin wrapper around the S3-compatible data lake (MinIO)."""

import io

import boto3
from botocore.config import Config
import pandas as pd
from botocore.exceptions import ClientError

from .config import Settings


class Lake:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.s3 = client or boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name="us-east-1",
            config=Config(retries={"max_attempts": 8, "mode": "standard"}),
        )

    def ensure_buckets(self) -> None:
        s = self.settings
        for bucket in (s.bronze_bucket, s.silver_bucket, s.gold_bucket, s.lineage_bucket, s.registry_bucket):
            try:
                self.s3.head_bucket(Bucket=bucket)
            except ClientError:
                self.s3.create_bucket(Bucket=bucket)

    def put_bytes(self, bucket: str, key: str, data: bytes) -> None:
        self.s3.put_object(Bucket=bucket, Key=key, Body=data)

    def get_bytes(self, bucket: str, key: str) -> bytes:
        return self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    def latest_key(self, bucket: str, prefix: str) -> str:
        objs = self.s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
        if not objs:
            raise FileNotFoundError(f"s3://{bucket}/{prefix}*")
        return max(objs, key=lambda o: o["Key"])["Key"]

    def write_parquet(self, bucket: str, table: str, df: pd.DataFrame) -> str:
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        key = f"{table}/{table}.parquet"
        self.put_bytes(bucket, key, buf.getvalue())
        return key

    def read_parquet(self, bucket: str, table: str) -> pd.DataFrame:
        return pd.read_parquet(io.BytesIO(self.get_bytes(bucket, f"{table}/{table}.parquet")))

    def append_parquet(self, bucket: str, table: str, df: pd.DataFrame) -> str:
        """Append rows to a parquet table, keeping history across pipeline runs."""
        try:
            existing = self.read_parquet(bucket, table)
            df = pd.concat([existing, df], ignore_index=True) if not existing.empty else df
        except (ClientError, FileNotFoundError):
            pass
        return self.write_parquet(bucket, table, df)
