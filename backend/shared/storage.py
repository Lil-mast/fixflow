"""S3 evidence uploads: presigned POST creation and verified copies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import ClientError

from shared.clock import seconds_from_now, to_iso
from shared.config import MAX_UPLOAD_BYTES
from shared.errors import UnprocessableEntity
from shared.ids import new_uuid4
from shared.validation import (
    CONTENT_TYPE_EXTENSIONS,
    EvidenceInput,
    UploadRequest,
)


@dataclass(frozen=True)
class PresignedUpload:
    upload: dict[str, Any]
    object_key: str
    upload_id: str
    expires_at: str


def _prefixes(purpose: str, report_id: str) -> tuple[str, str]:
    """Return ``(incoming prefix, stored prefix)`` for a supported upload purpose."""

    if purpose == "resolution":
        return f"incoming/resolution/{report_id}", f"resolution/{report_id}"
    if purpose == "report":
        return f"incoming/{report_id}", f"evidence/{report_id}"
    raise ValueError(f"Unsupported evidence purpose: {purpose}")


class EvidenceStorage:
    def __init__(
        self,
        bucket: str,
        *,
        client: Any = None,
        region: str = "us-east-1",
    ) -> None:
        self.bucket = bucket
        self.client = client or boto3.client("s3", region_name=region)

    def create_upload(
        self,
        request: UploadRequest,
        *,
        purpose: str = "report",
        expires_in: int = 300,
    ) -> PresignedUpload:
        incoming_prefix, _ = _prefixes(purpose, request.report_id)
        upload_id = new_uuid4()
        extension = CONTENT_TYPE_EXTENSIONS[request.content_type][0]
        object_key = f"{incoming_prefix}/{upload_id}.{extension}"

        fields = {
            "key": object_key,
            "Content-Type": request.content_type,
            "x-amz-meta-report-id": request.report_id,
            "x-amz-meta-upload-id": upload_id,
            "x-amz-meta-purpose": purpose,
        }
        conditions = [
            {"key": object_key},
            {"Content-Type": request.content_type},
            ["content-length-range", 1, MAX_UPLOAD_BYTES],
            {"x-amz-meta-report-id": request.report_id},
            {"x-amz-meta-upload-id": upload_id},
            {"x-amz-meta-purpose": purpose},
        ]

        signed = self.client.generate_presigned_post(
            Bucket=self.bucket,
            Key=object_key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=expires_in,
        )
        return PresignedUpload(
            upload=signed,
            object_key=object_key,
            upload_id=upload_id,
            expires_at=to_iso(seconds_from_now(expires_in)),
        )

    def verify_and_copy(
        self,
        evidence: EvidenceInput,
        report_id: str,
        *,
        purpose: str = "report",
    ) -> str:
        _, destination_prefix = _prefixes(purpose, report_id)
        head = self._head(evidence.object_key)
        metadata = head.get("Metadata") or {}

        if int(head.get("ContentLength", -1)) != evidence.content_length:
            raise _evidence_mismatch("The uploaded file size does not match the report.")
        if head.get("ContentType") != evidence.content_type:
            raise _evidence_mismatch("The uploaded file type does not match the report.")
        if metadata.get("report-id") != report_id:
            raise _evidence_mismatch("The uploaded file belongs to a different report.")
        if metadata.get("upload-id") != evidence.upload_id:
            raise _evidence_mismatch("The uploaded file identifier does not match.")
        if metadata.get("purpose") != purpose:
            raise _evidence_mismatch("The uploaded file has an invalid purpose.")

        extension = CONTENT_TYPE_EXTENSIONS[evidence.content_type][0]
        destination = f"{destination_prefix}/{evidence.upload_id}.{extension}"
        self.client.copy_object(
            Bucket=self.bucket,
            Key=destination,
            CopySource={"Bucket": self.bucket, "Key": evidence.object_key},
            MetadataDirective="COPY",
        )
        return destination

    def presign_download(self, object_key: str | None, *, expires_in: int = 300) -> str | None:
        """Return a short-lived GET URL for a private evidence object."""

        if not object_key:
            return None
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": object_key},
            ExpiresIn=expires_in,
        )

    def _head(self, object_key: str) -> dict[str, Any]:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=object_key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise _evidence_mismatch(
                    "The evidence upload was not found or has expired."
                ) from exc
            raise


def _evidence_mismatch(message: str) -> UnprocessableEntity:
    return UnprocessableEntity(message, {"field": "evidence"})
