from copy import deepcopy
from unittest.mock import Mock
import json

import httpx
import pytest
from pydantic import ValidationError

from app.schemas.matching import MatchDecision
from app.schemas.extraction import MemoryCandidate
from app.services.memory_capture import CaptureError
from app.services.memory_versions import MemoryVersionsService
from app.services.memory_routing import MemoryRoutingService
from app.services.memory_matching import GeminiMemoryMatcher
from app.services.gemini import GeminiError, GeminiExtractor
from test_memory_capture import setup_case, Analyses
from test_memory_versions import Memories


def setup_router():
    records, analyses, classifier, target, capture = setup_case()
    baseline, _ = capture.capture('alice', target)
    repo = Memories()
    repo.recent = lambda uid: [row for row in repo.rows.values() if row['user_uid'] == uid]
    versions = MemoryVersionsService(capture, repo)
    memory = versions.create('alice', baseline['_id'], baseline['memories'][0]['_id'])
    new_id = records.add(content='補充費用180元')
    result = classifier.analyze.return_value[0].model_copy(deep=True)
    result.items[0].text = '今天上課很開心，費用180元'
    result.items[0].evidence.type = 'contextual'
    from app.schemas.extraction import EvidenceSource
    result.items[0].evidence.sources.append(EvidenceSource(record_id=new_id, quote='補充費用180元'))
    result.context_record_ids = [target]
    result.context_required = True
    classifier.analyze.return_value = (result, 'category')
    incoming, _ = capture.capture('alice', new_id, [target])
    matcher = Mock()
    matcher.decide.return_value = MatchDecision(action='supplement', memory_id=memory['_id'], merged=result.items[0])
    plans = Analyses()
    router = MemoryRoutingService(capture, versions, matcher, plans)
    return records, repo, matcher, router, incoming, memory, plans


def test_supplement_preserves_identity_and_retry_reuses_decision():
    records, repo, matcher, router, incoming, memory, plans = setup_router()
    candidate = incoming['memories'][0]
    outcome = router.route('alice', incoming['_id'], candidate['_id'])
    assert outcome['action'] == 'supplement' and outcome['revision'] == 2
    assert outcome['memory_id'] == memory['_id']
    again = router.route('alice', incoming['_id'], candidate['_id'])
    assert again['reused'] and again['revision'] == 2
    matcher.decide.assert_called_once()
    assert repo.rows[memory['_id']]['versions'][0] == memory['versions'][0]
    assert len(plans.rows) == 1


@pytest.mark.parametrize('action', ['new', 'uncertain'])
def test_new_and_uncertain_save_separate_candidate_without_merging(action):
    records, repo, matcher, router, incoming, memory, plans = setup_router()
    matcher.decide.return_value = MatchDecision(action=action, memory_id=None, merged=None)
    outcome = router.route('alice', incoming['_id'], incoming['memories'][0]['_id'])
    assert outcome['action'] == action and outcome['memory_id'] != memory['_id']
    assert repo.rows[memory['_id']]['revision'] == 1
    assert len(repo.rows) == 2


def test_invalid_target_and_invented_quote_do_not_save_plan_or_update_memory():
    records, repo, matcher, router, incoming, memory, plans = setup_router()
    decision = matcher.decide.return_value
    decision.memory_id = 'other-owner-memory'
    with pytest.raises(CaptureError, match='invalid_matching_target'):
        router.route('alice', incoming['_id'], incoming['memories'][0]['_id'])
    decision.memory_id = memory['_id']
    decision.merged.evidence.sources[0].quote = 'invented'
    with pytest.raises(ValueError):
        router.route('alice', incoming['_id'], incoming['memories'][0]['_id'])
    assert not plans.rows and repo.rows[memory['_id']]['revision'] == 1


def test_owner_check_precedes_model_and_deleted_sources_block_replay():
    from bson import ObjectId
    from datetime import datetime, timezone
    records, repo, matcher, router, incoming, memory, plans = setup_router()
    with pytest.raises(CaptureError): router.route('bob', incoming['_id'], incoming['memories'][0]['_id'])
    matcher.decide.assert_not_called()
    router.route('alice', incoming['_id'], incoming['memories'][0]['_id'])
    records.rows[ObjectId(incoming['target_record_id'])]['deleted_at'] = datetime.now(timezone.utc)
    with pytest.raises(CaptureError): router.route('alice', incoming['_id'], incoming['memories'][0]['_id'])
    assert matcher.decide.call_count == 1


def test_recent_selection_skips_ineligible_and_stops_after_five():
    versions = Mock()
    versions.memories.recent.return_value = [{'_id': str(i)} for i in range(9)]
    def eligible(uid, identifier):
        assert uid == 'alice'
        return None if identifier == '0' else {'_id': identifier, 'versions': [{'candidate_id': identifier}]}
    versions.get_eligible.side_effect = eligible
    router = MemoryRoutingService(Mock(), versions, Mock(), Mock())
    assert [row['_id'] for row in router.recent('alice', 'incoming')] == ['1', '2', '3', '4', '5']
    versions.memories.recent.assert_called_once_with('alice')


