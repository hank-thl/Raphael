"""Version explicitly linked candidates; does not infer that two events are the same."""
from datetime import datetime, timezone

from app.repositories.memories import VersionConflict
from app.services.memory_capture import CaptureError, digest


class MemoryVersionsService:
    def __init__(self, capture, memories):
        self.capture = capture
        self.memories = memories

    def candidate(self, uid, analysis_id, candidate_id):
        analysis = self.capture.get_eligible(uid, analysis_id)
        if analysis is not None:
            for candidate in analysis['memories']:
                if candidate['_id'] == candidate_id and candidate['user_uid'] == uid:
                    return analysis, candidate
        raise CaptureError('candidate_unavailable')

    def version(self, analysis, candidate, revision):
        return {'revision': revision, 'analysis_id': analysis['_id'], 'candidate_id': candidate['_id'],
                'content': {**{key: candidate[key] for key in ('text', 'kind', 'status', 'evidence', 'time_text')},
                            'topics': candidate.get('topics', [])},
                'processing': analysis['processing'], 'created_at': datetime.now(timezone.utc)}

    def create(self, uid, analysis_id, candidate_id):
        analysis, candidate = self.candidate(uid, analysis_id, candidate_id)
        version = self.version(analysis, candidate, 1)
        identifier = digest(['logical-memory-v1', uid, analysis_id, candidate_id])
        return self.memories.create_once({'_id': identifier, 'user_uid': uid, 'revision': 1,
            'lifecycle': 'candidate', 'versions': [version], 'created_at': version['created_at'],
            'updated_at': version['created_at']})

    def supplement(self, uid, memory_id, expected_revision, analysis_id, candidate_id):
        memory = self.memories.get(uid, memory_id)
        if memory is None:
            raise CaptureError('memory_unavailable')
        analysis, candidate = self.candidate(uid, analysis_id, candidate_id)
        # Idempotent retry after a successful append; never append the same candidate twice.
        for version in memory['versions']:
            if version['analysis_id'] == analysis_id and version['candidate_id'] == candidate_id:
                return memory, True
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision != memory['revision']:
            raise CaptureError('revision_conflict')
        if memory['revision'] >= 100:
            raise CaptureError('revision_limit')
        previous = memory['versions'][-1]
        self.candidate(uid, previous['analysis_id'], previous['candidate_id'])
        # Supplements must retain all earlier evidence sources and add a new one.
        # This validates provenance, NOT semantic same-event identity.
        old_ids = {source['record_id'] for source in previous['content']['evidence']['sources']}
        new_ids = {source['record_id'] for source in candidate['evidence']['sources']}
        if not old_ids < new_ids:
            raise CaptureError('supplement_sources_required')
        version = self.version(analysis, candidate, expected_revision + 1)
        from app.services.topics import normalize_topics
        combined = dict.fromkeys([*previous['content'].get('topics', []), *version['content']['topics']])
        version['content']['topics'] = normalize_topics(list(combined))
        if len(version['content']['topics']) > 8:
            raise CaptureError('topic_limit')
        try:
            return self.memories.append(uid, memory_id, expected_revision, version), False
        except VersionConflict:
            raise CaptureError('revision_conflict') from None

    def get_eligible(self, uid, memory_id):
        memory = self.memories.get(uid, memory_id)
        if memory is None:
            return None
        # Validate every revision before exposing history, including raw old evidence.
        try:
            for version in memory['versions']:
                self.candidate(uid, version['analysis_id'], version['candidate_id'])
        except CaptureError:
            return None
        return memory
