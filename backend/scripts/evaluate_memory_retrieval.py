"""Private, owner-only retrieval evaluation. Preview is offline; --run explicitly sends text to Google."""
import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.analyses import AnalysesRepository
from app.repositories.memories import MemoriesRepository
from app.services.embeddings import (
    DIMENSIONS, MAX_INPUT_BYTES, PROFILE, EmbeddingError, GeminiEmbedder, normalize,
)
from app.services.memory_answer import MemoryAnswerService
from app.services.memory_capture import MemoryCaptureService
from app.services.memory_versions import MemoryVersionsService

PRIVATE_DIR = Path(__file__).resolve().parents[2] / 'data/private/retrieval-review'
DEFAULT_CASES = PRIVATE_DIR / 'retrieval-cases-draft-v1.json'
MAX_CALLS = 21


class EvaluationError(ValueError):
    pass


def sha(value):
    return hashlib.sha256(value).hexdigest()


def private_path(path):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(PRIVATE_DIR.resolve()):
        raise EvaluationError('private_path_required')
    return resolved


def read_json(path):
    path = private_path(path)
    if path.stat().st_size > 5 * 1024 * 1024:
        raise EvaluationError('file_limit')
    raw = path.read_bytes()
    return json.loads(raw), sha(raw)


def load_plan(path):
    dataset, dataset_hash = read_json(path)
    if dataset.get('dataset_version') != 'private-retrieval-review-v1':
        raise EvaluationError('invalid_dataset')
    source_name = dataset['source_export']
    if Path(source_name).name != source_name:
        raise EvaluationError('invalid_export_path')
    snapshot, snapshot_hash = read_json(PRIVATE_DIR / source_name)
    if snapshot_hash != dataset['source_sha256'] or snapshot.get('scope') != 'configured_admin_only':
        raise EvaluationError('snapshot_mismatch')
    if snapshot.get('export_version') != 'memory-retrieval-review-v1':
        raise EvaluationError('snapshot_mismatch')
    memories, cases = snapshot['memories'], dataset['cases']
    if not 1 <= len(memories) <= 11 or not 1 <= len(cases) <= 10 or len(memories) + len(cases) > MAX_CALLS:
        raise EvaluationError('call_budget')
    ids = {m['memory_id'] for m in memories}
    case_ids = {c['id'] for c in cases}
    if len(ids) != len(memories) or len(case_ids) != len(cases):
        raise EvaluationError('duplicate_id')
    labels = {m['memory_id']: m for m in dataset['memory_map']}
    if set(labels) != ids:
        raise EvaluationError('snapshot_mismatch')
    jobs = []
    for memory in memories:
        mid = memory['memory_id']
        if any(labels[mid][key] != memory[key] for key in ('revision', 'fingerprint')):
            raise EvaluationError('snapshot_mismatch')
        jobs.append({'key': 'document:' + mid, 'kind': 'document',
                     'text': memory['latest']['content']['text']})
    for case in cases:
        groups = [case[k] for k in ('required_memory_ids', 'optional_memory_ids', 'irrelevant_memory_ids')]
        sets = [set(group) for group in groups]
        if any(len(a) != len(b) for a, b in zip(groups, sets)) or set.union(*sets) != ids or any(
                sets[i] & sets[j] for i in range(3) for j in range(i+1, 3)):
            raise EvaluationError('invalid_labels')
        jobs.append({'key': 'query:' + case['id'], 'kind': 'query', 'text': case['question']})
    for job in jobs:
        if not isinstance(job['text'], str) or not job['text'].strip():
            raise EvaluationError('invalid_text')
        if len(job['text'].encode('utf-8')) > MAX_INPUT_BYTES:
            raise EvaluationError('input_limit')
        job['text_hash'] = sha(job['text'].encode('utf-8'))
    return {'dataset': dataset, 'dataset_hash': dataset_hash, 'snapshot_hash': snapshot_hash,
            'memories': memories, 'cases': cases, 'jobs': jobs}


