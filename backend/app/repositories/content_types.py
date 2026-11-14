"""Owner-scoped content types; MongoDB's unique _id handles concurrent creation."""
import hashlib
import unicodedata
from datetime import datetime, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


def normalize_name(value: str) -> str:
    name = ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()
    if not name or len(name) > 64 or any(unicodedata.category(c).startswith('C') for c in name):
        raise ValueError('Invalid content type name')
    return name


class ContentTypesRepository:
    def __init__(self, collection):
        self.collection = collection

    def list_names(self, uid: str) -> list[str]:
        if not uid:
            raise ValueError('Owner required')
        rows = list(self.collection.find({'user_uid': uid}, {'name': 1}).sort('_id', 1).limit(101))
        # Fail explicitly instead of silently omitting existing categories.
        if len(rows) > 100:
            raise ValueError('Content type catalog exceeds analysis limit')
        return [normalize_name(row['name']) for row in rows]

    def resolve(self, uid: str, name: str) -> dict:
        if not uid:
            raise ValueError('Owner required')
        name = normalize_name(name)
        identifier = hashlib.sha256((uid + '\0' + name).encode()).hexdigest()
        query = {'_id': identifier, 'user_uid': uid, 'name': name}
        try:
            return self.collection.find_one_and_update(
                query, {'$setOnInsert': {'created_at': datetime.now(timezone.utc)}},
                upsert=True, return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # Another analysis may have inserted the same name concurrently.
            row = self.collection.find_one(query)
            if row is None:
                raise ValueError('Content type conflict') from None
            return row
