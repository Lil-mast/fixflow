from __future__ import annotations

import pytest

from shared.ids import (
    CROCKFORD_ALPHABET,
    is_uuid4,
    new_public_ref,
    new_ulid,
    new_uuid4,
    normalize_public_ref,
)


def test_uuid4_validation() -> None:
    assert is_uuid4(new_uuid4())
    assert not is_uuid4("e3b0c442-98fc-1c14-9afb-f4c8996fb924")
    assert not is_uuid4("not-a-uuid")
    assert not is_uuid4(123)


def test_public_ref_format_and_normalization() -> None:
    ref = new_public_ref()
    assert len(ref) == 22
    assert ref.startswith("FF-")

    compact = ref.replace("-", "").lower()
    assert normalize_public_ref(compact) == ref
    assert normalize_public_ref(f"  {ref.lower()}  ") == ref

    assert normalize_public_ref("FF-7K3M-9Q2X-4T8N-6V2") is None
    assert normalize_public_ref("XX-7K3M-9Q2X-4T8N-6V2B") is None
    assert normalize_public_ref("not-a-ref") is None
    assert normalize_public_ref(None) is None


def test_public_ref_uses_crockford_alphabet_and_has_enough_length() -> None:
    ref = new_public_ref()
    body = ref.replace("-", "")[2:]
    assert len(body) == 16
    assert set(body) <= set(CROCKFORD_ALPHABET)


def test_ulids_sort_by_timestamp() -> None:
    early = new_ulid(1_700_000_000_000)
    late = new_ulid(1_700_000_001_000)

    assert len(early) == 26
    assert early < late
    assert set(early) <= set(CROCKFORD_ALPHABET)


@pytest.mark.parametrize("value", ["", "abc", "FF-", "FF-" + "I" * 16])
def test_bad_public_refs_are_rejected(value: str) -> None:
    assert normalize_public_ref(value) is None
