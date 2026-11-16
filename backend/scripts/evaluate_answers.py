"""Synthetic A/B answer review: no MongoDB, no saved dialogue, no automatic retries."""
import argparse
import hashlib
import json
from pathlib import Path
from time import monotonic
from typing import Literal

from pydantic import Field

from app.core.config import get_settings
from app.schemas.memory_answer import AnswerPassage, MemoryAnswer
from app.services.gemini import GeminiError, GeminiExtractor, generation_schema
from app.services.memory_answer import ANSWER_PROMPT_VERSION, ANSWER_VERSION, INSTRUCTION

DATA_DIR = Path(__file__).resolve().parents[2] / 'data' / 'evaluations' / 'answer_strategy'
USAGE_FIELDS = ('promptTokenCount', 'candidatesTokenCount', 'thoughtsTokenCount', 'totalTokenCount')


class LegacyPassage(AnswerPassage):
    basis: Literal['explicit', 'contextual', 'inferred', 'limitation']


class LegacyAnswer(MemoryAnswer):
    passages: list[LegacyPassage] = Field(min_length=1, max_length=10)


def load_cases():
    return json.loads((DATA_DIR / 'cases.json').read_text())


def baseline():
    meta = json.loads((DATA_DIR / 'baseline.json').read_text())
    instruction = (DATA_DIR / 'baseline_prompt.txt').read_text()
    if hashlib.sha256(instruction.encode()).hexdigest() != meta['prompt_sha256']:
        raise ValueError('baseline_changed')
    return instruction, json.loads((DATA_DIR / 'baseline_schema.json').read_text()), meta


def prepare(case):
    memories, wire = [], []
    for spec in case['memories']:
        mid = hashlib.sha256(f"evaluation:{case['id']}:{spec['key']}".encode()).hexdigest()
        versions = []
        for revision, version in enumerate(spec['versions'], 1):
            sources = [{'record_id': hashlib.sha256(f"evaluation:{case['id']}:{s['key']}".encode()).hexdigest()[:24],
                        'quote': s['quote'], 'record_created_at': s['recorded_at']} for s in version['sources']]
            content = {key: version[key] for key in ('text', 'kind', 'status', 'topics', 'time_text')}
            content['evidence'] = {'type': version['evidence_type'], 'sources': sources}
            versions.append({'revision': revision, 'content': content})
        memory = {'_id': mid, 'revision': len(versions), 'versions': versions}
        memories.append(memory)
        def render(v): return {'revision': v['revision'], **v['content']}
        wire.append({'memory_id': mid, 'revision': len(versions), 'latest': render(versions[-1]),
                     'history': [render(v) for v in versions[:-1]]})
    payload = {'question': case['question'], 'current_time': '2026-11-16T04:00:00+00:00',
               'timezone': 'Asia/Taipei', 'previous_conversation': [], 'memories': wire}
    return payload, memories


def compare(case, extractor, *, candidate_only=False):
    payload, memories = prepare(case)
    text = json.dumps(payload, ensure_ascii=False)
    old_instruction, old_schema, old_meta = baseline()
    variants = [('baseline', old_instruction, old_schema, old_meta, LegacyAnswer),
                ('candidate', INSTRUCTION, generation_schema(MemoryAnswer),
                 {'prompt_version': ANSWER_PROMPT_VERSION, 'contract_version': ANSWER_VERSION}, MemoryAnswer)]
    if candidate_only:
        variants = variants[-1:]
    report = {'case': case['id'], 'strategy': case['strategy'], 'language': case['language'],
              'memory_count': len(memories), 'review_checks': case['review_checks'],
              'human_review_required': True, 'semantic_passed': None,
              'comparison': not candidate_only}
    for name, instruction, schema, meta, contract in variants:
        usage = {}
        started = monotonic()
        row = {**{key: meta[key] for key in ('prompt_version', 'contract_version')},
               'system_characters': len(instruction), 'input_characters': len(text)}
        try:
            decoded = extractor.generate(text, schema, instruction, usage_callback=usage.update)
            result = contract.model_validate(decoded)
            result.validate_sources(memories)
            row.update({'contract_valid': True, 'answer': result.model_dump()})
        except GeminiError as exc:
            row.update({'contract_valid': False, 'error': exc.reason, 'provider_failed': True})
        except (ValueError, TypeError):
            row.update({'contract_valid': False, 'error': 'contract_validation_failed'})
        row.update({'elapsed_ms': round((monotonic() - started) * 1000), 'usage': usage or None})
        report[name] = row
        if row.get('provider_failed'):
            report['stopped'] = True
            return report
    old, new = report.get('baseline', {}).get('usage') or {}, report['candidate']['usage'] or {}
    report['token_delta'] = {key: new[key] - old[key] if key in old and key in new else None for key in USAGE_FIELDS}
    return report


def main(argv=None):
    cases = load_cases()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['all', *[row['id'] for row in cases]], default='fact')
    parser.add_argument('--run', action='store_true', help='Call Gemini twice per case, or once with --candidate-only; no database writes')
    parser.add_argument('--candidate-only', action='store_true', help='Run only the current version once per case, reusing your earlier baseline results')
    args = parser.parse_args(argv)
    selected = cases if args.case == 'all' else [row for row in cases if row['id'] == args.case]
    if not args.run:
        print(json.dumps({'mode': 'preview', 'provider_calls': 0, 'planned_calls': len(selected) * (1 if args.candidate_only else 2),
                          'cases': [{'id': row['id'], 'input': prepare(row)[0], 'review_checks': row['review_checks']}
                                    for row in selected]}, ensure_ascii=False, indent=2))
        return 0
    settings = get_settings()
    if not settings.gemini_api_key or not settings.gemini_model:
        print(json.dumps({'error': 'gemini_not_configured'}))
        return 1
    extractor = GeminiExtractor(settings.gemini_api_key, settings.gemini_model)
    try:
        for case in selected:
            result = compare(case, extractor, candidate_only=args.candidate_only)
            result['model'] = settings.gemini_model
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            if result.get('stopped'):
                return 1
    except Exception:
        print(json.dumps({'error': 'evaluation_failed'}))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
