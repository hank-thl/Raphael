"""Summary cache in existing analyses collection, separated by document_type."""
from pymongo import ReturnDocument


class TopicSummariesRepository:
    def __init__(self, collection): self.collection = collection

    def get(self, uid, identifier):
        return self.collection.find_one({'_id': identifier, 'user_uid': uid, 'document_type': 'topic_summary'})

    def save(self, document):
        return self.collection.find_one_and_update(
            {'_id': document['_id'], 'user_uid': document['user_uid'], 'document_type': 'topic_summary'},
            {'$set': {key: value for key, value in document.items() if key != '_id'}},
            upsert=True, return_document=ReturnDocument.AFTER)
