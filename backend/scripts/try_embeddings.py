"""Preview by default. --run makes ONE embedding call with fixed synthetic text only."""
import argparse
import json
import math

from app.core.config import get_settings
from app.services.embeddings import DIMENSIONS, MODEL, PROFILE, EmbeddingError, GeminiEmbedder

SAMPLES = {'document': '週末去河濱騎腳踏車，途中停下來看夕陽。',
           'query': '週末做了什麼戶外活動？'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=tuple(SAMPLES), default='document')
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args(argv)
    report = {'model': MODEL, 'profile': PROFILE, 'dimensions': DIMENSIONS, 'kind': args.kind,
              'synthetic_only': True, 'database_written': False}
    if not args.run:
        print(json.dumps({**report, 'mode': 'preview', 'provider_calls': 0, 'planned_calls': 1}, ensure_ascii=False))
        return 0
    key = get_settings().gemini_api_key
    if not key:
        print(json.dumps({**report, 'error': 'not_configured', 'provider_calls': 0}))
        return 1
    try:
        client = GeminiEmbedder(key)
        result = getattr(client, args.kind)(SAMPLES[args.kind])
        print(json.dumps({**report, 'mode': 'run', 'state': 'succeeded',
                          'vector_dimensions': len(result.vector), 'l2_norm': math.hypot(*result.vector),
                          'text_hash': result.text_hash}, ensure_ascii=False))
        return 0
    except EmbeddingError as exc:
        print(json.dumps({**report, 'state': 'failed', 'reason': exc.reason, 'http_status': exc.status_code}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
