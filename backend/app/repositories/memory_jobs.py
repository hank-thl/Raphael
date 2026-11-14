"""Jobs live inside newly created records, so capture and enqueue are atomic."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from bson import ObjectId
from pymongo import ReturnDocument

LEASE_SECONDS = 600


def now_utc():
    return datetime.now(timezone.utc)


def pending_job():
    return {'state': 'pending', 'attempts': 0, 'error': None, 'updated_at': now_utc()}


class MemoryJobsRepository:
    def __init__(self, records):
        self.records = records

    def get(self, uid, record_id):
        return self.records.find_one({'_id': ObjectId(record_id), 'user_uid': uid, 'deleted_at': None})

    def claim(self, uid):
        now, token = now_utc(), uuid4().hex
        return self.records.find_one_and_update(
            {'user_uid': uid, 'deleted_at': None, 'ai_processing.state': 'pending'},
            {'$set': {'ai_processing.state': 'running', 'ai_processing.token': token,
                      'ai_processing.lease_until': now + timedelta(seconds=LEASE_SECONDS),
                      'ai_processing.error': None, 'ai_processing.updated_at': now},
             '$inc': {'ai_processing.attempts': 1}},
            sort=[('created_at', 1), ('_id', 1)], return_document=ReturnDocument.AFTER)

    def touch(self, row):
        now = now_utc()
        result = self.records.update_one(self.running_query(row), {'$set': {
            'ai_processing.lease_until': now + timedelta(seconds=LEASE_SECONDS), 'ai_processing.updated_at': now}})
        return result.matched_count == 1

    def running_query(self, row):
        return {'_id': row['_id'], 'user_uid': row['user_uid'], 'deleted_at': None,
                'ai_processing.state': 'running', 'ai_processing.token': row['ai_processing']['token']}

    def finish(self, row, state, error=None, analysis_id=None, routing=None):
        return self.records.update_one(self.running_query(row), {'$set': {
            'ai_processing.state': state, 'ai_processing.error': error,
            'ai_processing.analysis_id': analysis_id, 'ai_processing.routing': routing or [],
            'ai_processing.updated_at': now_utc()}}).matched_count == 1

    def expire(self, uid):
        self.records.update_many({'user_uid': uid, 'deleted_at': None, 'ai_processing.state': 'running',
            'ai_processing.lease_until': {'$lte': now_utc()}}, {'$set': {
                'ai_processing.state': 'failed', 'ai_processing.error': 'interrupted', 'ai_processing.updated_at': now_utc()}})

    def retry(self, uid, record_id):
        # No job field means an old/ineligible record. Never backfill on retry.
        return self.records.find_one_and_update(
            {'_id': ObjectId(record_id), 'user_uid': uid, 'deleted_at': None, 'ai_processing.state': 'failed'},
            {'$set': {'ai_processing.state': 'pending', 'ai_processing.error': None,
                      'ai_processing.updated_at': now_utc()}}, return_document=ReturnDocument.AFTER)
