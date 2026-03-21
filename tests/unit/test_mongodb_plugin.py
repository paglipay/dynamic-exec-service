from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from plugins.mongodb_plugin import MongoDBPlugin


class _InsertOneResult:
    def __init__(self, inserted_id: Any) -> None:
        self.inserted_id = inserted_id


class _InsertManyResult:
    def __init__(self, inserted_ids: list[Any]) -> None:
        self.inserted_ids = inserted_ids


class _UpdateResult:
    def __init__(self, matched_count: int, modified_count: int, upserted_id: Any = None) -> None:
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class _DeleteResult:
    def __init__(self, deleted_count: int) -> None:
        self.deleted_count = deleted_count


class _FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = list(documents)

    def sort(self, sort_spec: list[tuple[str, Any]]) -> _FakeCursor:
        for field_name, direction in reversed(sort_spec):
            reverse = direction == -1 or isinstance(direction, dict)
            self._documents.sort(key=lambda item: item.get(field_name), reverse=reverse)
        return self

    def skip(self, count: int) -> _FakeCursor:
        self._documents = self._documents[count:]
        return self

    def limit(self, count: int) -> _FakeCursor:
        self._documents = self._documents[:count]
        return self

    def __iter__(self):
        return iter(self._documents)


class _FakeCollection:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.text_index_fields: list[str] = []
        self._next_id = 1

    def insert_one(self, document: dict[str, Any]) -> _InsertOneResult:
        stored = deepcopy(document)
        if "_id" not in stored:
            stored["_id"] = f"doc-{self._next_id}"
            self._next_id += 1
        self.documents.append(stored)
        return _InsertOneResult(stored["_id"])

    def insert_many(self, documents: list[dict[str, Any]], ordered: bool = True) -> _InsertManyResult:
        del ordered
        inserted_ids: list[Any] = []
        for document in documents:
            result = self.insert_one(document)
            inserted_ids.append(result.inserted_id)
        return _InsertManyResult(inserted_ids)

    def find_one(self, filter_query: dict[str, Any], projection: dict[str, Any] | None = None) -> dict[str, Any] | None:
        for document in self.documents:
            if self._matches_filter(document, filter_query):
                return self._apply_projection(document, projection)
        return None

    def find(
        self,
        filter_query: dict[str, Any],
        projection: dict[str, Any] | None = None,
    ) -> _FakeCursor:
        matched = [
            self._apply_projection(document, projection)
            for document in self.documents
            if self._matches_filter(document, filter_query)
        ]
        return _FakeCursor(matched)

    def count_documents(self, filter_query: dict[str, Any]) -> int:
        return sum(1 for document in self.documents if self._matches_filter(document, filter_query))

    def update_one(self, filter_query: dict[str, Any], update_operations: dict[str, Any], upsert: bool = False) -> _UpdateResult:
        return self._update(filter_query, update_operations, upsert, many=False)

    def update_many(self, filter_query: dict[str, Any], update_operations: dict[str, Any], upsert: bool = False) -> _UpdateResult:
        return self._update(filter_query, update_operations, upsert, many=True)

    def replace_one(self, filter_query: dict[str, Any], replacement: dict[str, Any], upsert: bool = False) -> _UpdateResult:
        for index, document in enumerate(self.documents):
            if self._matches_filter(document, filter_query):
                replacement_document = deepcopy(replacement)
                replacement_document["_id"] = document["_id"]
                self.documents[index] = replacement_document
                return _UpdateResult(1, 1)

        if upsert:
            inserted = deepcopy(replacement)
            for key, value in filter_query.items():
                inserted.setdefault(key, value)
            result = self.insert_one(inserted)
            return _UpdateResult(0, 0, upserted_id=result.inserted_id)

        return _UpdateResult(0, 0)

    def delete_one(self, filter_query: dict[str, Any]) -> _DeleteResult:
        for index, document in enumerate(self.documents):
            if self._matches_filter(document, filter_query):
                del self.documents[index]
                return _DeleteResult(1)
        return _DeleteResult(0)

    def delete_many(self, filter_query: dict[str, Any]) -> _DeleteResult:
        remaining: list[dict[str, Any]] = []
        deleted_count = 0
        for document in self.documents:
            if self._matches_filter(document, filter_query):
                deleted_count += 1
            else:
                remaining.append(document)
        self.documents = remaining
        return _DeleteResult(deleted_count)

    def distinct(self, field_name: str, filter_query: dict[str, Any]) -> list[Any]:
        values: list[Any] = []
        for document in self.documents:
            if not self._matches_filter(document, filter_query):
                continue
            value = document.get(field_name)
            if value not in values:
                values.append(value)
        return values

    def aggregate(self, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        documents = [deepcopy(document) for document in self.documents]
        for stage in pipeline:
            if "$match" in stage:
                documents = [
                    document
                    for document in documents
                    if self._matches_filter(document, stage["$match"])
                ]
            elif "$project" in stage:
                documents = [self._apply_projection(document, stage["$project"]) for document in documents]
            elif "$limit" in stage:
                documents = documents[: stage["$limit"]]
            else:
                raise ValueError("Unsupported fake aggregation stage")
        return documents

    def create_index(self, fields: list[tuple[str, Any]], name: str | None = None) -> str:
        self.text_index_fields = [field_name for field_name, _value in fields]
        return name or "_".join(f"{field_name}_text" for field_name in self.text_index_fields)

    def _update(
        self,
        filter_query: dict[str, Any],
        update_operations: dict[str, Any],
        upsert: bool,
        many: bool,
    ) -> _UpdateResult:
        matched_documents = [document for document in self.documents if self._matches_filter(document, filter_query)]

        if not matched_documents and upsert:
            inserted: dict[str, Any] = {}
            for key, value in filter_query.items():
                if not str(key).startswith("$"):
                    inserted[key] = value
            for key, value in update_operations.get("$set", {}).items():
                inserted[key] = value
            result = self.insert_one(inserted)
            return _UpdateResult(0, 0, upserted_id=result.inserted_id)

        modified_count = 0
        for document in matched_documents:
            if "$set" in update_operations:
                for key, value in update_operations["$set"].items():
                    document[key] = value
            if "$unset" in update_operations:
                for key in update_operations["$unset"].keys():
                    document.pop(key, None)
            modified_count += 1
            if not many:
                break

        return _UpdateResult(len(matched_documents), modified_count)

    def _matches_filter(self, document: dict[str, Any], filter_query: dict[str, Any]) -> bool:
        if not filter_query:
            return True

        for key, value in filter_query.items():
            if key == "$and":
                return all(self._matches_filter(document, item) for item in value)
            if key == "$text":
                if not self.text_index_fields:
                    raise ValueError("text index required")
                search_text = str(value.get("$search", "")).lower()
                haystack = " ".join(
                    str(document.get(field_name, ""))
                    for field_name in self.text_index_fields
                ).lower()
                return search_text in haystack
            if isinstance(value, dict) and "$in" in value:
                if document.get(key) not in value["$in"]:
                    return False
                continue
            if document.get(key) != value:
                return False

        return True

    def _apply_projection(
        self,
        document: dict[str, Any],
        projection: dict[str, Any] | None,
    ) -> dict[str, Any]:
        candidate = deepcopy(document)
        if projection is None:
            return candidate

        include_fields = [
            key
            for key, value in projection.items()
            if value == 1 or value is True
        ]
        exclude_fields = [
            key
            for key, value in projection.items()
            if value == 0 or value is False
        ]

        if include_fields:
            projected = {key: candidate[key] for key in include_fields if key in candidate}
            if projection.get("_id", 1) not in {0, False} and "_id" in candidate:
                projected.setdefault("_id", candidate["_id"])
        else:
            projected = dict(candidate)

        for key in exclude_fields:
            projected.pop(key, None)

        for key, value in projection.items():
            if isinstance(value, dict) and value.get("$meta") == "textScore":
                projected[key] = 1.0

        return projected


class _FakeDatabase:
    def __init__(self, name: str) -> None:
        self.name = name
        self._collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, collection_name: str) -> _FakeCollection:
        return self._collections.setdefault(collection_name, _FakeCollection())

    def list_collection_names(self) -> list[str]:
        return list(self._collections.keys())


