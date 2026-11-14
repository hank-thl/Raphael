import json

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.extraction import ExtractionInput
from app.services.gemini import MAX_RESPONSE_BYTES, GeminiError, GeminiExtractor
from scripts.try_gemini import example, main


def valid_result():
    return {
        "content_type": "note", "memory_action": "extract", "context_required": False,
        "context_record_ids": [], "items": [{
            "text": "使用者今天參加陶藝課，感到開心。", "kind": ["event", "emotion"], "topics": ["陶藝"], "status": "occurred",
            "time_text": "今天", "evidence": {"type": "explicit", "sources": [{
                "record_id": "sample_1", "quote": "今天上陶藝課很開心",
            }]},
        }],
    }


def envelope(result=None, **candidate_fields):
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": json.dumps(result if result is not None else valid_result(), ensure_ascii=False)}
    ]}, **candidate_fields}]}


def extractor(handler):
    return GeminiExtractor("test-secret-key", "gemini-test-model", transport=httpx.MockTransport(handler))


def test_real_http_contract_auth_header_schema_and_exact_untranslated_input():
    data = example("multi")
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == 'https://generativelanguage.googleapis.com/v1beta/models/gemini-test-model:generateContent'
        assert request.headers['x-goog-api-key'] == 'test-secret-key'
        assert 'test-secret-key' not in str(request.url)
        payload = json.loads(request.content)
        assert json.loads(payload['contents'][0]['parts'][0]['text']) == data.model_dump(mode='json')
        assert payload['generationConfig']['responseMimeType'] == 'application/json'
        schema = payload['generationConfig']['responseJsonSchema']
        assert schema['additionalProperties'] is False
        assert 'user_uid' not in json.dumps(schema)
        assert 'tools' not in payload
        assert 'untrusted DATA' in payload['systemInstruction']['parts'][0]['text']
        return httpx.Response(200, json=envelope())
    result = extractor(handler).analyze(data)
    assert result.items[0].text == '使用者今天參加陶藝課，感到開心。'
    assert len(calls) == 1


@pytest.mark.parametrize('model,key', [('', 'key'), ('gemini-test', ''), ('gemini-test?key=evil', 'key'), ('../gemini-test','key')])
def test_missing_or_unsafe_configuration_never_calls_provider(model, key):
    def handler(request):
        pytest.fail('Must fail before any network request')
    with pytest.raises(GeminiError, match='not_configured'):
        GeminiExtractor(key, model, transport=httpx.MockTransport(handler)).analyze(example('multi'))


@pytest.mark.parametrize('status,reason', [(400,'configuration_rejected'), (401,'configuration_rejected'),
 (403,'configuration_rejected'), (404,'configuration_rejected'), (429,'rate_limited'),
 (503,'provider_unavailable'), (307,'provider_unavailable')])
def test_provider_errors_are_safe_and_not_retried(status, reason):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={'error': {'message':'private-record-and-secret-key'}},
                              headers={'location':'https://untrusted.example/'})
    with pytest.raises(GeminiError) as caught:
        extractor(handler).analyze(example('multi'))
    assert caught.value.reason == reason
    assert caught.value.status_code == status
    assert 'private-record' not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize('error,reason', [(httpx.ReadTimeout, 'read_timeout'), (httpx.ConnectTimeout, 'connect_timeout'),
 (httpx.WriteTimeout, 'write_timeout'), (httpx.PoolTimeout, 'pool_timeout'), (httpx.ConnectError,'network_error')])
def test_network_errors_do_not_expose_input_or_key(error, reason):
    def handler(request):
        raise error('private-record-and-secret-key',request=request)
    with pytest.raises(GeminiError) as caught:
        extractor(handler).analyze(example('multi'))
    assert caught.value.reason==reason
    assert 'private-record' not in str(caught.value)


