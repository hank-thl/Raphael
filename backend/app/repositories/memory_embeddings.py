"""Immutable cache entries; Mongo's built-in _id index provides idempotency."""
from pymongo.errors import DuplicateKeyError


class MemoryEmbeddingsRepository:
    def __init__(self, collection):
        self.collection = collection

    def get(self, uid, identifier):
        return self.collection.find_one({'_id': identifier, 'user_uid': uid})

    def get_many(self, uid, identifiers):
        if not identifiers:
            return []
        return list(self.collection.find({'user_uid': uid, '_id': {'$in': list(identifiers)}}))

    def create_once(self, document):
        try:
            self.collection.insert_one(document)
        except DuplicateKeyError:
            pass
        return self.get(document['user_uid'], document['_id'])
