"""Explicit capture only. Callers must supply a verified owner, never browser UID."""
import hashlib
import json
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId

from app.schemas.extraction import ExtractionInput, ExtractionResult, SourceRecord
from app.services.gemini import CONTRACT_VERSION, PROMPT_VERSION


class CaptureError(Exception):
    """Fixed safe reasons; source content and driver details are never included."""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def snapshots(data):
    return [row.model_dump(mode='json') for row in [data.target, *data.context]]


class MemoryCaptureService:
    def __init__(self, records, analyses, classifier, model: str):
        self.records = records
        self.analyses = analyses
        self.classifier = classifier
        self.model = model

    def load(self, uid, target_id, context_ids, timezone_name):
        if not uid or not isinstance(uid, str):
            raise CaptureError('owner_required')
        if len(context_ids) > 5 or len(set([target_id, *context_ids])) != len(context_ids) + 1:
            raise CaptureError('invalid_sources')
        rows = []
        for identifier in [target_id, *context_ids]:
            try:
                oid = ObjectId(identifier)
            except (InvalidId, TypeError):
                raise CaptureError('source_unavailable') from None
            row = self.records.find_one({'_id': oid, 'user_uid': uid, 'deleted_at': None})
            if row is None:
                # Same result for deleted, missing or another owner's record.
                raise CaptureError('source_unavailable')
            rows.append(SourceRecord(record_id=str(row['_id']), content=row['content'], created_at=row['created_at']))
        return ExtractionInput(target=rows[0], context=rows[1:], timezone=timezone_name)

    def capture(self, uid, target_id, context_ids=(), timezone_name='Asia/Taipei'):
        data = self.load(uid, target_id, context_ids, timezone_name)
        source_snapshot = snapshots(data)
        identifier = digest({'uid': uid, 'sources': source_snapshot, 'timezone': timezone_name,
                             'model': self.model, 'prompt': PROMPT_VERSION, 'contract': CONTRACT_VERSION})
        existing = self.analyses.get(uid, identifier)
        if existing is not None:
            return existing, True
        result, category_id = self.classifier.analyze(uid, data)
        return self.persist(uid, data, result, category_id, identifier, {
            'state': 'succeeded', 'provider': 'gemini', 'model': self.model,
            'prompt_version': PROMPT_VERSION, 'contract_version': CONTRACT_VERSION}), False

    def persist(self, uid, data, result, category_id, identifier, processing):
        target_id = data.target.record_id
        context_ids = [row.record_id for row in data.context]
        timezone_name = data.timezone
        source_snapshot = snapshots(data)
        # Revalidate even if an alternate classifier is plugged in.
        result = ExtractionResult.model_validate(result.model_dump())
        result.validate_sources(data)
        current = self.load(uid, target_id, context_ids, timezone_name)
        if snapshots(current) != source_snapshot:
            raise CaptureError('sources_changed')
        now = datetime.now(timezone.utc)
        memories = []
        for index, item in enumerate(result.items):
            memories.append({'_id': digest([identifier, index]), 'user_uid': uid,
                             **item.model_dump(), 'lifecycle': 'candidate', 'created_at': now})
        document = {'_id': identifier, 'user_uid': uid, 'target_record_id': target_id,
                    'context_record_ids': list(context_ids), 'source_snapshot': source_snapshot,
                    'timezone': timezone_name, 'content_type_id': category_id,
                    'analysis': result.model_dump(exclude={'items'}), 'memories': memories,
                    'processing': processing,
                    'created_at': now}
        return self.analyses.create_once(document)

    def get_eligible(self, uid, identifier):
        document = self.analyses.get(uid, identifier)
        if document is None:
            return None
        try:
            current = self.load(uid, document['target_record_id'], document['context_record_ids'], document['timezone'])
        except CaptureError:
            return None
        if snapshots(current) != document['source_snapshot']:
            return None
        # Consumers must use this path, not raw collection reads, for source eligibility.
        return document
