"""Preview offline; --run makes at most 10 candidate-filter calls, with live owner/source checks."""
import argparse
import hashlib
import json
import statistics
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from app.core.config import get_settings
from app.db import DatabaseConnection
from app.repositories.analyses import AnalysesRepository
from app.repositories.memories import MemoriesRepository
from app.services.candidate_filter import (
    CandidateFilter, CompactCandidateFilter, FILTER_VERSION, COMPACT_FILTER_VERSION,
    FilterError, validate_output, validate_compact_output,
)
from app.services.gemini import GeminiError, GeminiExtractor
from app.services.memory_capture import MemoryCaptureService
from app.services.memory_versions import MemoryVersionsService
from scripts.compare_retrieval_strategies import assess
from scripts.evaluate_memory_retrieval import (
    DEFAULT_CASES, PRIVATE_DIR, EvaluationError, checkpoint, load_plan, load_resume,
    private_path, rank_case, read_json, validate_current,
)


def prepare(plan, report):
    cache=load_resume(report,plan)
    if cache.get('state') != 'succeeded':
        raise EvaluationError('completed_report_required')
    _, report_hash=read_json(report)
    memories={m['memory_id']:m for m in plan['memories']}
    jobs=[]
    for case in plan['cases']:
        ranking=rank_case(case,plan['memories'],cache['vectors']['query:'+case['id']]['vector'],cache['vectors'])['ranking']
        candidates=[]
        for row in ranking[:10]:
            memory=memories[row['memory_id']];content=memory['latest']['content']
            candidates.append({'memory_id':memory['memory_id'],'revision':memory['revision'],
                'text':content['text'],'kind':content['kind'],'topics':content.get('topics',[]),
                'status':content['status'],'evidence_type':content['evidence']['type'],
                'time_text':content['time_text']})
        parsed,payload=CandidateFilter(None).prepare(case['question'],candidates)
        digest=hashlib.sha256((FILTER_VERSION+'\n'+payload).encode()).hexdigest()
        jobs.append({'id':case['id'],'case':case,'ranking':ranking,'candidates':candidates,
                     'parsed':parsed,'input_hash':digest})
    return jobs,report_hash


def find_report(plan):
    paths=list(PRIVATE_DIR.glob('evaluation-*.json'))
    if len(paths)>100:
        raise EvaluationError('report_limit')
    matches=[]
    for path in paths:
        data,_=read_json(path)
        if data.get('state')=='succeeded' and data.get('dataset_hash')==plan['dataset_hash']:
            matches.append(path)
    if len(matches)!=1:
        raise EvaluationError('specify_report')
    return matches[0]


def new_state(plan, report_hash, model, prompt_version=FILTER_VERSION):
    return {'evaluation_version':'candidate-filter-evaluation-v1','prompt_version':prompt_version,
            'model':model,'dataset_hash':plan['dataset_hash'],'source_report_hash':report_hash,
            'state':'pending','completed':{},'call_attempts':0,'database_written':False,
            'embedding_calls':0,'human_review_required':True,'semantic_passed':None}


def resume_state(path, plan, report_hash, model, jobs, prompt_version=FILTER_VERSION):
    state,_=read_json(path);expected=new_state(plan,report_hash,model,prompt_version)
    if any(state.get(k)!=expected[k] for k in ('evaluation_version','prompt_version','model','dataset_hash','source_report_hash')):
        raise EvaluationError('resume_mismatch')
    by_id={j['id']:j for j in jobs}
    if not isinstance(state.get('completed'),dict) or not set(state['completed'])<=set(by_id):
        raise EvaluationError('resume_mismatch')
    for key,item in state['completed'].items():
        if item.get('input_hash')!=by_id[key]['input_hash']:
            raise EvaluationError('resume_mismatch')
        validator=validate_compact_output if prompt_version==COMPACT_FILTER_VERSION else validate_output
        validator(item['decision'],by_id[key]['parsed'])
    if type(state.get('call_attempts')) is not int or state['call_attempts']<len(state['completed']):
        raise EvaluationError('resume_mismatch')
    return state


