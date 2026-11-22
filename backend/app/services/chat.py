"""Unified timeline without copying legacy dialogue into personal records."""
from app.repositories.chat import encode_cursor
from app.repositories.conversations import identifier
from app.repositories.records import record_response


class ChatService:
    def __init__(self, repository, conversations):
        self.repository, self.conversations = repository, conversations

    def entry(self, uid, row, *, capture, ai, turns=None):
        if row.get('kind') == 'conversation':
            turn = turns[row['_id']] if turns is not None else self.conversations.public(uid, [row])[0]
            return {'id': f"conversation:{row['_id']}", 'created_at': row['created_at'],
                    'record': None, 'turn': turn, 'mode': 'dialogue', 'request_id': row['request_id'],
                    'needs_answer': False}
        record = record_response(row, include_processing=capture).model_dump(mode='json')
        turn = None
        rid = row.get('chat_request_id')
        dialogue = row.get('chat_mode') == 'dialogue'
        if ai and rid and dialogue:
            if turns is not None:
                turn = turns.get(identifier(uid, rid))
            else:
                saved = self.conversations.repository.get(uid, rid)
                if saved and saved.get('source_record_id') == str(row['_id']):
                    turn = self.conversations.public(uid, [saved])[0]
        return {'id': f"record:{row['_id']}", 'created_at': row['created_at'], 'record': record,
                'turn': turn, 'mode': 'dialogue' if dialogue else 'record', 'request_id': rid,
                'needs_answer': bool(ai and dialogue and turn is None)}

    def page(self, uid, limit, cursor, *, capture, ai):
        rows = self.repository.page(uid, limit, cursor, include_dialogue=ai)
        selected = rows[:limit]
        turn_rows = [row for row in selected if row['kind'] == 'conversation']
        record_ids = [str(row['_id']) for row in selected if row['kind'] == 'record']
        if ai and record_ids:
            turn_rows.extend(self.repository.conversations.find({'user_uid': uid, 'source_record_id': {'$in': record_ids}}))
        turns = {row['id']: row for row in self.conversations.public(uid, turn_rows)} if turn_rows else {}
        return {'items': [self.entry(uid, row, capture=capture, ai=ai, turns=turns) for row in selected],
                'next_cursor': encode_cursor(rows[limit - 1]) if len(rows) > limit else None}

    def get(self, uid, request_id, *, capture, ai):
        row = self.repository.get(uid, request_id)
        if row is None or row.get('deleted_at') is not None:
            return None
        return self.entry(uid, row, capture=capture, ai=ai)

    def send(self, uid, request_id, content, mode, *, capture, ai):
        # Reject key collisions with old independent dialogue before saving a Record.
        old = self.repository.conversations.find_one({'_id': identifier(uid, request_id), 'user_uid': uid})
        if old and not old.get('source_record_id'):
            raise ValueError('request_conflict')
        row = self.repository.save(uid, request_id, content, mode, analyze=capture)
        if mode == 'dialogue':
            self.conversations.ask(uid, request_id, content, source_record=row,
                recent_records=self.repository.recent_records(uid, row)[1:])
        current = self.get(uid, request_id, capture=capture, ai=ai)
        if current is None:
            raise ValueError('source_unavailable')
        return current
