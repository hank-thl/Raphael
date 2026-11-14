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


def describe_memory(versions, uid, identifier):
    """Use eligibility checks before displaying any historical evidence."""
    memory = versions.get_eligible(uid, identifier)
    if memory is None:
        raise CaptureError('memory_no_longer_eligible')
    return {'memory_id': memory['_id'], 'revision': memory['revision'], 'versions': [
        {'revision': row['revision'], **row['content']} for row in memory['versions']]}


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
    parser.add_argument('--with-versions', action='store_true', help='For context only: explicitly link baseline and supplement as two revisions')
    parser.add_argument('--auto-match', action='store_true', help='For context only: let Gemini choose new/supplement/uncertain in separate trial collections')
    args = parser.parse_args()
    if args.with_versions and args.case != 'context':
        parser.error('--with-versions requires --case context')
    if args.auto_match and (args.case != 'context' or args.with_versions):
        parser.error('--auto-match requires --case context and cannot combine with --with-versions')
    if not args.save:
        print(example(args.case).model_dump_json(indent=2))
        print('Dry run: add --save to write isolated trial collections and call Gemini.')
        return 0
    settings = get_settings()
    if not settings.gemini_api_key or not settings.gemini_model:
        print('Capture failed: not_configured')
        return 1
    connection = DatabaseConnection(settings)
    stage = 'database_initialization'
    try:
        connection.open()
        if connection.database is None:
            raise CaptureError('database_unavailable')
        db = connection.database
        prefix = 'memory_auto_trial' if args.auto_match else 'memory_trial'
        records = db[f'{prefix}_records']
        analyses = AnalysesRepository(db[f'{prefix}_analyses'])
        classifier = ClassificationService(GeminiExtractor(settings.gemini_api_key, settings.gemini_model),
                                           ContentTypesRepository(db[f'{prefix}_content_types']))
        service = MemoryCaptureService(records, analyses, classifier, settings.gemini_model)
        stage = 'seed_synthetic_records'
        ids = seed(records, args.case)
        baseline = None
        if args.with_versions or args.auto_match:
            stage = 'baseline_analysis'
            baseline, _ = service.capture(OWNER, ids[1])
        stage = 'incoming_analysis'
        document, reused = service.capture(OWNER, ids[0], ids[1:])
        if service.get_eligible(OWNER, document['_id']) is None:
            raise CaptureError('sources_no_longer_eligible')
        print(json.dumps({'analysis_id': document['_id'], 'reused': reused,
                          'memory_count': len(document['memories']),
                          'memory_ids': [item['_id'] for item in document['memories']],
                          'memory_action': document['analysis']['memory_action'],
                          'processing': document['processing']}, ensure_ascii=False, indent=2))
        if args.auto_match:
            from app.repositories.memories import MemoriesRepository
            from app.services.memory_versions import MemoryVersionsService
            from app.services.memory_matching import GeminiMemoryMatcher
            from app.services.memory_routing import MemoryRoutingService
            versions = MemoryVersionsService(service, MemoriesRepository(db[f'{prefix}_memories']))
            router = MemoryRoutingService(service, versions,
                GeminiMemoryMatcher(GeminiExtractor(settings.gemini_api_key, settings.gemini_model)),
                AnalysesRepository(db[f'{prefix}_routes']))
            for item in baseline['memories']:
                stage = 'route_baseline'
                router.route(OWNER, baseline['_id'], item['_id'])
            stage = 'route_incoming'
            outcomes = [router.route(OWNER, document['_id'], item['_id']) for item in document['memories']]
            print(json.dumps({'routing': outcomes, 'matching_version': 'memory-match-v1',
                              'memory_details': [describe_memory(versions, OWNER, row['memory_id'])
                                                 for row in outcomes]}, ensure_ascii=False, indent=2))
            print('Saved synthetic automatic matching data only in memory_auto_trial_* collections.')
        if args.with_versions:
            stage = 'explicit_versions'
            from app.repositories.memories import MemoriesRepository
            from app.services.memory_versions import MemoryVersionsService
            if len(baseline['memories']) != 1 or len(document['memories']) != 1:
                raise CaptureError('trial_requires_one_candidate_per_analysis')
            versions = MemoryVersionsService(service, MemoriesRepository(db['memory_trial_memories']))
            memory = versions.create(OWNER, baseline['_id'], baseline['memories'][0]['_id'])
            memory, version_reused = versions.supplement(OWNER, memory['_id'], 1,
                document['_id'], document['memories'][0]['_id'])
            if versions.get_eligible(OWNER, memory['_id']) is None:
                raise CaptureError('memory_no_longer_eligible')
            print(json.dumps({'memory_id': memory['_id'], 'revision': memory['revision'],
                              'version_reused': version_reused, 'versions': [
                                  {'revision': row['revision'], 'text': row['content']['text'],
                                   'source_record_ids': [source['record_id'] for source in row['content']['evidence']['sources']]}
                                  for row in memory['versions']]}, ensure_ascii=False, indent=2))
            print('Explicit synthetic same-event link saved in memory_trial_memories; no automatic event matching.')
        if not args.auto_match:
            print('Saved only synthetic data in memory_trial_records / memory_trial_analyses / memory_trial_content_types.')
        return 0
    except (GeminiError, CaptureError) as exc:
        print(f'Capture failed: {exc.reason if isinstance(exc, GeminiError) else str(exc)}')
        print(f'Stage: {stage}')
        return 1
    except Exception:
        print('Capture failed: storage_or_validation_failed. No raw errors are displayed.')
        print(f'Stage: {stage}')
        return 1
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