class _FakeAdmin:
    @staticmethod
    def command(command_name: str) -> dict[str, int]:
        if command_name != "ping":
            raise ValueError("Unsupported admin command")
        return {"ok": 1}


class _FakeMongoClient:
    def __init__(self, default_database_name: str = "appdb") -> None:
        self.admin = _FakeAdmin()
        self._default_database_name = default_database_name
        self._databases: dict[str, _FakeDatabase] = {}

    def __getitem__(self, database_name: str) -> _FakeDatabase:
        return self._databases.setdefault(database_name, _FakeDatabase(database_name))

    def get_default_database(self) -> _FakeDatabase:
        return self[self._default_database_name]


@pytest.fixture
def plugin() -> MongoDBPlugin:
    return MongoDBPlugin(database="appdb", client=_FakeMongoClient())


def test_create_find_update_and_delete_documents(plugin: MongoDBPlugin) -> None:
    created = plugin.create_document(
        "tickets",
        {"title": "Router down", "status": "open", "priority": 2},
    )

    assert created["status"] == "success"
    assert created["document"]["title"] == "Router down"

    found = plugin.find_documents(
        "tickets",
        {"status": "open"},
        ["title", "status"],
        [{"field": "title", "direction": "asc"}],
        10,
        0,
    )

    assert found["count"] == 1
    assert found["documents"][0]["title"] == "Router down"

    updated = plugin.update_documents(
        "tickets",
        {"status": "open"},
        {"$set": {"status": "closed", "owner": "ops"}},
        False,
        True,
    )

    assert updated["matched_count"] == 1
    assert updated["modified_count"] == 1

    fetched = plugin.get_document_by_id("tickets", created["inserted_id"])
    assert fetched["document"]["status"] == "closed"
    assert fetched["document"]["owner"] == "ops"

    deleted = plugin.delete_documents("tickets", {"status": "closed"}, True)
    assert deleted["deleted_count"] == 1
    assert plugin.count_documents("tickets")["count"] == 0


