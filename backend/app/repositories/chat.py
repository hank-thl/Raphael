"""One canonical user record per request; bounded pagination over existing histories."""
import base64
import json
import re
from datetime import datetime, timezone

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.repositories.memory_jobs import pending_job
from app.repositories.records import utc_now
from app.services.memory_capture import digest


def record_id(uid, request_id):
    return ObjectId(digest(['chat-record-v1', uid, request_id])[:24])


def encode_cursor(row):
    return base64.urlsafe_b64encode(json.dumps([
        row['created_at'].isoformat(), row['kind'], str(row['_id'])]).encode()).decode()


def decode_cursor(value):
    try:
        stamp, kind, identifier = json.loads(base64.b64decode(value, altchars=b'-_', validate=True))
        stamp = datetime.fromisoformat(stamp)
        if stamp.tzinfo is None or kind not in ('record', 'conversation'):
            raise ValueError()
        if not isinstance(identifier, str) or not re.fullmatch(
                '[a-f0-9]{24}' if kind == 'record' else '[a-f0-9]{64}', identifier):
            raise ValueError()
        return stamp.astimezone(timezone.utc), kind, identifier
    except (ValueError, TypeError, OverflowError):
        raise ValueError('invalid_cursor') from None


class ChatRepository:
    def __init__(self, records, conversations):
        self.records, self.conversations = records, conversations

    def get(self, uid, request_id):
        return self.records.find_one({'_id': record_id(uid, request_id), 'user_uid': uid,
                                      'chat_request_id': request_id})

    def save(self, uid, request_id, content, mode, *, analyze):
        row = {'_id': record_id(uid, request_id), 'user_uid': uid, 'content': content,
               'created_at': utc_now(), 'deleted_at': None,
               'chat_request_id': request_id, 'chat_mode': mode}
        if analyze:
            row['ai_processing'] = pending_job()
        try:
            self.records.insert_one(row)
        except DuplicateKeyError:
            row = self.get(uid, request_id)
            if row is None or row['content'] != content or row['chat_mode'] != mode:
                raise ValueError('request_conflict') from None
            if row.get('deleted_at') is not None:
                raise ValueError('source_unavailable')
        return row

    def page(self, uid, limit=20, cursor=None, *, include_dialogue=True):
        groups = []
        streams = [('record', self.records, {'user_uid': uid, 'deleted_at': None})]
        if include_dialogue:
            streams.append(('conversation', self.conversations,
                            {'user_uid': uid, 'source_record_id': None}))
        for kind, collection, query in streams:
            if cursor:
                stamp, cursor_kind, key = cursor
                boundary = [{'created_at': {'$lt': stamp}}]
                if kind < cursor_kind:
                    boundary.append({'created_at': stamp})
                elif kind == cursor_kind:
                    boundary.append({'created_at': stamp, '_id': {'$lt': ObjectId(key) if kind == 'record' else key}})
                query['$or'] = boundary
            groups.extend({**row, 'kind': kind} for row in collection.find(query)
                          .sort([('created_at', -1), ('_id', -1)]).limit(limit + 1))
        return sorted(groups, key=lambda row: (row['created_at'], row['kind'], str(row['_id'])), reverse=True)[:limit + 1]

    def recent_records(self, uid, target, limit=3):
        rows = list(self.records.find({'user_uid': uid, 'deleted_at': None, '$or': [
            {'created_at': {'$lt': target['created_at']}},
            {'created_at': target['created_at'], '_id': {'$lt': target['_id']}}]})
            .sort([('created_at', -1), ('_id', -1)]).limit(limit))
        return [target, *rows]
