import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence

NAMESPACE = "rag"


def _hash(payload: object) -> str:
    s = (
        payload
        if isinstance(payload, str)
        else json.dumps(payload, sort_keys=True, default=str)
    )
    return hashlib.sha256(s.encode()).hexdigest()[:32]


def embedding_key(model: str, text: str, provider_version: str = "") -> str:
    return f"{NAMESPACE}:emb:{model}:{provider_version}:{_hash(text)}"


def query_ext_key(
    model: str,
    query: str,
    max_variants: int,
    provider_version: str = "",
) -> str:
    return f"{NAMESPACE}:qext:{model}:{provider_version}:{_hash({'q': query, 'n': max_variants})}"


def search_key(payload: Mapping[str, object]) -> str:
    versions_value = payload.get("dataset_versions", [])
    versions: list[object] = []
    if isinstance(versions_value, Sequence) and not isinstance(versions_value, str):
        versions = sorted(versions_value, key=str)
    versions_str = "-".join(str(v) for v in versions) if versions else "0"

    dataset_ids_value = payload.get("dataset_ids", [])
    ds_ids: list[str] = []
    if isinstance(dataset_ids_value, Sequence) and not isinstance(
        dataset_ids_value, str
    ):
        ds_ids = sorted(str(dataset_id) for dataset_id in dataset_ids_value)
    ds_ids_str = "-".join(ds_ids) if ds_ids else "_"
    canonical: dict[str, object] = {
        "dataset_ids": ds_ids,
        "dataset_versions": versions,
    }
    for field in ("query", "top_k"):
        if field in payload:
            canonical[field] = payload[field]
    return f"{NAMESPACE}:search:{ds_ids_str}:{versions_str}:{_hash(canonical)}"


def search_key_pattern_for_dataset(dataset_id: uuid.UUID | str) -> str:
    return f"{NAMESPACE}:search:*-{dataset_id}*:*"


def dataset_version_key(dataset_id: uuid.UUID | str) -> str:
    return f"{NAMESPACE}:version:{dataset_id}"


def rerank_key(model: str, query: str, doc_ids: list[uuid.UUID]) -> str:
    return f"{NAMESPACE}:rk:{model}:{_hash({'q': query, 'ids': [str(i) for i in doc_ids]})}"
