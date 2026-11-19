"""Offline, in-sample candidate comparisons from saved private vectors. No network or DB."""
import argparse
import json
import math
from pathlib import Path
from uuid import uuid4

from scripts.evaluate_memory_retrieval import (
    DEFAULT_CASES, PRIVATE_DIR, EvaluationError, checkpoint, load_plan, load_resume, rank_case,
)

STRATEGIES = ('top3', 'top5', 'floor060', 'floor065', 'floor070', 'gap006',
              'topic_top3', 'floor065_topic')


def select(ranking, strategy, topics_by_id, topic_hints=()):
    """Selection never receives gold IDs, expected answers or relevance labels."""
    if strategy not in STRATEGIES:
        raise EvaluationError('unknown_strategy')
    if strategy == 'top3':
        return ranking[:3]
    if strategy == 'top5':
        return ranking[:5]
    if strategy.startswith('floor'):
        floor = {'floor060':0.60, 'floor065':0.65, 'floor070':0.70, 'floor065_topic':0.65}[strategy]
        chosen = [r for r in ranking if r['score'] >= floor][:5]
    elif strategy == 'gap006':
        chosen = ranking[:5]
        gaps = [chosen[i]['score'] - chosen[i+1]['score'] for i in range(len(chosen)-1)]
        if gaps and max(gaps) >= 0.06:
            chosen = chosen[:gaps.index(max(gaps))+1]
    else:
        chosen = ranking[:3]
    if strategy in {'topic_top3', 'floor065_topic'}:
        # Explicit reviewer-supplied scenario, not an automatic question classifier.
        seen = {r['memory_id'] for r in chosen}
        for row in ranking:
            if len(chosen) >= 5:
                break
            if row['memory_id'] not in seen and set(topic_hints) & set(topics_by_id.get(row['memory_id'], [])):
                chosen.append(row)
                seen.add(row['memory_id'])
    return chosen


def assess(case, selected):
    required = set(case['required_memory_ids'])
    relevant = required | set(case['optional_memory_ids'])
    ids = {r['memory_id'] for r in selected}
    return {'id':case['id'], 'question':case['question'],
        'selected':[{'memory_id':r['memory_id'], 'score':r['score'], 'text':r['text']} for r in selected],
        'required_recall':len(ids & required)/len(required) if required else None,
        'precision':len(ids & relevant)/len(ids) if ids else None,
        'missing_required_ids':sorted(required-ids), 'irrelevant_selected_ids':sorted(ids-relevant),
        'empty_selection':not selected, 'required_count':len(required),
        'answerability':case['answerability']}


def compare(plan, cache, *, coverage_case=None, topics=()):
    if cache.get('state') != 'succeeded':
        raise EvaluationError('completed_report_required')
    if coverage_case is not None and coverage_case not in {c['id'] for c in plan['cases']}:
        raise EvaluationError('unknown_coverage_case')
    available = {topic for m in plan['memories'] for topic in m['latest']['content'].get('topics', [])}
    if topics and (not coverage_case or not set(topics) <= available):
        raise EvaluationError('invalid_topic_hints')
    if coverage_case and not topics:
        raise EvaluationError('invalid_topic_hints')
    topics_by_id = {m['memory_id']:m['latest']['content'].get('topics', []) for m in plan['memories']}
    rankings = {case['id']:rank_case(case,plan['memories'],cache['vectors']['query:'+case['id']]['vector'],
                                     cache['vectors'])['ranking'] for case in plan['cases']}
    reports = {}
    for strategy in STRATEGIES:
        results=[]
        for case in plan['cases']:
            hints=topics if case['id']==coverage_case else ()
            results.append(assess(case, select(rankings[case['id']],strategy,topics_by_id,hints)))
        positives = [c for c in results if c['required_count']]
        negatives = [c for c in results if not c['required_count'] and not any(
            p['id']==c['id'] and p['optional_memory_ids'] for p in plan['cases'])]
        reports[strategy]={'cases':results, 'summary':{
            'positive_cases':len(positives),
            'full_required_coverage_cases':sum(c['required_recall']==1 for c in positives),
            'empty_positive_cases':sum(c['empty_selection'] for c in positives),
            'macro_required_recall':math.fsum(c['required_recall'] for c in positives)/len(positives) if positives else None,
            'macro_precision_positive_empty_as_zero':math.fsum(c['precision'] or 0 for c in positives)/len(positives) if positives else None,
            'selected_total':sum(len(c['selected']) for c in results),
            'no_positive_cases':len(negatives),
            'no_positive_cases_with_candidates':sum(not c['empty_selection'] for c in negatives)}}
    return {'comparison_version':'retrieval-candidate-comparison-v1',
            'dataset_hash':plan['dataset_hash'],'snapshot_hash':plan['snapshot_hash'],
            'profile':cache['profile'],'provider_calls':0,'database_accessed':False,'database_written':False,
            'selection_budget':5, 'strategies':reports,
            'manual_topic_scenario':{'case':coverage_case,'topics':list(topics),
                                     'automatic_routing_implemented':False},
            'in_sample_only':True,'generalization_validated':False,'selected_strategy':None,
            'limitations':['Draft labels and thresholds evaluated on the same 10 questions; no held-out validation.',
                           'Explicit topic hints are supplied by the reviewer, not inferred by an implemented router.',
                           'No live source revalidation; results describe only the bound snapshot. Never feed this report to answering.',
                           'No new questions embedded, no answer generation, no Atlas or deployment.']}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--cases',type=Path,default=DEFAULT_CASES)
    parser.add_argument('--coverage-case')
    parser.add_argument('--topic',action='append',default=[])
    args=parser.parse_args(argv)
    try:
        plan=load_plan(args.cases)
        cache=load_resume(args.report,plan)
        result=compare(plan,cache,coverage_case=args.coverage_case,topics=args.topic)
        path=PRIVATE_DIR/('comparison-'+uuid4().hex+'.json')
        checkpoint(path,result)
        print(json.dumps({'report':str(path),'provider_calls':0,'database_accessed':False,
                          'strategies':{k:v['summary'] for k,v in result['strategies'].items()},
                          'in_sample_only':True,'selected_strategy':None},ensure_ascii=False))
        return 0
    except Exception as exc:
        allowed={'completed_report_required','unknown_coverage_case','invalid_topic_hints','resume_mismatch',
                 'snapshot_mismatch','input_limit','private_path_required'}
        reason=str(exc) if isinstance(exc,EvaluationError) and str(exc) in allowed else 'comparison_failed'
        print(json.dumps({'error':reason,'provider_calls':0,'database_accessed':False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