def test_query_helpers_support_insert_many_distinct_and_aggregate(plugin: MongoDBPlugin) -> None:
    created = plugin.create_documents(
        "devices",
        [
            {"name": "edge-1", "site": "north", "role": "router"},
            {"name": "edge-2", "site": "south", "role": "router"},
            {"name": "core-1", "site": "north", "role": "switch"},
        ],
    )

    assert created["inserted_count"] == 3

    distinct = plugin.distinct_values("devices", "site")
    assert distinct["values"] == ["north", "south"]

    aggregate = plugin.aggregate_documents(
        "devices",
        [
            {"$match": {"site": "north"}},
            {"$project": {"name": 1, "role": 1}},
        ],
        5,
    )

    assert aggregate["count"] == 2
    assert aggregate["documents"][0]["name"] in {"edge-1", "core-1"}


def test_text_search_requires_index_and_returns_ranked_documents(plugin: MongoDBPlugin) -> None:
    plugin.create_documents(
        "knowledge",
        [
            {"title": "Firewall runbook", "body": "Reset VPN tunnel and verify health"},
            {"title": "Switch notes", "body": "Check spanning tree settings"},
        ],
    )

    with pytest.raises(ValueError, match="text index"):
        plugin.text_search("knowledge", "VPN")

    index_result = plugin.create_text_index("knowledge", ["title", "body"])
    assert index_result["index_name"]

    search = plugin.text_search("knowledge", "VPN")
    assert search["count"] == 1
    assert search["documents"][0]["title"] == "Firewall runbook"
    assert search["documents"][0]["score"] == 1.0


def test_dangerous_bulk_mutations_require_explicit_opt_in(plugin: MongoDBPlugin) -> None:
    with pytest.raises(ValueError, match="allow_empty_filter"):
        plugin.update_documents("tickets", {}, {"$set": {"status": "closed"}})

    with pytest.raises(ValueError, match="allow_empty_filter"):
        plugin.delete_documents("tickets", {})


def test_list_collections_can_filter_by_name(plugin: MongoDBPlugin) -> None:
    plugin.create_document("tickets", {"title": "one"})
    plugin.create_document("ticket_archive", {"title": "two"})
    plugin.create_document("devices", {"name": "edge-1"})

    result = plugin.list_collections("ticket")

    assert result["collections"] == ["ticket_archive", "tickets"]