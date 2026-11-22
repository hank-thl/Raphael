"""Owner-scoped, idempotent question/answer documents; separate from raw records."""
import base64
import binascii
import json
import re
from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.repositories.records import utc_now
from app.services.memory_capture import digest


def identifier(uid, request_id):
    return digest(['conversation-turn-v1', uid, request_id])


def encode_cursor(row):
    return base64.urlsafe_b64encode(json.dumps([row['created_at'].isoformat(), row['_id']]).encode()).decode()


def decode_cursor(value):
    try:
        values = json.loads(base64.b64decode(value, altchars=b'-_', validate=True))
        if not isinstance(values, list) or len(values) != 2 or not all(isinstance(v, str) for v in values):
            raise ValueError
        timestamp = datetime.fromisoformat(values[0])
        if timestamp.tzinfo is None or not re.fullmatch('[a-f0-9]{64}', values[1]):
            raise ValueError
        return timestamp.astimezone(timezone.utc), values[1]
    except (ValueError, TypeError, binascii.Error, OverflowError):
        raise ValueError('invalid_cursor') from None


class ConversationsRepository:
    def __init__(self, collection):
        self.collection = collection

    def get(self, uid, request_id):
        return self.collection.find_one({'_id': identifier(uid, request_id), 'user_uid': uid})

    def start(self, uid, request_id, question, *, source_record_id=None, record_inputs=None):
        now = utc_now()
        row = {'_id': identifier(uid, request_id), 'request_id': request_id, 'user_uid': uid,
               'question': question, 'state': 'running', 'answer': None, 'inputs': [],
               'error': None, 'created_at': now, 'updated_at': now,
               'lease_until': now + timedelta(minutes=5)}
        if source_record_id is not None:
            row.update(source_record_id=source_record_id, record_inputs=record_inputs or [])
        try:
            self.collection.insert_one(row)
            return row, True
        except DuplicateKeyError:
            existing = self.get(uid, request_id)
            if (existing is None or existing['question'] != question
                    or existing.get('source_record_id') != source_record_id):
                raise ValueError('request_conflict') from None
            return existing, False

    def finish(self, row, state, *, answer=None, inputs=None, error=None, processing=None, record_inputs=None):
        now = utc_now()
        values = {'state': state, 'answer': answer, 'inputs': inputs or [], 'error': error,
                  'processing': processing, 'updated_at': now}
        if record_inputs is not None:
            values['record_inputs'] = record_inputs
        return self.collection.find_one_and_update(
            {'_id': row['_id'], 'user_uid': row['user_uid'], 'state': 'running', 'lease_until': {'$gt': now}},
            {'$set': values}, return_document=ReturnDocument.AFTER)

    def page(self, uid, limit=20, cursor=None):
        query = {'user_uid': uid}
        if cursor is not None:
            stamp, key = cursor
            query['$or'] = [{'created_at': {'$lt': stamp}}, {'created_at': stamp, '_id': {'$lt': key}}]
        return list(self.collection.find(query).sort([('created_at', -1), ('_id', -1)]).limit(limit + 1))