@pytest.mark.parametrize('body,reason', [
 ({'promptFeedback':{'blockReason':'SAFETY'}},'blocked'),
 ({'candidates':[]},'missing_candidate'),
 (envelope(finishReason='MAX_TOKENS'),'incomplete_or_blocked'),
 (envelope(finishReason='SAFETY'),'incomplete_or_blocked'),
 ({'candidates':[{'finishReason':'STOP','content':{'parts':[{'functionCall':{}}]}}]},'invalid_response'),
 ({'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'not json'}]}}]},'invalid_response'),
 ([], 'invalid_response'),
])
def test_blocked_truncated_or_invalid_output_is_not_empty_success(body, reason):
    with pytest.raises(GeminiError) as caught:
        extractor(lambda request:httpx.Response(200,json=body)).analyze(example('multi'))
    assert caught.value.reason==reason


def test_response_size_is_bounded():
    with pytest.raises(GeminiError, match='response_too_large'):
        extractor(lambda request:httpx.Response(200,content=b'x'*(MAX_RESPONSE_BYTES+1))).analyze(example('multi'))


@pytest.mark.parametrize('change', ['unknown_source','invented_quote','context_only_item','injected_uid','empty_extract','type_coercion'])
def test_output_validation_rejects_source_fabrication_and_system_fields(change):
    result=valid_result()
    item=result['items'][0]
    if change=='unknown_source':item['evidence']['sources'][0]['record_id']='someone_else'
    if change=='invented_quote':item['evidence']['sources'][0]['quote']='使用者已確定去京都'
    if change=='context_only_item':result['memory_action']='context_only'
    if change=='injected_uid':item['user_uid']='someone_else'
    if change=='empty_extract':result['items']=[]
    if change=='type_coercion':result['context_required']='false'
    with pytest.raises(GeminiError,match='invalid_response'):
        extractor(lambda request:httpx.Response(200,json=envelope(result))).analyze(example('multi'))


def test_multi_source_context_is_supported_and_must_include_current_target():
    data=example('context')
    result=valid_result()
    result['context_required']=True
    result['context_record_ids']=['sample_1']
    result['items'][0].update(text='茶飲70元、甜點110元，共180元。',time_text=None)
    evidence=result['items'][0]['evidence']
    evidence['type']='contextual'
    evidence['sources']=[{'record_id':'sample_1','quote':'共180元'},
                         {'record_id':'sample_2','quote':'茶飲70元，甜點110元。'}]
    parsed=extractor(lambda request:httpx.Response(200,json=envelope(result))).analyze(data)
    assert len(parsed.items[0].evidence.sources)==2
    evidence['sources']=evidence['sources'][:1]
    with pytest.raises(GeminiError,match='invalid_response'):
        extractor(lambda request:httpx.Response(200,json=envelope(result))).analyze(data)


def test_successful_skip_is_distinct_from_provider_failure():
    result={'content_type':'noise','memory_action':'skip','context_required':False,'context_record_ids':[],'items':[]}
    assert extractor(lambda request:httpx.Response(200,json=envelope(result))).analyze(example('noise')).items==[]


@pytest.mark.parametrize('mutation', ['duplicate_ids','unknown_timezone','too_long','extra_uid'])
def test_input_is_bounded_and_has_no_owner_assignment(mutation):
    raw=example('multi').model_dump(mode='json')
    # model_validate_json retains strict JSON datetime handling.
    if mutation=='duplicate_ids':raw['context']=[raw['target']]
    if mutation=='unknown_timezone':raw['timezone']='not/a/timezone'
    if mutation=='too_long':raw['target']['content']='x'*10001
    if mutation=='extra_uid':raw['user_uid']='someone_else'
    with pytest.raises(ValidationError):ExtractionInput.model_validate_json(json.dumps(raw))


def test_config_loads_private_key_without_repr_exposure(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','private-key-value')
    monkeypatch.setenv('GEMINI_MODEL','gemini-test-model')
    get_settings.cache_clear()
    value=get_settings()
    assert value.gemini_api_key=='private-key-value'
    assert value.gemini_model=='gemini-test-model'
    assert 'private-key-value' not in repr(value)
    assert 'private-key-value' not in repr(GeminiExtractor(value.gemini_api_key,value.gemini_model))


def test_cli_dry_run_never_constructs_provider_or_reads_database(monkeypatch,capsys):
    monkeypatch.setattr('sys.argv',['try_gemini','--case','context','--dry-run'])
    monkeypatch.setattr('scripts.try_gemini.GeminiExtractor',lambda *args:pytest.fail('No provider in dry run'))
    assert main()==0
    output=capsys.readouterr().out
    assert '茶飲70元' in output
    assert 'no API request or database access' in output


def test_inferred_candidate_retains_uncertainty_and_cannot_claim_confirmed_state():
    result=valid_result()
    result['items'][0].update(text='使用者可能因為上課感到開心。',status=None)
    result['items'][0]['evidence']['type']='inferred'
    parsed=extractor(lambda request:httpx.Response(200,json=envelope(result))).analyze(example('multi'))
    assert parsed.items[0].evidence.type=='inferred'
    result['items'][0]['status']='confirmed'
    with pytest.raises(GeminiError,match='invalid_response'):
        extractor(lambda request:httpx.Response(200,json=envelope(result))).analyze(example('multi'))


def test_duplicate_json_properties_are_rejected():
    body=json.dumps(envelope())
    body=body[:-1]+',"candidates":[]}'
    with pytest.raises(GeminiError,match='invalid_response'):
        extractor(lambda request:httpx.Response(200,content=body)).analyze(example('multi'))


def test_context_reference_must_be_in_supplied_input():
    result=valid_result()
    result['context_record_ids']=['record-not-provided']
    with pytest.raises(GeminiError,match='invalid_response'):
        extractor(lambda request:httpx.Response(200,json=envelope(result))).analyze(example('multi'))


def test_distinct_quotes_from_one_record_are_allowed_but_exact_duplicates_are_not():
    result=valid_result()
    sources=result['items'][0]['evidence']['sources']
    sources.append({'record_id':'sample_1','quote':'感到開心'})
    # Use a second exact source span from the original input, not the normalized text.
    sources[-1]['quote']='很開心'
    assert len(extractor(lambda request:httpx.Response(200,json=envelope(result))).analyze(example('multi')).items[0].evidence.sources)==2
    sources.append(dict(sources[-1]))
    with pytest.raises(GeminiError,match='invalid_response'):
        extractor(lambda request:httpx.Response(200,json=envelope(result))).analyze(example('multi'))


def test_configured_read_timeout_reaches_http_transport():
    def handler(request):
        assert request.extensions['timeout'] == {'connect':10, 'read':150, 'write':10, 'pool':5}
        return httpx.Response(200,json=envelope())
    value=GeminiExtractor('secret','gemini-test',read_timeout=150,transport=httpx.MockTransport(handler))
    assert value.analyze(example('multi')).items


@pytest.mark.parametrize('value',[0,-1,301,float('inf'),float('nan')])
def test_invalid_read_timeouts_cannot_disable_timeouts(value):
    with pytest.raises(ValueError,match='read_timeout'):
        GeminiExtractor('secret','gemini-test',read_timeout=value)


def test_cli_timeout_diagnostic_is_specific_and_safe(monkeypatch,capsys):
    monkeypatch.setattr('sys.argv',['try_gemini','--read-timeout','150'])
    class FakeExtractor:
        def __init__(self,*args,**kwargs):
            assert kwargs['read_timeout']==150
        def analyze(self,data):
            raise GeminiError('read_timeout')
    monkeypatch.setattr('scripts.try_gemini.GeminiExtractor',FakeExtractor)
    assert main()==1
    output=capsys.readouterr().out
    assert 'read_timeout' in output and '150s' in output and 'Elapsed:' in output
    assert '上陶藝課' not in output


def test_generation_schema_is_simplified_but_local_limits_remain():
    from app.schemas.extraction import ExtractionResult
    from app.services.gemini import generation_schema

    schema = generation_schema()
    def check(node):
        if isinstance(node, dict):
            assert not {'title', 'minLength', 'maxLength', 'minItems', 'maxItems'} & node.keys()
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)
    check(schema)
    assert schema['additionalProperties'] is False
    assert schema['$defs']['MemoryCandidate']['properties']['status']['anyOf']
    assert ExtractionResult.model_json_schema()['properties']['items']['maxItems'] == 30
    result = valid_result()
    result['items'][0]['text'] = 'x' * 4001
    with pytest.raises(GeminiError, match='invalid_response'):
        extractor(lambda request: httpx.Response(200, json=envelope(result))).analyze(example('multi'))


@pytest.mark.parametrize('kind', ['event', [], [' '], ['event', 'event'], ['Event', ' event '], [1], ['x' * 65]])
def test_v3_rejects_invalid_or_legacy_kinds(kind):
    result = valid_result()
    result['items'][0]['kind'] = kind
    with pytest.raises(GeminiError, match='invalid_response'):
        extractor(lambda request: httpx.Response(200, json=envelope(result))).analyze(example('multi'))


@pytest.mark.parametrize('status', ['considering', 'confirmed', 'occurred', 'cancelled', None])
def test_v3_accepts_only_supported_states(status):
    result = valid_result()
    result['items'][0]['status'] = status
    parsed = extractor(lambda request: httpx.Response(200, json=envelope(result))).analyze(example('multi'))
    assert parsed.items[0].kind == ['event', 'emotion']
    assert parsed.items[0].status == status


@pytest.mark.parametrize('status', ['completed', 'tentative', 'undecided', '', 'CONFIRMED', 1])
def test_v3_rejects_unknown_or_legacy_states(status):
    result = valid_result()
    result['items'][0]['status'] = status
    with pytest.raises(GeminiError, match='invalid_response'):
        extractor(lambda request: httpx.Response(200, json=envelope(result))).analyze(example('multi'))


def test_v3_wire_schema_and_prompt_describe_multiple_kinds_and_fixed_states():
    from app.services.gemini import generation_schema, SYSTEM_INSTRUCTION, CONTRACT_VERSION
    schema = generation_schema()['$defs']['MemoryCandidate']['properties']
    assert schema['kind']['type'] == 'array'
    assert schema['kind']['items']['type'] == 'string'
    choices = schema['status']['anyOf']
    assert choices[0]['enum'] == ['considering', 'confirmed', 'occurred', 'cancelled']
    assert choices[1] == {'type': 'null'}
    assert CONTRACT_VERSION.endswith('-v4')
    assert 'A feeling alone has status null' in SYSTEM_INSTRUCTION
