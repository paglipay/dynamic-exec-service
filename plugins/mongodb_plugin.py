"""MongoDB plugin with JSON-friendly CRUD and query helpers."""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

try:
    from bson import ObjectId
except Exception:  # pragma: no cover - dependency may be optional in some environments
    ObjectId = None  # type: ignore[assignment]

try:
    from pymongo import ASCENDING, DESCENDING, TEXT, MongoClient
except Exception:  # pragma: no cover - dependency may be optional in some environments
    ASCENDING = 1  # type: ignore[assignment]
    DESCENDING = -1  # type: ignore[assignment]
    TEXT = "text"  # type: ignore[assignment]
    MongoClient = None  # type: ignore[assignment]


class MongoDBPlugin:
    """CRUD and search helpers for one MongoDB database."""

    def __init__(
        self,
        uri: str | None = None,
        database: str | None = None,
        server_selection_timeout_ms: int = 5000,
        client: Any | None = None,
    ) -> None:
        if not isinstance(server_selection_timeout_ms, int) or server_selection_timeout_ms <= 0:
            raise ValueError("server_selection_timeout_ms must be a positive integer")

        resolved_database = self._resolve_database_name(database)

        if client is not None:
            self.client = client
        else:
            if MongoClient is None:
                raise ValueError("pymongo must be installed to use MongoDBPlugin")

            resolved_uri = uri or os.getenv("MONGODB_URI")
            if not isinstance(resolved_uri, str) or not resolved_uri.strip():
                raise ValueError("uri must be provided (or set MONGODB_URI)")

            self.client = MongoClient(
                resolved_uri.strip(),
                serverSelectionTimeoutMS=server_selection_timeout_ms,
            )

        if resolved_database is None:
            resolved_database = self._infer_default_database_name()

        if not isinstance(resolved_database, str) or not resolved_database.strip():
            raise ValueError(
                "database must be provided (or set MONGODB_DATABASE, or include it in MONGODB_URI)"
            )

        self.database_name = resolved_database.strip()
        self.db = self.client[self.database_name]
        self._ping()

    def _resolve_database_name(self, database: str | None) -> str | None:
        if database is not None:
            if not isinstance(database, str) or not database.strip():
                raise ValueError("database must be a non-empty string when provided")
            return database.strip()

        env_database = os.getenv("MONGODB_DATABASE")
        if env_database and env_database.strip():
            return env_database.strip()

        return None

    def _infer_default_database_name(self) -> str | None:
        try:
            default_db = self.client.get_default_database()
        except Exception:
            return None

        name = getattr(default_db, "name", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None

    def _ping(self) -> None:
        try:
            self.client.admin.command("ping")
        except Exception as exc:
            raise ValueError(f"Failed to connect to MongoDB: {exc}") from exc

    def _validate_collection_name(self, collection: str) -> str:
        if not isinstance(collection, str) or not collection.strip():
            raise ValueError("collection must be a non-empty string")

        normalized = collection.strip()
        if normalized.startswith("system."):
            raise ValueError("system collections are not allowed")
        if "$" in normalized or "\x00" in normalized:
            raise ValueError("collection contains invalid characters")

        return normalized

    def _get_collection(self, collection: str) -> Any:
        return self.db[self._validate_collection_name(collection)]

    def _normalize_limit(self, limit: int, *, default: int, maximum: int) -> int:
        if limit is None:
            return default
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if limit > maximum:
            raise ValueError(f"limit must be <= {maximum}")
        return limit

    def _normalize_skip(self, skip: int) -> int:
        if not isinstance(skip, int) or skip < 0:
            raise ValueError("skip must be an integer >= 0")
        return skip

    def _normalize_filter(self, filter_query: dict[str, Any] | None) -> dict[str, Any]:
        if filter_query is None:
            return {}
        if not isinstance(filter_query, dict):
            raise ValueError("filter_query must be an object when provided")
        return self._normalize_bson_value(filter_query)

    def _normalize_document(self, document: dict[str, Any], *, allow_operators: bool) -> dict[str, Any]:
        if not isinstance(document, dict) or not document:
            raise ValueError("document must be a non-empty object")

        if not allow_operators and any(str(key).startswith("$") for key in document):
            raise ValueError("replacement documents cannot contain update operators")

        return self._normalize_bson_value(document)

    def _normalize_update(self, update_operations: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(update_operations, dict) or not update_operations:
            raise ValueError("update_operations must be a non-empty object")
        if not any(str(key).startswith("$") for key in update_operations):
            raise ValueError("update_operations must contain MongoDB update operators")
        return self._normalize_bson_value(update_operations)

    def _normalize_projection(self, projection: dict[str, Any] | list[str] | None) -> dict[str, Any] | None:
        if projection is None:
            return None
        if isinstance(projection, list):
            if not projection or any(not isinstance(field, str) or not field.strip() for field in projection):
                raise ValueError("projection lists must contain non-empty field names")
            return {field.strip(): 1 for field in projection}
        if not isinstance(projection, dict):
            raise ValueError("projection must be an object or array of field names")
        return projection

    def _normalize_sort(self, sort: list[Any] | None) -> list[tuple[str, int]] | None:
        if sort is None:
            return None
        if not isinstance(sort, list) or not sort:
            raise ValueError("sort must be a non-empty array when provided")

        normalized_sort: list[tuple[str, int]] = []
        for item in sort:
            field_name: str | None = None
            direction_value: Any = None

            if isinstance(item, dict):
                field_name = item.get("field")
                direction_value = item.get("direction", "asc")
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                field_name = item[0]
                direction_value = item[1]
            else:
                raise ValueError("each sort item must be {field, direction} or [field, direction]")

            if not isinstance(field_name, str) or not field_name.strip():
                raise ValueError("sort field must be a non-empty string")

            normalized_sort.append((field_name.strip(), self._normalize_sort_direction(direction_value)))

        return normalized_sort

    def _normalize_sort_direction(self, direction: Any) -> int:
        if direction in {1, "1", "asc", "ascending", "ASC", "ASCENDING"}:
            return ASCENDING
        if direction in {-1, "-1", "desc", "descending", "DESC", "DESCENDING"}:
            return DESCENDING
        raise ValueError("sort direction must be asc/desc or 1/-1")

    def _normalize_pipeline(self, pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not isinstance(pipeline, list) or not pipeline:
            raise ValueError("pipeline must be a non-empty array")
        normalized_pipeline: list[dict[str, Any]] = []
        for stage in pipeline:
            if not isinstance(stage, dict) or not stage:
                raise ValueError("each pipeline stage must be a non-empty object")
            normalized_pipeline.append(self._normalize_bson_value(stage))
        return normalized_pipeline

    def _normalize_bson_value(self, value: Any, field_name: str | None = None) -> Any:
        if isinstance(value, dict):
            if set(value.keys()) == {"$oid"} and isinstance(value.get("$oid"), str):
                return self._coerce_object_id(value["$oid"])
            return {
                key: self._normalize_bson_value(item, field_name=str(key))
                for key, item in value.items()
            }

        if isinstance(value, list):
            return [self._normalize_bson_value(item, field_name=field_name) for item in value]

        if field_name == "_id" and isinstance(value, str):
            return self._coerce_object_id(value)

        return value

    def _coerce_object_id(self, value: Any) -> Any:
        if not isinstance(value, str) or ObjectId is None:
            return value
        try:
            return ObjectId(value) if ObjectId.is_valid(value) else value
        except Exception:
            return value

    def _serialize_value(self, value: Any) -> Any:
        if ObjectId is not None and isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if isinstance(value, dict):
            return {key: self._serialize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._serialize_value(item) for item in value]
        if isinstance(value, tuple):
            return [self._serialize_value(item) for item in value]
        return value

    def ping(self) -> dict[str, Any]:
        self._ping()
        return {
            "status": "success",
            "action": "ping",
            "database": self.database_name,
        }

    def list_collections(self, name_contains: str | None = None) -> dict[str, Any]:
        if name_contains is not None and (not isinstance(name_contains, str) or not name_contains.strip()):
            raise ValueError("name_contains must be a non-empty string when provided")

        names = sorted(self.db.list_collection_names())
        if name_contains is not None:
            needle = name_contains.strip().lower()
            names = [name for name in names if needle in name.lower()]

        return {
            "status": "success",
            "action": "list_collections",
            "database": self.database_name,
            "collections": names,
            "count": len(names),
        }

    def create_document(self, collection: str, document: dict[str, Any]) -> dict[str, Any]:
        target = self._get_collection(collection)
        payload = self._normalize_document(document, allow_operators=False)
        result = target.insert_one(payload)
        created_document = target.find_one({"_id": result.inserted_id})

        return {
            "status": "success",
            "action": "create_document",
            "collection": self._validate_collection_name(collection),
            "inserted_id": self._serialize_value(result.inserted_id),
            "document": self._serialize_value(created_document),
        }

    def create_documents(
        self,
        collection: str,
        documents: list[dict[str, Any]],
        ordered: bool = True,
    ) -> dict[str, Any]:
        if not isinstance(ordered, bool):
            raise ValueError("ordered must be a boolean")
        if not isinstance(documents, list) or not documents:
            raise ValueError("documents must be a non-empty array")

        target = self._get_collection(collection)
        payloads = [self._normalize_document(document, allow_operators=False) for document in documents]
        result = target.insert_many(payloads, ordered=ordered)

        return {
            "status": "success",
            "action": "create_documents",
            "collection": self._validate_collection_name(collection),
            "inserted_ids": self._serialize_value(result.inserted_ids),
            "inserted_count": len(result.inserted_ids),
        }

    def get_document_by_id(
        self,
        collection: str,
        document_id: Any,
        projection: dict[str, Any] | list[str] | None = None,
    ) -> dict[str, Any]:
        target = self._get_collection(collection)
        normalized_projection = self._normalize_projection(projection)
        normalized_id = self._coerce_object_id(document_id)
        document = target.find_one({"_id": normalized_id}, normalized_projection)
        if document is None:
            raise ValueError("document not found")

        return {
            "status": "success",
            "action": "get_document_by_id",
            "collection": self._validate_collection_name(collection),
            "document": self._serialize_value(document),
        }

    def find_documents(
        self,
        collection: str,
        filter_query: dict[str, Any] | None = None,
        projection: dict[str, Any] | list[str] | None = None,
        sort: list[Any] | None = None,
        limit: int = 25,
        skip: int = 0,
    ) -> dict[str, Any]:
        target = self._get_collection(collection)
        normalized_filter = self._normalize_filter(filter_query)
        normalized_projection = self._normalize_projection(projection)
        normalized_sort = self._normalize_sort(sort)
        normalized_limit = self._normalize_limit(limit, default=25, maximum=200)
        normalized_skip = self._normalize_skip(skip)

        cursor = target.find(normalized_filter, normalized_projection)
        if normalized_sort:
            cursor = cursor.sort(normalized_sort)
        cursor = cursor.skip(normalized_skip).limit(normalized_limit)
        documents = [self._serialize_value(document) for document in cursor]

        return {
            "status": "success",
            "action": "find_documents",
            "collection": self._validate_collection_name(collection),
            "count": len(documents),
            "documents": documents,
            "limit": normalized_limit,
            "skip": normalized_skip,
        }

    def count_documents(
        self,
        collection: str,
        filter_query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target = self._get_collection(collection)
        normalized_filter = self._normalize_filter(filter_query)
        count = target.count_documents(normalized_filter)

        return {
            "status": "success",
            "action": "count_documents",
            "collection": self._validate_collection_name(collection),
            "count": count,
        }

    def update_documents(
        self,
        collection: str,
        filter_query: dict[str, Any],
        update_operations: dict[str, Any],
        upsert: bool = False,
        many: bool = True,
        allow_empty_filter: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(upsert, bool):
            raise ValueError("upsert must be a boolean")
        if not isinstance(many, bool):
            raise ValueError("many must be a boolean")
        if not isinstance(allow_empty_filter, bool):
            raise ValueError("allow_empty_filter must be a boolean")

        target = self._get_collection(collection)
        normalized_filter = self._normalize_filter(filter_query)
        if not normalized_filter and not allow_empty_filter:
            raise ValueError("filter_query cannot be empty unless allow_empty_filter is true")

        normalized_update = self._normalize_update(update_operations)
        result = (
            target.update_many(normalized_filter, normalized_update, upsert=upsert)
            if many
            else target.update_one(normalized_filter, normalized_update, upsert=upsert)
        )

        return {
            "status": "success",
            "action": "update_documents",
            "collection": self._validate_collection_name(collection),
            "matched_count": result.matched_count,
            "modified_count": result.modified_count,
            "upserted_id": self._serialize_value(getattr(result, "upserted_id", None)),
            "many": many,
            "upsert": upsert,
        }

    def replace_document(
        self,
        collection: str,
        filter_query: dict[str, Any],
        replacement: dict[str, Any],
        upsert: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(upsert, bool):
            raise ValueError("upsert must be a boolean")

        target = self._get_collection(collection)
        normalized_filter = self._normalize_filter(filter_query)
        if not normalized_filter:
            raise ValueError("filter_query cannot be empty")

        normalized_replacement = self._normalize_document(replacement, allow_operators=False)
        result = target.replace_one(normalized_filter, normalized_replacement, upsert=upsert)

        return {
            "status": "success",
            "action": "replace_document",
            "collection": self._validate_collection_name(collection),
            "matched_count": result.matched_count,
            "modified_count": result.modified_count,
            "upserted_id": self._serialize_value(getattr(result, "upserted_id", None)),
            "upsert": upsert,
        }

    def delete_documents(
        self,
        collection: str,
        filter_query: dict[str, Any],
        many: bool = True,
        allow_empty_filter: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(many, bool):
            raise ValueError("many must be a boolean")
        if not isinstance(allow_empty_filter, bool):
            raise ValueError("allow_empty_filter must be a boolean")

        target = self._get_collection(collection)
        normalized_filter = self._normalize_filter(filter_query)
        if not normalized_filter and not allow_empty_filter:
            raise ValueError("filter_query cannot be empty unless allow_empty_filter is true")

        result = target.delete_many(normalized_filter) if many else target.delete_one(normalized_filter)

        return {
            "status": "success",
            "action": "delete_documents",
            "collection": self._validate_collection_name(collection),
            "deleted_count": result.deleted_count,
            "many": many,
        }

    def distinct_values(
        self,
        collection: str,
        field_name: str,
        filter_query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(field_name, str) or not field_name.strip():
            raise ValueError("field_name must be a non-empty string")

        target = self._get_collection(collection)
        normalized_filter = self._normalize_filter(filter_query)
        values = [self._serialize_value(item) for item in target.distinct(field_name.strip(), normalized_filter)]

        return {
            "status": "success",
            "action": "distinct_values",
            "collection": self._validate_collection_name(collection),
            "field_name": field_name.strip(),
            "values": values,
            "count": len(values),
        }

    def aggregate_documents(
        self,
        collection: str,
        pipeline: list[dict[str, Any]],
        limit: int = 50,
    ) -> dict[str, Any]:
        target = self._get_collection(collection)
        normalized_limit = self._normalize_limit(limit, default=50, maximum=200)
        normalized_pipeline = self._normalize_pipeline(pipeline)
        normalized_pipeline.append({"$limit": normalized_limit})
        documents = [self._serialize_value(document) for document in target.aggregate(normalized_pipeline)]

        return {
            "status": "success",
            "action": "aggregate_documents",
            "collection": self._validate_collection_name(collection),
            "count": len(documents),
            "documents": documents,
            "limit": normalized_limit,
        }

    def create_text_index(
        self,
        collection: str,
        fields: list[str],
        index_name: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(fields, list) or not fields:
            raise ValueError("fields must be a non-empty array")
        if any(not isinstance(field, str) or not field.strip() for field in fields):
            raise ValueError("fields must contain non-empty strings")
        if index_name is not None and (not isinstance(index_name, str) or not index_name.strip()):
            raise ValueError("index_name must be a non-empty string when provided")

        target = self._get_collection(collection)
        index_fields = [(field.strip(), TEXT) for field in fields]
        created_index_name = target.create_index(index_fields, name=index_name.strip() if index_name else None)

        return {
            "status": "success",
            "action": "create_text_index",
            "collection": self._validate_collection_name(collection),
            "fields": [field.strip() for field in fields],
            "index_name": created_index_name,
        }

    def text_search(
        self,
        collection: str,
        search_text: str,
        filter_query: dict[str, Any] | None = None,
        projection: dict[str, Any] | list[str] | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        if not isinstance(search_text, str) or not search_text.strip():
            raise ValueError("search_text must be a non-empty string")

        target = self._get_collection(collection)
        normalized_limit = self._normalize_limit(limit, default=10, maximum=100)
        normalized_filter = self._normalize_filter(filter_query)
        normalized_projection = self._normalize_projection(projection) or {}
        normalized_projection["score"] = {"$meta": "textScore"}

        search_filter: dict[str, Any] = {"$text": {"$search": search_text.strip()}}
        if normalized_filter:
            search_filter = {"$and": [normalized_filter, search_filter]}

        documents = [
            self._serialize_value(document)
            for document in target.find(search_filter, normalized_projection).sort([("score", {"$meta": "textScore"})]).limit(normalized_limit)
        ]

        return {
            "status": "success",
            "action": "text_search",
            "collection": self._validate_collection_name(collection),
            "search_text": search_text.strip(),
            "count": len(documents),
            "documents": documents,
            "limit": normalized_limit,
        }