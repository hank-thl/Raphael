"""Derive the owner topic catalog from valid logical memories; no new collection."""
from app.repositories.content_types import normalize_name


def normalize_topics(values):
    names = [normalize_name(value) for value in values]
    if len(set(names)) != len(names):
        raise ValueError('Duplicate topic names')
    return names


class MemoryTopics:
    def __init__(self, versions):
        self.versions = versions

    def list_names(self, uid):
        if not uid:
            raise ValueError('Owner required')
        names = set()
        for row in self.versions.memories.recent(uid):
            memory = self.versions.get_eligible(uid, row['_id'])
            if memory is None:
                continue
            names.update(normalize_topics(memory['versions'][-1]['content'].get('topics', [])))
            if len(names) > 100:
                raise ValueError('Topic catalog exceeds analysis limit')
        return sorted(names)
