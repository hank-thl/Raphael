"""Read the configured owner's stored answer inputs, without writes or Gemini calls."""
import argparse
import json
from uuid import UUID

from bson import ObjectId

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.analyses import AnalysesRepository
from app.repositories.conversations import identifier
from app.repositories.memories import MemoriesRepository
from app.schemas.memory_answer import MemoryAnswer
from app.services.memory_answer import MemoryAnswerService
from app.services.memory_capture import MemoryCaptureService
from app.services.memory_versions import MemoryVersionsService


def inspect(db, uid, *, question, request_id=None):
    query = {'user_uid': uid, 'state': 'succeeded'}
    if not uid:
        raise ValueError('owner_required')
    if request_id:
        query['_id'] = identifier(uid, request_id)
    else:
        query['question'] = question
    turn = db['conversations'].find_one(query, sort=[('created_at', -1), ('_id', -1)])
    if turn is None:
        return {'error': 'answer_not_found'}
    if turn.get('user_uid') != uid:
        raise ValueError('owner_mismatch')
    dependencies = turn['inputs']
    if len(dependencies) > 50:
        raise ValueError('input_limit')
    reader = MemoryVersionsService(
        MemoryCaptureService(db['records'], AnalysesRepository(db['analyses']), None, ''),
        MemoriesRepository(db['memories']))
    rows = list(db['memories'].find({'user_uid': uid, 'lifecycle': 'candidate',
                                    '_id': {'$in': [row['memory_id'] for row in dependencies]}}))
    current = {row['_id']: row for row in reader.get_eligible_many(uid, rows)}
    valid = {}
    for item in dependencies:
        memory = current.get(item['memory_id'])
        if memory is not None and MemoryAnswerService.dependency(memory)['fingerprint'] == item['fingerprint']:
            valid[item['memory_id']] = memory
    source_ids = {source['record_id'] for memory in valid.values() for version in memory['versions']
                  for source in version['content']['evidence']['sources']}
    records = {str(row['_id']): row for row in db['records'].find({
        'user_uid': uid, 'deleted_at': None, '_id': {'$in': [ObjectId(key) for key in source_ids]}})} if source_ids else {}
    # Do not expose a source that disappeared between eligibility and this read.
    valid = {key: memory for key, memory in valid.items() if all(
        source['record_id'] in records and source['quote'] in records[source['record_id']]['content']
        for version in memory['versions'] for source in version['content']['evidence']['sources'])}
    answer = None
    if len(valid) == len(dependencies):
        parsed = MemoryAnswer.model_validate(turn['answer'])
        parsed.validate_sources(list(valid.values()))
        answer = parsed.model_dump()
    cited = {(ref['memory_id'], ref['revision']) for passage in (answer or {}).get('passages', [])
             for ref in passage['sources']}

    def version_data(version):
        content = version['content']
        return {'revision': version['revision'], **content, 'evidence': {**content['evidence'], 'sources': [
            {**source, 'record_created_at': records[source['record_id']]['created_at'].isoformat()}
            for source in content['evidence']['sources']]}}

    items = []
    for dependency in dependencies:
        key = dependency['memory_id']
        memory = valid.get(key)
        if memory is None:
            items.append({'memory_id': key, 'state': 'unavailable_or_changed'})
        else:
            items.append({'memory_id': key, 'state': 'unchanged', 'revision': memory['revision'],
                          'cited_revisions': sorted(revision for mid, revision in cited if mid == key),
                          'latest': version_data(memory['versions'][-1]),
                          'history': [version_data(version) for version in memory['versions'][:-1]]})
    dates = {source['record_id']: records[source['record_id']]['created_at'] for memory in valid.values()
             for version in memory['versions'] for source in version['content']['evidence']['sources']}
    return {'question': turn['question'], 'request_id': turn['request_id'],
            'answered_at': turn['updated_at'].isoformat(), 'processing': {
                key: turn.get('processing', {}).get(key) for key in ('model', 'version', 'prompt_version')},
            'all_memory_inputs_unchanged': len(valid) == len(dependencies),
            'input_memory_count': len(dependencies), 'available_memory_count': len(valid),
            'distinct_source_record_count': len(dates),
            'recorded_from': min(dates.values()).isoformat() if dates else None,
            'recorded_to': max(dates.values()).isoformat() if dates else None,
            'answer': answer, 'memory_inputs': items}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--question', default='我的心理狀態怎麼樣？', help='Find the newest successful answer to this exact question')
    parser.add_argument('--request-id', type=UUID, help='Inspect a specific request instead of matching question text')
    args = parser.parse_args(argv)
    settings = get_settings()
    if not settings.admin_uid or not settings.mongodb_uri or not settings.mongodb_database:
        print(json.dumps({'error': 'owner_or_database_not_configured'}))
        return 1
    connection = DatabaseConnection(settings)
    try:
        connection.open()
        if connection.database is None:
            raise ValueError('database_unavailable')
        result = inspect(connection.database, settings.admin_uid, question=args.question,
                         request_id=str(args.request_id) if args.request_id else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if 'error' in result else 0
    except Exception:
        # Driver/validation errors may contain credentials or deleted source text.
        print(json.dumps({'error': 'storage_or_validation_failed'}))
        return 1
    finally:
        connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
