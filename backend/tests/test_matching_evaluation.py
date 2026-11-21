from copy import deepcopy
from unittest.mock import Mock
import json

import pytest
from pymongo.errors import DuplicateKeyError

from app.schemas.extraction import ExtractionResult
from app.services.memory_capture import CaptureError
from scripts.try_memory_capture import describe_memory
from scripts import try_memory_matching as trial


class Cursor:
    def __init__(self, rows): self.rows = rows
    def sort(self, fields, direction=None):
        fields = fields if isinstance(fields, list) else [(fields, direction)]
        for key, order in reversed(fields): self.rows.sort(key=lambda row: row[key], reverse=order == -1)
        return self
    def limit(self, count): return self.rows[:count]
    def __iter__(self): return iter(self.rows)


class Collection:
    def __init__(self): self.rows = {}
    def find_one(self, query):
        return next((deepcopy(row) for row in self.rows.values() if all(row.get(k) == v for k, v in query.items())), None)
    def find(self, query, *args):
        return Cursor([deepcopy(row) for row in self.rows.values() if all(row.get(k) == v for k, v in query.items())])
    def insert_one(self, row):
        if row['_id'] in self.rows: raise DuplicateKeyError('duplicate')
        self.rows[row['_id']] = deepcopy(row)
    def update_one(self, query, update, upsert=False):
        return self.find_one_and_update(query, update, upsert=upsert)
    def find_one_and_update(self, query, update, upsert=False, **kwargs):
        row = self.find_one(query)
        new = row is None
        if new:
            if not upsert: return None
            row = deepcopy(query)
            if row['_id'] in self.rows: raise DuplicateKeyError('duplicate')
        if new: row.update(deepcopy(update.get('$setOnInsert', {})))
        row.update(deepcopy(update.get('$set', {})))
        for key, value in update.get('$inc', {}).items(): row[key] += value
        for key, value in update.get('$push', {}).items(): row[key].append(deepcopy(value))
        self.rows[row['_id']] = row
        return deepcopy(row)


class Database(dict):
    def __missing__(self, key):
        result = self[key] = Collection()
        return result


class Model:
    def __init__(self, action): self.action, self.calls = action, 0
    def analyze(self, data):
        return ExtractionResult.model_validate({'content_type': 'journal', 'memory_action': 'extract',
            'context_required': False, 'context_record_ids': [], 'items': [{
                'text': data.target.content, 'kind': ['event'], 'topics': ['消費'], 'status': 'occurred', 'time_text': None,
                'evidence': {'type': 'explicit', 'sources': [{'record_id': data.target.record_id, 'quote': data.target.content}]}}]})
    def generate(self, payload, schema, instruction):
        self.calls += 1
        data = json.loads(payload)
        if self.action != 'supplement': return {'action': self.action, 'memory_id': None, 'merged': None}
        return {'action': 'supplement', 'memory_id': data['options'][0]['memory_id'], 'merged': {
            'text': '今天買茶飲與甜點共180元，茶飲70元、甜點110元。', 'kind': ['event'], 'topics': ['消費'], 'status': 'occurred',
            'time_text': '今天', 'evidence': {'type': 'contextual', 'sources': [
                {'record_id': row['record_id'], 'quote': row['content']} for row in data['raw_records']]}}}


@pytest.mark.parametrize('case', ['new', 'uncertain', 'supplement'])
def test_evaluation_full_flow_replays_without_new_decision_and_reports_sources(case):
    db, model = Database(), Model(case)
    first = trial.evaluate(db, 'gemini-test', model, case)
    second = trial.evaluate(db, 'gemini-test', model, case)
    assert first['expectation_met'] and second['expectation_met']
    assert not first['analysis_reused'] and second['analysis_reused']
    assert model.calls == 1
    assert first['result_memories'] == second['result_memories']
    result = second['result_memories'][0]
    assert result['revision'] == (2 if case == 'supplement' else 1)
    if case == 'supplement':
        assert len(result['versions']) == 2
        assert len(result['versions'][-1]['evidence']['sources']) == 2
        assert '180' in result['versions'][-1]['text']
    else:
        assert result['memory_id'] not in {row['memory_id'] for row in second['baseline_memories']}
    assert all(name.startswith('memory_matching_eval_') for name in db)


def test_model_mismatch_is_reported_without_forcing_expected_answer():
    result = trial.evaluate(Database(), 'gemini-test', Model('new'), 'uncertain')
    assert result['expectation_met'] is False
    assert result['routing'][0]['action'] == 'new'


def test_optional_embedding_uses_result_revision_and_synthetic_collections(monkeypatch):
    db = Database()
    jobs = Mock()
    jobs.claim.return_value = None
    service = Mock()
    service.current.return_value = {'revision': 2}
    jobs_factory = Mock(return_value=jobs)
    service_factory = Mock(return_value=service)
    monkeypatch.setattr('app.repositories.embedding_jobs.EmbeddingJobsRepository', jobs_factory)
    monkeypatch.setattr('app.services.memory_embeddings.MemoryEmbeddingService', service_factory)
    result = trial.evaluate(db, 'test', Model('supplement'), 'supplement', embedder=Mock())
    identifier = result['routing'][0]['memory_id']
    assert result['result_memories'][0]['revision'] == 2
    jobs.claim.assert_called_once_with(next(iter(db[f'{trial.PREFIX}_memories'].rows.values()))['user_uid'],
                                       memory_id=identifier)
    assert jobs_factory.call_args.args[0] is db[f'{trial.PREFIX}_memories']
    assert result['embedding_results'][0]['vector_revision'] == 2
    assert result['planned_provider_calls_max'] == 4
    assert all(name.startswith('memory_matching_eval_') for name in db)


@pytest.mark.parametrize('case', list(trial.SCENARIOS))
def test_default_dry_run_has_no_db_or_api(monkeypatch, capsys, case):
    monkeypatch.setattr('sys.argv', ['try_memory_matching', '--case', case])
    monkeypatch.setattr(trial, 'DatabaseConnection', lambda *a: pytest.fail('No DB'))
    assert trial.main() == 0
    assert 'Dry run' in capsys.readouterr().out


def test_scenario_owners_are_isolated_and_seeds_are_idempotent():
    records = Collection()
    identities = [trial.seed(records, case) for case in trial.SCENARIOS]
    assert len({uid for uid, ids in identities}) == len(trial.SCENARIOS)
    count = len(records.rows)
    assert trial.seed(records, 'new') == identities[0]
    assert len(records.rows) == count


def test_memory_details_never_display_ineligible_history():
    versions = Mock()
    versions.get_eligible.return_value = None
    with pytest.raises(CaptureError, match='memory_no_longer_eligible'):
        describe_memory(versions, 'alice', 'memory-id')
