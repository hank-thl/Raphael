import json
from unittest.mock import Mock

import pytest

from app.services.memory_matching import GeminiMemoryMatcher
from app.services.memory_capture import CaptureError
from scripts import try_memory_matching as trial
from test_matching_evaluation import Database, Model


class ContinuationFixtureModel(Model):
    """Controlled output to test persistence/provenance, not real model judgment."""
    def analyze(self, data):
        result = super().analyze(data)
        result.items[0].topics = ['健康']
        return result

    def generate(self, payload, schema, instruction):
        self.calls += 1
        data = json.loads(payload)
        return {'action': 'supplement', 'memory_id': data['options'][0]['memory_id'], 'merged': {
            'text': '記錄口服a酸後大爆痘，並補充醫生還有開外用a酸。', 'kind': ['event', 'observation'],
            'topics': ['健康'], 'status': 'occurred', 'time_text': None,
            'evidence': {'type': 'contextual', 'sources': [
                {'record_id': row['record_id'], 'quote': row['content']} for row in data['raw_records']]}}}


def test_episode_supplement_keeps_original_sources_versions_and_queues_new_revision():
    db, model = Database(), ContinuationFixtureModel('supplement')
    result = trial.evaluate(db, 'fixture-model', model, 'continuation')
    memory = result['result_memories'][0]
    assert result['expectation_met'] and memory['revision'] == 2
    assert memory['memory_id'] == result['baseline_memories'][0]['memory_id']
    assert memory['versions'][0]['text'] == '吃口服a酸結果大爆痘'
    assert {source['quote'] for source in memory['versions'][1]['evidence']['sources']} == {
        '吃口服a酸結果大爆痘', '醫生還有開外用a酸'}
    row = db[f'{trial.PREFIX}_memories'].rows[memory['memory_id']]
    assert row['embedding_processing']['revision'] == 2 and row['embedding_processing']['state'] == 'pending'
    again = trial.evaluate(db, 'fixture-model', model, 'continuation')
    assert again['routing'][0]['reused'] and model.calls == 1


@pytest.mark.parametrize('case,action', [('different_episode', 'new'),
    ('ambiguous_continuation', 'uncertain'), ('topic_only', 'new')])
def test_negative_decisions_keep_existing_memories_separate(case, action):
    result = trial.evaluate(Database(), 'fixture-model', Model(action), case)
    assert result['expectation_met']
    assert all(row['revision'] == 1 for row in result['baseline_memories'])
    assert result['result_memories'][0]['memory_id'] not in {row['memory_id'] for row in result['baseline_memories']}


def test_original_continuation_cue_reaches_matcher_even_when_candidate_was_normalized():
    extractor = Mock()
    extractor.generate.return_value = {'action': 'uncertain', 'memory_id': None, 'merged': None}
    raw = [{'record_id': 'first', 'content': '吃口服a酸結果大爆痘'},
           {'record_id': 'second', 'content': '醫生還有開外用a酸'}]
    GeminiMemoryMatcher(extractor).decide({'text': '醫生有開外用a酸'}, [], raw, 'second')
    payload = json.loads(extractor.generate.call_args.args[0])
    assert payload['raw_records'][1]['content'] == '醫生還有開外用a酸'
    assert payload['target_record_id'] == 'second'
    assert payload['candidate']['text'] == '醫生有開外用a酸'
    assert extractor.generate.call_count == 1


class ReferenceFixtureModel(Model):
    wrong_target = False

    def generate(self, payload, schema, instruction):
        data = json.loads(payload)
        selected = next(row for row in data['options']
                        if ('異音' in row['content']['text']) != self.wrong_target)
        sources = selected['content']['evidence']['sources'] + data['candidate']['evidence']['sources']
        return {'action': 'supplement', 'memory_id': selected['memory_id'], 'merged': {
            **selected['content'], 'text': selected['content']['text'] + data['candidate']['text'],
            'evidence': {'type': 'contextual', 'sources': sources}}}


def test_explicit_reference_updates_only_named_episode_with_related_distractor():
    result = trial.evaluate(Database(), 'fixture-model', ReferenceFixtureModel('supplement'),
                            'explicit_reference')
    assert result['expectation_met']
    assert result['result_memories'][0]['memory_id'] == result['baseline_memories'][0]['memory_id']
    assert result['baseline_memories'][0]['revision'] == 2
    assert result['baseline_memories'][1]['revision'] == 1


def test_explicit_reference_wrong_target_is_not_counted_as_success():
    class WrongTarget(ReferenceFixtureModel):
        wrong_target = True
    result = trial.evaluate(Database(), 'fixture-model', WrongTarget('supplement'), 'explicit_reference')
    assert not result['expectation_met']


def test_new_prompt_trials_do_not_reuse_previous_synthetic_owner(monkeypatch):
    records = Database()['records']
    before, _ = trial.seed(records, 'continuation')
    monkeypatch.setattr(trial, 'PROMPT_VERSION', 'another-extraction-version')
    after, _ = trial.seed(records, 'continuation')
    assert before != after


@pytest.mark.parametrize('split_call', [1, 2])
def test_trial_rejects_extra_candidates_instead_of_spending_additional_match_calls(split_call):
    class SplitFixture(Model):
        count = 0
        def analyze(self, data):
            self.count += 1
            result = super().analyze(data)
            if self.count == split_call:
                result.items.append(result.items[0].model_copy(deep=True))
            return result
    model = SplitFixture('new')
    with pytest.raises(CaptureError, match='single_.*_candidate_required'):
        trial.evaluate(Database(), 'fixture-model', model, 'continuation')
    assert model.calls == 0
