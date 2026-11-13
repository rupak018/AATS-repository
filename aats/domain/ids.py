"""Deterministic identifiers.

IDs are UUIDv5 values derived from their business keys, never random UUIDv4s, so that the same
replay input always produces the same IDs. This is what makes golden files and audit hashes stable.
"""

from __future__ import annotations

import uuid

AATS_NAMESPACE = uuid.UUID("6f1c2a54-2b7e-5d1a-9c40-aa7500000001")


def stable_id(kind: str, *parts: object) -> str:
    key = "|".join([kind, *(str(p) for p in parts)])
    return str(uuid.uuid5(AATS_NAMESPACE, key))
