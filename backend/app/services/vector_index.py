"""Validate the existing Atlas index without creating or updating anything."""
import json
from pathlib import Path

from app.services.memory_search import INDEX_NAME, MemorySearchError

INDEX_FILE = Path(__file__).resolve().parents[2] / 'config' / 'memory_vector_index.json'


def check_index(collection):
    indexes = list(collection.list_search_indexes(name=INDEX_NAME))
    if len(indexes) != 1 or indexes[0].get('queryable') is not True:
        raise MemorySearchError('index_not_ready')
    index = indexes[0]
    expected = json.loads(INDEX_FILE.read_text())
    actual = index.get('latestDefinition', {})
    canonical = lambda fields: sorted(json.dumps(v, sort_keys=True) for v in fields)
    if (index.get('type') != 'vectorSearch' or index.get('status') != 'READY'
            or canonical(actual.get('fields', [])) != canonical(expected['fields'])):
        raise MemorySearchError('index_definition_mismatch')