def evaluate(jobs, service, verify, path, state):
    verify()
    state.update(state='running',error=None)
    checkpoint(path,state)
    for job in jobs:
        if job['id'] in state['completed']:
            continue
        verify()
        state['call_attempts']+=1
        checkpoint(path,state)
        usage={};start=perf_counter()
        result=service.filter(job['case']['question'],job['candidates'],usage_callback=usage.update)
        result.validate_candidates(job['parsed'])
        verify()
        selected_ids=set(result.keep_ids)
        selected=[r for r in job['ranking'][:10] if r['memory_id'] in selected_ids]
        state['completed'][job['id']]={'input_hash':job['input_hash'],
            'candidate_ids':[c['memory_id'] for c in job['candidates']], 'decision':result.model_dump(),
            'filtered':assess(job['case'],selected),
            'baselines':{f'top{k}':assess(job['case'],job['ranking'][:k]) for k in (3,5,10)},
            'elapsed_ms':round((perf_counter()-start)*1000),'usage':usage}
        checkpoint(path,state)
    verify()
    items=list(state['completed'].values())
    positives=[v['filtered'] for v in items if v['filtered']['required_count']]
    no_evidence=[v['filtered'] for v in items if not v['filtered']['required_count'] and not any(
        j['id']==v['filtered']['id'] and j['case']['optional_memory_ids'] for j in jobs)]
    state['summary']={'case_count':len(items),'positive_cases':len(positives),
        'full_required_coverage_cases':sum(v['required_recall']==1 for v in positives),
        'empty_positive_cases':sum(v['empty_selection'] for v in positives),
        'irrelevant_candidates_kept':sum(len(v['filtered']['irrelevant_selected_ids']) for v in items),
        'no_evidence_cases_with_candidates':sum(not v['empty_selection'] for v in no_evidence),
        'note':'In-sample draft labels. Compare top10 baseline for equal candidate budget; no answer generated.'}
    state['state']='succeeded'
    checkpoint(path,state)
    return state



def detailed_baseline(plan, report_hash, jobs, path=None):
    if path is None:
        paths=list(PRIVATE_DIR.glob('filter-evaluation-*.json'))
        if len(paths)>100:
            raise EvaluationError('report_limit')
        matches=[]
        for candidate in paths:
            raw,_=read_json(candidate)
            if (raw.get('state')=='succeeded' and raw.get('dataset_hash')==plan['dataset_hash']
                    and raw.get('source_report_hash')==report_hash and raw.get('prompt_version')==FILTER_VERSION):
                matches.append(candidate)
        if len(matches)!=1:
            raise EvaluationError('specify_baseline')
        path=matches[0]
    raw,file_hash=read_json(path)
    state=resume_state(path,plan,report_hash,raw.get('model'),jobs)
    if state.get('state')!='succeeded' or set(state['completed'])!={j['id'] for j in jobs}:
        raise EvaluationError('baseline_mismatch')
    for job in jobs:
        if state['completed'][job['id']]['candidate_ids']!=[c['memory_id'] for c in job['candidates']]:
            raise EvaluationError('baseline_mismatch')
    return state,file_hash


