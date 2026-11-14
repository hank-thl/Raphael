"""Atomically save one analysis and its embedded memory candidates."""
from pymongo.errors import DuplicateKeyError


class AnalysesRepository:
    def __init__(self, collection):
        self.collection = collection

    def get(self, uid: str, identifier: str):
        return self.collection.find_one({'_id': identifier, 'user_uid': uid})

    def create_once(self, document):
        try:
            self.collection.insert_one(document)
            return document
        except DuplicateKeyError:
            row = self.get(document['user_uid'], document['_id'])
            if row is None:
                raise ValueError('Analysis identity conflict') from None
            return row
