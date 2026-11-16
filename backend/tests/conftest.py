from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from tests.support import EVIDENCE_BUCKET, create_tables


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        yield


@pytest.fixture
def dynamodb(aws):
    client = boto3.client("dynamodb", region_name="us-east-1")
    create_tables(client)
    return client


@pytest.fixture
def s3(aws):
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket=EVIDENCE_BUCKET)
    return client