def compare_saved(state, baseline, jobs):
    # Historical paired observations, NOT a randomized simultaneous A/B experiment.
    if state['model']!=baseline['model']:
        raise EvaluationError('baseline_mismatch')
    comparisons=[]
    for job in jobs:
        key=job['id'];old=baseline['completed'][key];new=state['completed'][key]
        old_result=validate_output(old['decision'],job['parsed'])
        new_result=validate_compact_output(new['decision'],job['parsed'])
        before=set(old_result.keep_ids);after=set(new_result.keep_ids)
        def integer_usage(row,key):
            value=row.get('usage',{}).get(key)
            return value if type(value) is int and value>=0 else None
        deltas={}
        for name in ('promptTokenCount','candidatesTokenCount','thoughtsTokenCount','totalTokenCount'):
            a,b=integer_usage(old,name),integer_usage(new,name)
            deltas[name]=b-a if a is not None and b is not None else None
        comparisons.append({'id':key,'kept_ids_equal':before==after,
            'added_ids':sorted(after-before),'removed_ids':sorted(before-after),
            'answerability_before':old_result.answerability,'answerability_after':new_result.answerability,
            'limitation_after':new_result.limitation,
            'before':assess(job['case'],[r for r in job['ranking'][:10] if r['memory_id'] in before]),
            'after':assess(job['case'],[r for r in job['ranking'][:10] if r['memory_id'] in after]),
            'usage_delta':deltas,'elapsed_ms_before':old['elapsed_ms'],'elapsed_ms_after':new['elapsed_ms'],
            'elapsed_ms_delta':new['elapsed_ms']-old['elapsed_ms'],
            'human_review_required':True,'semantic_passed':None})
    def totals(rows,key):
        values=[row.get('usage',{}).get(key) for row in rows]
        return sum(values) if all(type(v) is int and v>=0 for v in values) else None
    old_rows=[baseline['completed'][j['id']] for j in jobs]
    new_rows=[state['completed'][j['id']] for j in jobs]
    return {'kind':'historical_paired_comparison','baseline_prompt_version':FILTER_VERSION,
        'candidate_prompt_version':COMPACT_FILTER_VERSION,'same_model_name':True,
        'same_question_and_candidates':True,'baseline_provider_calls':0,
        'kept_ids_equal_cases':sum(c['kept_ids_equal'] for c in comparisons),
        'median_elapsed_ms_before':statistics.median(r['elapsed_ms'] for r in old_rows),
        'median_elapsed_ms_after':statistics.median(r['elapsed_ms'] for r in new_rows),
        'usage_totals':{k:{'before':totals(old_rows,k),'after':totals(new_rows,k)} for k in
            ('promptTokenCount','candidatesTokenCount','thoughtsTokenCount','totalTokenCount')},
        'cases':comparisons,'human_review_required':True,
        'note':'Historical single samples; timing/thinking may vary. No universal speed or semantic equivalence claim.'}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path)
    parser.add_argument('--cases',type=Path,default=DEFAULT_CASES)
    parser.add_argument('--run',action='store_true')
    parser.add_argument('--resume',type=Path)
    parser.add_argument('--variant',choices=('detailed','compact'),default='detailed')
    parser.add_argument('--baseline',type=Path)
    args=parser.parse_args(argv)
    state,path,connection=None,None,None
    try:
        plan=load_plan(args.cases);report=args.report or find_report(plan)
        jobs,report_hash=prepare(plan,report)
        compact=args.variant=='compact'
        version=COMPACT_FILTER_VERSION if compact else FILTER_VERSION
        baseline,baseline_hash=detailed_baseline(plan,report_hash,jobs,args.baseline) if compact else (None,None)
        if not args.run:
            print(json.dumps({'mode':'preview','provider_calls':0,'database_accessed':False,
                'planned_calls_max':len(jobs),'candidates_per_question_max':10,
                'embedding_calls':0,'database_written':False,'prompt_version':version,
                'baseline_reused':compact,'baseline_provider_calls':0}))
            return 0
        settings=get_settings()
        if not all((settings.admin_uid,settings.mongodb_uri,settings.gemini_api_key,settings.gemini_model)):
            raise EvaluationError('not_configured')
        if baseline is not None and baseline['model']!=settings.gemini_model:
            raise EvaluationError('baseline_mismatch')
        state=resume_state(args.resume,plan,report_hash,settings.gemini_model,jobs,version) if args.resume else new_state(plan,report_hash,settings.gemini_model,version)
        if args.resume and compact and state.get('baseline_hash')!=baseline_hash:
            raise EvaluationError('baseline_mismatch')
        if compact:state['baseline_hash']=baseline_hash
        connection=DatabaseConnection(settings);connection.open()
        if connection.database is None:
            raise EvaluationError('database_unavailable')
        db=connection.database
        reader=MemoryVersionsService(MemoryCaptureService(db['records'],AnalysesRepository(db['analyses']),None,''),
                                     MemoriesRepository(db['memories']))
        verify=lambda:validate_current(reader,settings.admin_uid,plan['memories'])
        verify()
        prefix='filter-compact-evaluation-' if compact else 'filter-evaluation-'
        path=private_path(args.resume) if args.resume else PRIVATE_DIR/(prefix+uuid4().hex+'.json')
        service=CompactCandidateFilter if compact else CandidateFilter
        state=evaluate(jobs,service(GeminiExtractor(settings.gemini_api_key,settings.gemini_model)),verify,path,state)
        if compact:
            state['comparison']=compare_saved(state,baseline,jobs)
            checkpoint(path,state)
        print(json.dumps({'state':'succeeded','report':str(path),'call_attempts':state['call_attempts'],
                          'summary':state['summary'],'database_written':False},ensure_ascii=False))
        return 0
    except Exception as exc:
        allowed={'resume_mismatch','completed_report_required','report_limit','specify_report','not_configured',
                 'sources_changed','database_unavailable','snapshot_mismatch','input_limit','private_path_required',
                 'specify_baseline','baseline_mismatch'}
        reason=exc.reason if isinstance(exc,GeminiError) else str(exc) if isinstance(exc,(EvaluationError,FilterError)) and str(exc) in allowed|{
            'invalid_filter_result','invalid_candidates','candidate_limit','duplicate_candidate','invalid_question'} else 'filter_evaluation_failed'
        if path is not None and state is not None:
            state.update(state='failed',error=reason)
            try:checkpoint(path,state)
            except Exception:path=None
        print(json.dumps({'state':'failed','reason':reason,'report':str(path) if path else None,
                          'automatic_retry':False,'database_written':False}))
        return 1
    finally:
        if connection is not None:connection.close()


if __name__=='__main__':
    raise SystemExit(main())
