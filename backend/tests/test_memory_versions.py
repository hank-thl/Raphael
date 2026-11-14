from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.repositories.memories import MemoriesRepository, VersionConflict
from app.services.memory_capture import CaptureError
from app.services.memory_versions import MemoryVersionsService


class Memories:
    def __init__(self): self.rows = {}
    def get(self, uid, identifier):
        row = self.rows.get(identifier)
        return deepcopy(row) if row and row['user_uid'] == uid else None
    def create_once(self, doc): return deepcopy(self.rows.setdefault(doc['_id'], deepcopy(doc)))
    def append(self, uid, identifier, expected_revision, version):
        row = self.rows[identifier]
        if row['revision'] != expected_revision: raise VersionConflict('revision_conflict')
        row['versions'].append(deepcopy(version))
        row['revision'] += 1
        return deepcopy(row)


def fixture():
    def candidate(identifier, sources, text):
        return {'_id': identifier, 'user_uid': 'alice', 'text': text, 'kind': ['event'],
            'status': 'occurred', 'time_text': '今天', 'evidence': {'type': 'explicit', 'sources': [
                {'record_id': source, 'quote': source} for source in sources]}}
    rows = {'a1': {'_id': 'a1', 'memories': [candidate('c1', ['r1'], '總額180元')], 'processing': {'model': 'test'}},
            'a2': {'_id': 'a2', 'memories': [candidate('c2', ['r1', 'r2'], '茶飲70元、甜點110元')], 'processing': {'model': 'test'}}}
    capture = Mock()
    capture.get_eligible.side_effect = lambda uid, identifier: deepcopy(rows.get(identifier)) if uid == 'alice' else None
    repo = Memories()
    service = MemoryVersionsService(capture, repo)
    return rows, repo, service


def test_same_memory_keeps_old_revision_and_reuses_supplement():
    rows, repo, service = fixture()
    first = service.create('alice', 'a1', 'c1')
    initial = deepcopy(first['versions'][0])
    second, reused = service.supplement('alice', first['_id'], 1, 'a2', 'c2')
    assert not reused and second['_id'] == first['_id'] and second['revision'] == 2
    assert second['versions'][0] == initial
    assert second['versions'][1]['content']['text'] == '茶飲70元、甜點110元'
    again, reused = service.supplement('alice', first['_id'], 1, 'a2', 'c2')
    assert reused and len(again['versions']) == 2
    assert service.create('alice', 'a1', 'c1')['revision'] == 2


def test_owner_isolation_missing_candidates_and_ineligible_history():
    rows, repo, service = fixture()
    with pytest.raises(CaptureError): service.create('bob', 'a1', 'c1')
    with pytest.raises(CaptureError): service.create('alice', 'a1', 'missing')
    memory = service.create('alice', 'a1', 'c1')
    assert service.get_eligible('bob', memory['_id']) is None
    with pytest.raises(CaptureError): service.supplement('bob', memory['_id'], 1, 'a2', 'c2')
    service.supplement('alice', memory['_id'], 1, 'a2', 'c2')
    rows.pop('a1')
    assert service.get_eligible('alice', memory['_id']) is None


@pytest.mark.parametrize('revision', [0, 2, True, '1'])
def test_stale_revision_is_rejected_without_changes(revision):
    rows, repo, service = fixture()
    memory = service.create('alice', 'a1', 'c1')
    with pytest.raises(CaptureError, match='revision_conflict'):
        service.supplement('alice', memory['_id'], revision, 'a2', 'c2')
    assert repo.rows[memory['_id']]['revision'] == 1


@pytest.mark.parametrize('sources', [['r1'], ['r2'], ['r2', 'r3']])
def test_supplement_requires_old_and_new_sources(sources):
    rows, repo, service = fixture()
    rows['a2']['memories'][0]['evidence']['sources'] = [{'record_id': x, 'quote': x} for x in sources]
    memory = service.create('alice', 'a1', 'c1')
    with pytest.raises(CaptureError, match='supplement_sources_required'):
        service.supplement('alice', memory['_id'], 1, 'a2', 'c2')
    assert len(repo.rows[memory['_id']]['versions']) == 1


def test_repository_compare_and_swap_is_owner_scoped_and_never_overwrites_history():
    collection = Mock()
    collection.find_one_and_update.return_value = None
    repo = MemoriesRepository(collection)
    version = {'created_at': 'time', 'revision': 2}
    with pytest.raises(VersionConflict): repo.append('alice', 'id', 1, version)
    query, update = collection.find_one_and_update.call_args.args
    assert query == {'_id': 'id', 'user_uid': 'alice', 'revision': 1}
    assert update['$push']['versions'] == version
    assert update['$inc']['revision'] == 1
    assert 'versions' not in update['$set']


def test_trial_versions_dry_run_does_not_connect(monkeypatch, capsys):
    from scripts import try_memory_capture
    monkeypatch.setattr('sys.argv', ['try_memory_capture', '--case', 'context', '--with-versions'])
    monkeypatch.setattr(try_memory_capture, 'DatabaseConnection', lambda *a: pytest.fail('No DB'))
    assert try_memory_capture.main() == 0
    assert 'Dry run' in capsys.readouterr().out


def test_concurrent_revision_conflict_is_safe_and_does_not_retry():
    rows, repo, service = fixture()
    memory = service.create('alice', 'a1', 'c1')
    repo.append = Mock(side_effect=VersionConflict('driver details'))
    with pytest.raises(CaptureError, match='revision_conflict') as caught:
        service.supplement('alice', memory['_id'], 1, 'a2', 'c2')
    assert 'driver' not in str(caught.value)
    repo.append.assert_called_once()
    assert repo.rows[memory['_id']]['revision'] == 1


def test_repository_create_duplicate_returns_owner_scoped_existing_memory():
    from pymongo.errors import DuplicateKeyError
    collection = Mock()
    collection.insert_one.side_effect = DuplicateKeyError('private')
    collection.find_one.return_value = {'_id': 'id', 'user_uid': 'alice', 'revision': 2}
    repo = MemoriesRepository(collection)
    assert repo.create_once({'_id': 'id', 'user_uid': 'alice'})['revision'] == 2
    collection.find_one.assert_called_with({'_id': 'id', 'user_uid': 'alice'})
    collection.find_one.return_value = None
    with pytest.raises(VersionConflict): repo.create_once({'_id': 'id', 'user_uid': 'alice'})
