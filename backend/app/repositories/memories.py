"""One logical memory with immutable embedded revisions; optimistic concurrency."""
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


class VersionConflict(ValueError):
    pass


class MemoriesRepository:
    def __init__(self, collection):
        self.collection = collection

    def get(self, uid, identifier):
        return self.collection.find_one({'_id': identifier, 'user_uid': uid})

    def create_once(self, document):
        try:
            self.collection.insert_one(document)
            return document
        except DuplicateKeyError:
            existing = self.get(document['user_uid'], document['_id'])
            if existing is None:
                raise VersionConflict('memory_conflict') from None
            return existing

    def append(self, uid, identifier, expected_revision, version):
        updated = self.collection.find_one_and_update(
            {'_id': identifier, 'user_uid': uid, 'revision': expected_revision},
            {'$push': {'versions': version}, '$inc': {'revision': 1},
             '$set': {'updated_at': version['created_at']}},
            return_document=ReturnDocument.AFTER,
        )
        if updated is None:
            raise VersionConflict('revision_conflict')
        return updated
