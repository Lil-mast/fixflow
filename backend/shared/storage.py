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
        expires_in: int = 300,
    ) -> PresignedUpload:
        upload_id = new_uuid4()
        extension = CONTENT_TYPE_EXTENSIONS[request.content_type][0]
        object_key = f"incoming/{request.report_id}/{upload_id}.{extension}"

        fields = {
            "key": object_key,
            "Content-Type": request.content_type,
            "x-amz-meta-report-id": request.report_id,
            "x-amz-meta-upload-id": upload_id,
            "x-amz-meta-purpose": "report",
        }
        conditions = [
            {"key": object_key},
            {"Content-Type": request.content_type},
            ["content-length-range", 1, MAX_UPLOAD_BYTES],
            {"x-amz-meta-report-id": request.report_id},
            {"x-amz-meta-upload-id": upload_id},
            {"x-amz-meta-purpose": "report"},
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

    def verify_and_copy(self, evidence: EvidenceInput, report_id: str) -> str:
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
        if metadata.get("purpose") != "report":
            raise _evidence_mismatch("The uploaded file has an invalid purpose.")

        extension = CONTENT_TYPE_EXTENSIONS[evidence.content_type][0]
        destination = f"evidence/{report_id}/{evidence.upload_id}.{extension}"
        self.client.copy_object(
            Bucket=self.bucket,
            Key=destination,
            CopySource={"Bucket": self.bucket, "Key": evidence.object_key},
            MetadataDirective="COPY",
        )
        return destination

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
