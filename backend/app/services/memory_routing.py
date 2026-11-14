"""Explicit routing orchestration; no public API or private-data trigger."""
from app.schemas.extraction import ExtractionResult
from app.schemas.matching import MatchDecision
from app.services.memory_capture import CaptureError, digest, snapshots
from app.services.memory_matching import MATCH_VERSION


class MemoryRoutingService:
    def __init__(self, capture, versions, matcher, plans):
        self.capture, self.versions, self.matcher, self.plans = capture, versions, matcher, plans

    def recent(self, uid, candidate_id):
        selected = []
        for row in self.versions.memories.recent(uid):
            memory = self.versions.get_eligible(uid, row['_id'])
            if memory is None:
                continue
            # Do not match an incoming candidate back to itself on retries.
            if any(v['candidate_id'] == candidate_id for v in memory['versions']):
                continue
            selected.append(memory)
            if len(selected) == 5:
                break
        return selected

    def route(self, uid, analysis_id, candidate_id):
        analysis, candidate = self.versions.candidate(uid, analysis_id, candidate_id)
        key = digest([MATCH_VERSION, uid, analysis_id, candidate_id])
        plan = self.plans.get(uid, key)
        if plan is None:
            memories = self.recent(uid, candidate_id)
            options, source_ids = [], set()
            for memory in memories:
                latest = memory['versions'][-1]
                options.append({'memory_id': memory['_id'], 'revision': memory['revision'],
                                'content': latest['content']})
                source_ids.update(source['record_id'] for source in latest['content']['evidence']['sources'])
            source_ids.update(analysis['context_record_ids'])
            source_ids.discard(analysis['target_record_id'])
            if len(source_ids) > 5:
                raise CaptureError('matching_context_limit')
            data = self.capture.load(uid, analysis['target_record_id'], sorted(source_ids), analysis['timezone'])
            decision = (self.matcher.decide(candidate, options, snapshots(data), data.target.record_id)
                        if options else MatchDecision(action='new', memory_id=None, merged=None))
            decision = MatchDecision.model_validate(decision.model_dump())
            expected_revision = None
            result = None
            if decision.action == 'supplement':
                target = next((row for row in memories if row['_id'] == decision.memory_id), None)
                if target is None:
                    raise CaptureError('invalid_matching_target')
                old_ids = {s['record_id'] for s in target['versions'][-1]['content']['evidence']['sources']}
                new_ids = {s.record_id for s in decision.merged.evidence.sources}
                incoming_ids = {s['record_id'] for s in candidate['evidence']['sources']}
                if (not old_ids < new_ids or not new_ids <= old_ids | incoming_ids
                        or decision.merged.evidence.type == 'inferred'):
                    raise CaptureError('invalid_supplement_evidence')
                # Selection options are not evidence dependencies of the saved memory.
                data = self.capture.load(uid, analysis['target_record_id'],
                    sorted(new_ids - {analysis['target_record_id']}), analysis['timezone'])
                result = ExtractionResult(content_type=analysis['analysis']['content_type'], memory_action='extract',
                    context_required=True, context_record_ids=[r.record_id for r in data.context], items=[decision.merged])
                result.validate_sources(data)
                expected_revision = target['revision']
            plan = self.plans.create_once({'_id': key, 'user_uid': uid, 'match_version': MATCH_VERSION,
                'decision': decision.model_dump(), 'expected_revision': expected_revision,
                'sources': snapshots(data), 'timezone': data.timezone,
                'result': result.model_dump() if result else None, 'content_type_id': analysis['content_type_id']})
        # Replays apply the persisted decision, not a new model judgment.
        self.versions.candidate(uid, analysis_id, candidate_id)
        decision = MatchDecision.model_validate(plan['decision'])
        if decision.action != 'supplement':
            memory = self.versions.create(uid, analysis_id, candidate_id)
            if self.versions.get_eligible(uid, memory['_id']) is None:
                raise CaptureError('memory_no_longer_eligible')
            return {'action': decision.action, 'memory_id': memory['_id'], 'revision': memory['revision']}
        source_ids = [row['record_id'] for row in plan['sources']]
        data = self.capture.load(uid, source_ids[0], source_ids[1:], plan['timezone'])
        if snapshots(data) != plan['sources']:
            raise CaptureError('sources_changed')
        merged_analysis = self.capture.persist(uid, data, ExtractionResult.model_validate(plan['result']),
            plan['content_type_id'], digest([key, 'merged']),
            {**analysis['processing'], 'matching_version': MATCH_VERSION})
        memory, reused = self.versions.supplement(uid, decision.memory_id, plan['expected_revision'],
            merged_analysis['_id'], merged_analysis['memories'][0]['_id'])
        if self.versions.get_eligible(uid, memory['_id']) is None:
            raise CaptureError('memory_no_longer_eligible')
        return {'action': 'supplement', 'memory_id': memory['_id'], 'revision': memory['revision'], 'reused': reused}
