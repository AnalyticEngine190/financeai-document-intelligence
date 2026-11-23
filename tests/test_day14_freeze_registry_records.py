from dataclasses import dataclass

import pytest

from scripts.day14_freeze import _indexed_document_ids, _public_document_dict


@dataclass
class FakeDocumentRecord:
    document_id: str
    issuer: str

    def public_dict(self):
        return {"document_id": self.document_id, "issuer": self.issuer}


def test_registry_document_record_is_serialized_to_plain_dictionary():
    result = _public_document_dict(FakeDocumentRecord("nq3", "Nordea"))
    assert result == {"document_id": "nq3", "issuer": "Nordea"}
    assert isinstance(result, dict)


def test_registry_document_mapping_is_supported_without_attribute_access():
    source = {"document_id": "sebq3", "issuer": "SEB"}
    result = _public_document_dict(source)
    assert result == source
    assert result is not source


def test_indexed_ids_accept_serialized_inventory_dictionaries():
    documents = [
        {"document_id": "nq2"},
        {"document_id": "nq3"},
        {"document_id": "sebq3"},
        {"document_id": "hbq3"},
    ]
    assert _indexed_document_ids(documents) == {"nq2", "nq3", "sebq3", "hbq3"}


def test_indexed_ids_remain_compatible_with_record_objects():
    documents = [FakeDocumentRecord("nq3", "Nordea"), FakeDocumentRecord("sebq3", "SEB")]
    assert _indexed_document_ids(documents) == {"nq3", "sebq3"}


def test_public_record_without_document_id_fails_with_clear_error():
    with pytest.raises(RuntimeError, match="missing its document_id"):
        _public_document_dict({"issuer": "Nordea"})