def validate_current(reader, uid, expected):
    if not uid:
        raise EvaluationError('owner_required')
    ids = [m['memory_id'] for m in expected]
    rows = list(reader.memories.collection.find({
        'user_uid': uid, 'lifecycle': 'candidate', '_id': {'$in': ids}}))
    valid = reader.get_eligible_many(uid, rows)
    current = {row['_id']: row for row in valid if row.get('user_uid') == uid}
    if set(current) != set(ids) or len(valid) != len(current):
        raise EvaluationError('sources_changed')
    for memory in expected:
        row = current[memory['memory_id']]
        if (row['revision'] != memory['revision'] or
                MemoryAnswerService.dependency(row)['fingerprint'] != memory['fingerprint'] or
                row['versions'][-1]['content']['text'] != memory['latest']['content']['text']):
            raise EvaluationError('sources_changed')


def rank_case(case, documents, query_vector, vectors):
    ranked = []
    for memory in documents:
        mid = memory['memory_id']
        score = math.fsum(a*b for a, b in zip(query_vector, vectors['document:' + mid]['vector']))
        ranked.append({'memory_id': mid, 'revision': memory['revision'],
                       'text': memory['latest']['content']['text'], 'score': max(-1, min(1, score))})
    ranked.sort(key=lambda item: (-item['score'], item['memory_id']))
    required = set(case['required_memory_ids'])
    relevant = required | set(case['optional_memory_ids'])
    metrics = {}
    for k in (1, 3, 5):
        top = {r['memory_id'] for r in ranked[:k]}
        metrics[str(k)] = {'required_recall': len(top & required)/len(required) if required else None,
                           'relevant_precision': len(top & relevant)/len(top) if top else None}
    first = next((i for i, row in enumerate(ranked, 1) if row['memory_id'] in required), None)
    return {'id': case['id'], 'question': case['question'], 'metrics': metrics,
            'reciprocal_rank': 1/first if first else None,
            'required_memory_ids': case['required_memory_ids'],
            'optional_memory_ids': case['optional_memory_ids'],
            'answerability': case['answerability'], 'review_checks': case['review_checks'],
            'ranking': ranked, 'human_review_required': True, 'semantic_passed': None,
            'note': 'Scores are similarities, not probabilities. Raw top results do not prove relevance or facts.'}


def checkpoint(path, state):
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.parent / ('.checkpoint-' + uuid4().hex + '.json')
    raw = json.dumps(state, ensure_ascii=False, indent=2, allow_nan=False)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            file.write(raw + '\n')
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def initial_state(plan):
    return {'evaluation_version': 'private-retrieval-evaluation-v1', 'profile': PROFILE,
            'dataset_hash': plan['dataset_hash'], 'snapshot_hash': plan['snapshot_hash'],
            'created_at': datetime.now(timezone.utc).isoformat(), 'state': 'pending',
            'database_written': False, 'atlas_search_used': False,
            'call_attempts': 0, 'vectors': {}, 'results': []}


def load_resume(path, plan):
    state, _ = read_json(path)
    expected = initial_state(plan)
    if any(state.get(key) != expected[key] for key in ('evaluation_version','profile','dataset_hash','snapshot_hash')):
        raise EvaluationError('resume_mismatch')
    jobs = {job['key']: job for job in plan['jobs']}
    if not isinstance(state.get('vectors'), dict) or not set(state['vectors']) <= set(jobs):
        raise EvaluationError('resume_mismatch')
    for key, value in state['vectors'].items():
        job = jobs[key]
        if value.get('text_hash') != job['text_hash'] or value.get('kind') != job['kind']:
            raise EvaluationError('resume_mismatch')
        # Validate all saved vectors again; do not trust cached dimensions or values.
        value['vector'] = list(normalize(value['vector']))
    if type(state.get('call_attempts')) is not int or state['call_attempts'] < len(state['vectors']):
        raise EvaluationError('resume_mismatch')
    return state


