"""Persistent jobs embedded atomically in each newly saved Memory revision."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from pymongo import ReturnDocument

from app.services.embeddings import PROFILE

LEASE_SECONDS = 600


def now_utc():
    return datetime.now(timezone.utc)


def pending_embedding(revision):
    return {'state': 'pending', 'revision': revision, 'profile': PROFILE, 'attempts': 0,
            'error': None, 'updated_at': now_utc()}


class EmbeddingJobsRepository:
    def __init__(self, collection):
        self.collection = collection

    def claim(self, uid, memory_id=None):
        now = now_utc()
        query = {'user_uid': uid, 'lifecycle': 'candidate', 'embedding_processing.state': 'pending',
                 'embedding_processing.profile': PROFILE}
        if memory_id is not None:
            query['_id'] = memory_id
        return self.collection.find_one_and_update(
            query,
            {'$set': {'embedding_processing.state': 'running', 'embedding_processing.token': uuid4().hex,
                'embedding_processing.lease_until': now + timedelta(seconds=LEASE_SECONDS),
                'embedding_processing.error': None, 'embedding_processing.updated_at': now},
             '$inc': {'embedding_processing.attempts': 1}},
            sort=[('embedding_processing.updated_at', 1), ('_id', 1)], return_document=ReturnDocument.AFTER)

    def running_query(self, row):
        return {'_id': row['_id'], 'user_uid': row['user_uid'], 'lifecycle': 'candidate',
            'revision': row['revision'], 'embedding_processing.revision': row['revision'],
            'embedding_processing.profile': PROFILE, 'embedding_processing.state': 'running',
            'embedding_processing.token': row['embedding_processing']['token']}

    def touch(self, row):
        now = now_utc()
        return self.collection.update_one(self.running_query(row), {'$set': {
            'embedding_processing.lease_until': now + timedelta(seconds=LEASE_SECONDS),
            'embedding_processing.updated_at': now}}).matched_count == 1

    def finish(self, row, state, *, error=None, cache_id=None, reused=None):
        return self.collection.update_one(self.running_query(row), {'$set': {
            'embedding_processing.state': state, 'embedding_processing.error': error,
            'embedding_processing.cache_id': cache_id, 'embedding_processing.reused': reused,
            'embedding_processing.updated_at': now_utc()}}).matched_count == 1

    def expire(self, uid):
        self.collection.update_many({'user_uid': uid, 'embedding_processing.state': 'running',
            'embedding_processing.profile': PROFILE, 'embedding_processing.lease_until': {'$lte': now_utc()}},
            {'$set': {'embedding_processing.state': 'failed', 'embedding_processing.error': 'interrupted',
                      'embedding_processing.updated_at': now_utc()}})

    def retry(self, uid, memory_id, revision):
        return self.collection.find_one_and_update(
            {'_id': memory_id, 'user_uid': uid, 'revision': revision, 'lifecycle': 'candidate',
             'embedding_processing.state': 'failed', 'embedding_processing.profile': PROFILE,
             'embedding_processing.revision': revision},
            {'$set': {'embedding_processing.state': 'pending', 'embedding_processing.error': None,
                      'embedding_processing.updated_at': now_utc()}}, return_document=ReturnDocument.AFTER)
