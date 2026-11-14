"""Opt-in synthetic capture trial in isolated MongoDB collections; no private records."""
import argparse
import hashlib
import json

from bson import ObjectId

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.analyses import AnalysesRepository
from app.repositories.content_types import ContentTypesRepository
from app.services.classification import ClassificationService
from app.services.gemini import GeminiError, GeminiExtractor
from app.services.memory_capture import CaptureError, MemoryCaptureService
from scripts.try_gemini import example

OWNER = '__raphael_synthetic_memory_v1__'


def seed(records, case):
    data = example(case)
    ids = []
    for source in [data.target, *data.context]:
        identifier = ObjectId(hashlib.sha256(f'{OWNER}:{case}:{source.record_id}'.encode()).hexdigest()[:24])
        records.update_one({'_id': identifier, 'user_uid': OWNER}, {'$setOnInsert': {
            'content': source.content, 'created_at': source.created_at, 'deleted_at': None,
        }}, upsert=True)
        ids.append(str(identifier))
    return ids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['multi', 'context', 'noise'], default='multi')
    parser.add_argument('--save', action='store_true', help='Write synthetic trial records and analysis; call Gemini on cache miss')
    args = parser.parse_args()
    if not args.save:
        print(example(args.case).model_dump_json(indent=2))
        print('Dry run: add --save to write isolated trial collections and call Gemini.')
        return 0
    settings = get_settings()
    if not settings.gemini_api_key or not settings.gemini_model:
        print('Capture failed: not_configured')
        return 1
    connection = DatabaseConnection(settings)
    try:
        connection.open()
        if connection.database is None:
            raise CaptureError('database_unavailable')
        db = connection.database
        records = db['memory_trial_records']
        analyses = AnalysesRepository(db['memory_trial_analyses'])
        classifier = ClassificationService(GeminiExtractor(settings.gemini_api_key, settings.gemini_model),
                                           ContentTypesRepository(db['memory_trial_content_types']))
        service = MemoryCaptureService(records, analyses, classifier, settings.gemini_model)
        ids = seed(records, args.case)
        document, reused = service.capture(OWNER, ids[0], ids[1:])
        if service.get_eligible(OWNER, document['_id']) is None:
            raise CaptureError('sources_no_longer_eligible')
        print(json.dumps({'analysis_id': document['_id'], 'reused': reused,
                          'memory_count': len(document['memories']),
                          'memory_ids': [item['_id'] for item in document['memories']],
                          'memory_action': document['analysis']['memory_action'],
                          'processing': document['processing']}, ensure_ascii=False, indent=2))
        print('Saved only synthetic data in memory_trial_records / memory_trial_analyses / memory_trial_content_types.')
        return 0
    except (GeminiError, CaptureError) as exc:
        print(f'Capture failed: {exc.reason if isinstance(exc, GeminiError) else str(exc)}')
        return 1
    except Exception:
        print('Capture failed: storage_or_validation_failed. No raw errors are displayed.')
        return 1
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