@pytest.mark.parametrize('payload', [
    {'action': 'supplement', 'memory_id': None, 'merged': None},
    {'action': 'uncertain', 'memory_id': 'x', 'merged': None},
    {'action': 'new', 'memory_id': None, 'merged': None, 'user_uid': 'evil'},
    {'action': 'update', 'memory_id': None, 'merged': None},
])
def test_decision_contract_rejects_unsafe_combinations(payload):
    with pytest.raises(ValidationError): MatchDecision.model_validate(payload)


def test_matching_wire_uses_same_safe_transport_and_separate_schema():
    def handler(request):
        payload = json.loads(request.content)
        assert request.headers['x-goog-api-key'] == 'fake-key'
        assert 'untrusted DATA' in payload['systemInstruction']['parts'][0]['text']
        assert payload['generationConfig']['responseJsonSchema']['properties']['action']['enum'] == ['new', 'supplement', 'uncertain']
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'text': json.dumps({'action': 'uncertain', 'memory_id': None, 'merged': None})}]}}]})
    matcher = GeminiMemoryMatcher(GeminiExtractor('fake-key', 'gemini-test', transport=httpx.MockTransport(handler)))
    assert matcher.decide({}, [], [], 'target').action == 'uncertain'


def test_matching_input_limit_prevents_network_call():
    extractor = Mock()
    matcher = GeminiMemoryMatcher(extractor)
    with pytest.raises(GeminiError, match='matching_input_too_large'):
        matcher.decide({'text': 'x' * 100001}, [], [], 'target')
    extractor.generate.assert_not_called()


def test_matching_cli_dry_run(monkeypatch, capsys):
    from scripts import try_memory_capture
    monkeypatch.setattr('sys.argv', ['try_memory_capture', '--case', 'context', '--auto-match'])
    monkeypatch.setattr(try_memory_capture, 'DatabaseConnection', lambda *a: pytest.fail('No DB'))
    assert try_memory_capture.main() == 0
    assert 'Dry run' in capsys.readouterr().out


def test_no_existing_memories_creates_without_matching_call():
    records, repo, matcher, router, incoming, memory, plans = setup_router()
    repo.rows.clear()
    outcome = router.route('alice', incoming['_id'], incoming['memories'][0]['_id'])
    assert outcome['action'] == 'new' and outcome['revision'] == 1
    matcher.decide.assert_not_called()


def test_context_limit_stops_without_truncation_or_model_call():
    records, repo, matcher, router, incoming, memory, plans = setup_router()
    old = deepcopy(memory)
    old['versions'][-1]['content']['evidence']['sources'] = [
        {'record_id': str(i), 'quote': 'x'} for i in range(6)]
    router.recent = Mock(return_value=[old])
    with pytest.raises(CaptureError, match='matching_context_limit'):
        router.route('alice', incoming['_id'], incoming['memories'][0]['_id'])
    matcher.decide.assert_not_called()
    assert not plans.rows


def test_model_failure_does_not_create_plan_or_change_existing_memory():
    records, repo, matcher, router, incoming, memory, plans = setup_router()
    matcher.decide.side_effect = GeminiError('provider_unavailable')
    with pytest.raises(GeminiError):
        router.route('alice', incoming['_id'], incoming['memories'][0]['_id'])
    assert not plans.rows and repo.rows[memory['_id']]['revision'] == 1


def test_stored_candidate_datetime_and_uid_never_enter_matching_payload():
    from datetime import datetime, timezone
    captured = {}
    def handler(request):
        payload = json.loads(request.content)
        captured.update(json.loads(payload['contents'][0]['parts'][0]['text']))
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'text': json.dumps({'action': 'uncertain', 'memory_id': None, 'merged': None})}]}}]})
    candidate = {'_id': 'system-id', 'user_uid': 'private-owner', 'created_at': datetime.now(timezone.utc),
                 'lifecycle': 'candidate', 'text': '今天上課很開心', 'kind': ['event', 'emotion'],
                 'status': 'occurred', 'time_text': '今天',
                 'evidence': {'type': 'explicit', 'sources': [{'record_id': 'source', 'quote': '很開心'}]}}
    matcher = GeminiMemoryMatcher(GeminiExtractor('fake', 'gemini-test', transport=httpx.MockTransport(handler)))
    assert matcher.decide(candidate, [], [], 'source').action == 'uncertain'
    assert set(captured['candidate']) == set(MemoryCandidate.model_fields)
    assert captured['candidate']['text'] == candidate['text']
    assert not {'_id', 'user_uid', 'created_at', 'lifecycle'} & captured['candidate'].keys()
