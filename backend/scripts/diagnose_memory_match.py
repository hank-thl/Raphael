"""One synthetic matching-only call. No extraction or database access."""
import argparse
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import get_settings
from app.services.gemini import GeminiError, GeminiExtractor, PROMPT_VERSION
from app.services.memory_matching import GeminiMemoryMatcher, MATCH_VERSION
from scripts.evaluate_memory_retrieval import PRIVATE_DIR, checkpoint


def fixture():
    first, second = '吃口服a酸結果大爆痘', '醫生還有開外用a酸'
    def candidate(text, record_id, kind):
        return {'text': text, 'kind': kind, 'topics': ['健康'], 'status': 'occurred', 'time_text': None,
            'evidence': {'type': 'explicit', 'sources': [{'record_id': record_id, 'quote': text}]}}
    old = candidate(first, 'synthetic_first', ['event', 'observation'])
    incoming = candidate(second, 'synthetic_second', ['event'])
    return incoming, [{'memory_id': 'synthetic_baseline_memory', 'revision': 1, 'content': old}], [
        {'record_id': 'synthetic_second', 'content': second, 'created_at': '2026-11-19T14:48:58+00:00'},
        {'record_id': 'synthetic_first', 'content': first, 'created_at': '2026-11-19T14:40:48+00:00'}]


def diagnose(extractor):
    incoming, options, raw = fixture()
    decision = GeminiMemoryMatcher(extractor).decide(incoming, options, raw, 'synthetic_second')
    if decision.merged is not None:
        # Validate invented/missing quotes even though this diagnostic never saves Memory.
        known = {row['record_id']: row['content'] for row in raw}
        refs = decision.merged.evidence.sources
        if (decision.merged.evidence.type == 'inferred' or len(refs) != len(known)
                or {s.record_id for s in refs} != set(known)
                or any(s.quote not in known[s.record_id] for s in refs)):
            raise GeminiError('invalid_matching_response')
    return {'version': 'matching-only-diagnostic-v1', 'matching_version': MATCH_VERSION,
        'fixture_extraction_version': PROMPT_VERSION, 'decision': decision.model_dump(),
        'expected_action': 'supplement', 'expectation_met': decision.action == 'supplement',
        'note_available': decision.decision_note is not None,
        'input_kind': 'fixed_synthetic_candidates_not_fresh_extraction',
        'new_judgment_not_original_call_explanation': True, 'database_accessed': False,
        'database_written': False, 'provider_calls': 1, 'automatic_retry': False,
        'human_review_required': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    if not args.run:
        print(json.dumps({'mode': 'preview', 'planned_provider_calls_max': 1,
            'provider_calls': 0, 'database_accessed': False, 'database_written': False}))
        return 0
    try:
        settings = get_settings()
        if not settings.gemini_api_key or not settings.gemini_model:
            raise GeminiError('not_configured')
        result = diagnose(GeminiExtractor(settings.gemini_api_key, settings.gemini_model))
        result.update(model=settings.gemini_model, created_at=datetime.now(timezone.utc).isoformat())
        path = PRIVATE_DIR / ('matching-diagnostic-' + uuid4().hex + '.json')
        checkpoint(path, result)
        print(json.dumps({'state': 'succeeded', 'report': str(path), 'provider_calls': 1,
                          'database_written': False, 'expectation_met': result['expectation_met']}))
        return 0
    except GeminiError as exc:
        print(json.dumps({'state': 'failed', 'reason': exc.reason, 'automatic_retry': False, 'database_written': False}))
        return 1
    except Exception:
        print(json.dumps({'state': 'failed', 'reason': 'diagnostic_or_report_failed',
                          'automatic_retry': False, 'database_written': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