def evaluate(plan, client, verify, path, state):
    # A resume may reuse local vectors, but never skips live source/owner checks.
    verify()
    state.update(state='running', results=[], error=None)
    checkpoint(path, state)
    completed_at_start = len(state['vectors'])
    for job in plan['jobs']:
        if job['key'] in state['vectors']:
            continue
        verify()
        state['call_attempts'] += 1
        checkpoint(path, state)  # Record possible spend even if the process is interrupted.
        result = getattr(client, job['kind'])(job['text'])
        if (result.profile != PROFILE or result.text_hash != job['text_hash'] or
                result.task != ('RETRIEVAL_DOCUMENT' if job['kind'] == 'document' else 'RETRIEVAL_QUERY')):
            raise EvaluationError('embedding_mismatch')
        vector = list(normalize(list(result.vector)))
        verify()
        state['vectors'][job['key']] = {'kind': job['kind'], 'text_hash': job['text_hash'], 'vector': vector}
        checkpoint(path, state)
    verify()
    state['results'] = [rank_case(case, plan['memories'], state['vectors']['query:' + case['id']]['vector'], state['vectors'])
                        for case in plan['cases']]
    positives = [case for case in state['results'] if case['required_memory_ids']]
    state['summary'] = {'memory_count': len(plan['memories']), 'case_count': len(plan['cases']),
        'new_vectors': len(state['vectors']) - completed_at_start,
        'macro_required_recall': {str(k): math.fsum(c['metrics'][str(k)]['required_recall'] for c in positives)/len(positives)
                                  if positives else None for k in (1,3,5)},
        'no_positive_case_ids': [c['id'] for c in state['results'] if not c['required_memory_ids']],
        'label_status': 'draft', 'human_review_required': True,
        'note': 'Local cosine baseline only; not Atlas, not an answer quality or authorization test.'}
    state.update(state='succeeded', evaluated_at=datetime.now(timezone.utc).isoformat())
    checkpoint(path, state)
    return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--resume', type=Path)
    args = parser.parse_args(argv)
    connection, state, path = None, None, None
    try:
        plan = load_plan(args.cases)
        state = load_resume(args.resume, plan) if args.resume else initial_state(plan)
        remaining = len(plan['jobs']) - len(state['vectors'])
        if not args.run:
            print(json.dumps({'mode':'preview', 'provider_calls':0, 'database_accessed':False,
                'memory_count':len(plan['memories']), 'case_count':len(plan['cases']),
                'planned_calls':remaining, 'max_calls_per_run':MAX_CALLS, 'profile':PROFILE,
                'private_text_sent_only_with_run':True, 'database_written':False}))
            return 0
        settings = get_settings()
        if not settings.admin_uid or not settings.mongodb_uri or not settings.gemini_api_key:
            raise EvaluationError('not_configured')
        connection = DatabaseConnection(settings)
        connection.open()
        if connection.database is None:
            raise EvaluationError('database_unavailable')
        db = connection.database
        reader = MemoryVersionsService(MemoryCaptureService(db['records'], AnalysesRepository(db['analyses']), None,''),
                                       MemoriesRepository(db['memories']))
        verify = lambda: validate_current(reader, settings.admin_uid, plan['memories'])
        verify()  # Do not create a run or send any text if the snapshot no longer matches.
        path = private_path(args.resume) if args.resume else PRIVATE_DIR / ('evaluation-' + uuid4().hex + '.json')
        state = evaluate(plan, GeminiEmbedder(settings.gemini_api_key), verify, path, state)
        print(json.dumps({'state':state['state'], 'report':str(path), 'summary':state['summary'],
                          'call_attempts':state['call_attempts'], 'database_written':False}, ensure_ascii=False))
        return 0
    except Exception as exc:
        allowed = {'private_path_required','file_limit','invalid_dataset','invalid_export_path','snapshot_mismatch',
                   'call_budget','duplicate_id','invalid_labels','invalid_text','input_limit','owner_required',
                   'sources_changed','resume_mismatch','embedding_mismatch','not_configured','database_unavailable'}
        reason = (exc.reason if isinstance(exc, EmbeddingError) else str(exc)
                  if isinstance(exc, EvaluationError) and str(exc) in allowed else 'evaluation_failed')
        if path is not None and state is not None:
            state.update(state='failed', error=reason, results=[])
            try:
                checkpoint(path, state)
            except Exception:
                path = None
        print(json.dumps({'state':'failed','reason':reason,'report':str(path) if path else None,
                          'database_written':False,'automatic_retry':False}))
        return 1
    finally:
        if connection is not None:
            connection.close()


if __name__ == '__main__':
    raise SystemExit(main())
