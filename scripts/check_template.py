"""Offline sanity checks for the SAM template.

The SAM CLI is the real validator, but it is not always installed. This script catches
the mistakes that matter most in review: broken YAML, missing resources, a public bucket,
a missing TTL, a missing GSI, and a wildcard principal.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

TEMPLATE = Path(__file__).resolve().parents[1] / "infrastructure" / "template.yaml"

REQUIRED_RESOURCES = {
    "HttpApi",
    "HealthFunction",
    "ReportsTable",
    "ReportEventsTable",
    "WorkflowTokensTable",
    "WebBucket",
    "WebBucketPolicy",
    "EvidenceBucket",
    "WebOriginAccessControl",
    "SecurityHeadersPolicy",
    "WebDistribution",
    "AdminUserPool",
    "AdminUserPoolClient",
    "AdminUserPoolDomain",
    "AdminGroup",
}


class CfnLoader(yaml.SafeLoader):
    """SafeLoader that keeps CloudFormation short-form tags as ``Fn::*`` maps."""


def _construct_tag(loader: CfnLoader, tag_suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {f"Fn::{tag_suffix}": value}


CfnLoader.add_multi_constructor("!", _construct_tag)


def load_template() -> dict[str, Any]:
    return yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=CfnLoader)


def find_wildcard_principals(node: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key == "Principal" and value == "*":
                hits.append(child)
            elif isinstance(value, dict) and "*" in value:
                hits.append(f"{child}.*")
            else:
                hits.extend(find_wildcard_principals(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(find_wildcard_principals(value, f"{path}[{index}]"))
    return hits


def check() -> list[str]:
    problems: list[str] = []
    template = load_template()
    resources = template.get("Resources", {})

    if template.get("Transform") != "AWS::Serverless-2016-10-31":
        problems.append("Template is missing the AWS::Serverless transform")

    missing = REQUIRED_RESOURCES - resources.keys()
    if missing:
        problems.append(f"Missing resources: {', '.join(sorted(missing))}")

    reports = resources.get("ReportsTable", {}).get("Properties", {})
    indexes = {index.get("IndexName") for index in reports.get("GlobalSecondaryIndexes", [])}
    if indexes != {"GSI1", "GSI2", "GSI3"}:
        problems.append(f"ReportsTable indexes are {sorted(indexes)}, expected GSI1-GSI3")

    tokens = resources.get("WorkflowTokensTable", {}).get("Properties", {})
    ttl = tokens.get("TimeToLiveSpecification", {})
    if ttl.get("AttributeName") != "expiresAt" or ttl.get("Enabled") is not True:
        problems.append("WorkflowTokensTable must enable TTL on expiresAt")

    for bucket_id in ("WebBucket", "EvidenceBucket"):
        props = resources.get(bucket_id, {}).get("Properties", {})
        block = props.get("PublicAccessBlockConfiguration", {})
        if not all(
            block.get(key) is True
            for key in (
                "BlockPublicAcls",
                "BlockPublicPolicy",
                "IgnorePublicAcls",
                "RestrictPublicBuckets",
            )
        ):
            problems.append(f"{bucket_id} does not block all public access")

    wildcards = find_wildcard_principals(template)
    if wildcards:
        problems.append(f"Wildcard principals found at: {', '.join(wildcards)}")

    allowed_origin = (
        template.get("Parameters", {}).get("AllowedOrigin", {}).get("Default", "")
    )
    if allowed_origin.strip() == "*":
        problems.append("AllowedOrigin must not default to a wildcard")

    return problems


def main() -> int:
    problems = check()
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print(f"OK: {TEMPLATE.name} passed offline template checks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
