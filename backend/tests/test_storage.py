from __future__ import annotations

import pytest

from shared.errors import UnprocessableEntity
from shared.ids import new_uuid4
from shared.storage import EvidenceStorage
from shared.validation import UploadRequest, validate_upload_request
from tests.support import EVIDENCE_BUCKET, RecordingS3


def test_presigned_post_has_exact_conditions() -> None:
    signing_client = RecordingS3()
    storage = EvidenceStorage(EVIDENCE_BUCKET, client=signing_client)
    request = validate_upload_request(
        {
            "reportId": new_uuid4(),
            "contentType": "image/jpeg",
            "contentLength": 2048,
        }
    )

    signed = storage.create_upload(request, expires_in=300)
    kwargs = signing_client.kwargs
    assert kwargs is not None
    assert kwargs["Key"] == signed.object_key
    assert signed.object_key.startswith(f"incoming/{request.report_id}/")
    assert signed.object_key.endswith(".jpg")
    assert kwargs["ExpiresIn"] == 300
    assert ["content-length-range", 1, 10 * 1024 * 1024] in kwargs["Conditions"]
    assert {"Content-Type": "image/jpeg"} in kwargs["Conditions"]
    assert {"x-amz-meta-report-id": request.report_id} in kwargs["Conditions"]
    assert {"x-amz-meta-upload-id": signed.upload_id} in kwargs["Conditions"]
    assert {"x-amz-meta-purpose": "report"} in kwargs["Conditions"]


def test_verify_and_copy_preserves_metadata(s3) -> None:
    request = UploadRequest(
        report_id=new_uuid4(),
        content_type="image/png",
        content_length=4,
    )
    storage = EvidenceStorage(EVIDENCE_BUCKET, client=s3)
    signed = storage.create_upload(request)
    s3.put_object(
        Bucket=EVIDENCE_BUCKET,
        Key=signed.object_key,
        Body=b"data",
        ContentType="image/png",
        Metadata={
            "report-id": request.report_id,
            "upload-id": signed.upload_id,
            "purpose": "report",
        },
    )

    from shared.validation import EvidenceInput

    destination = storage.verify_and_copy(
        EvidenceInput(
            object_key=signed.object_key,
            upload_id=signed.upload_id,
            content_type="image/png",
            content_length=4,
        ),
        request.report_id,
    )

    assert destination == f"evidence/{request.report_id}/{signed.upload_id}.png"
    copied = s3.head_object(Bucket=EVIDENCE_BUCKET, Key=destination)
    assert copied["ContentLength"] == 4
    assert copied["Metadata"]["report-id"] == request.report_id


def test_missing_upload_is_unprocessable(s3) -> None:
    storage = EvidenceStorage(EVIDENCE_BUCKET, client=s3)

    from shared.validation import EvidenceInput

    with pytest.raises(UnprocessableEntity) as exc:
        storage.verify_and_copy(
            EvidenceInput(
                object_key=f"incoming/{new_uuid4()}/{new_uuid4()}.jpg",
                upload_id=new_uuid4(),
                content_type="image/jpeg",
                content_length=10,
            ),
            new_uuid4(),
        )
    assert exc.value.status_code == 422
