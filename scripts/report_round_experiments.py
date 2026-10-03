"""Publish scalar experiment evidence; no private source text or provider bodies."""
from pathlib import Path
from decimal import Decimal as D
import json,sys,collections
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from evals.reviews import load_bundle
from evals.automated_reviews import review_verdict
from evals.suite import fingerprint
ROUND=ROOT/'evaluation/runs/round3'
SPECIAL=json.loads((ROOT/'evaluation/round2_comparison_results.json').read_text())['special_cases']
result={'version':'round3-experiments-v2','status':'in progress; inspect immutable run statuses',
        'human_calibration':False,'hosted_trace_delivery':'unavailable: monthly unique-trace quota',
        'special_cases':SPECIAL,'runs':[]}
for directory in sorted(ROUND.iterdir()):
 if not (directory/'bundle.json').exists():continue
 bundle=load_bundle(directory);budget=json.loads((directory/'budget.json').read_text());calls=budget['calls']
 rows=[]
 for row in bundle['cases']:
  if row['status']=='queued':continue
  j=row.get('judgment') or {}
  costs={}
  for phase in ('generation','judging'):
   paid=[c for c in calls if c.get('case_id')==row['id'] and c['phase']==phase]
   costs[phase]={'reported_usd':str(sum((D(c.get('cost_usd','0')) for c in paid),D(0))),
                 'unknown_reserved_usd':str(sum((D(c['reserved_usd']) for c in paid if c['status']=='reserved'),D(0))),
                 'physical_requests':len(paid),'reported_tokens':sum(c.get('usage',{}).get('total_tokens') or 0 for c in paid) if all(c.get('usage',{}).get('total_tokens') is not None for c in paid) else None}
  rows.append({'case_id':row['id'],'flow':row['flow'],'status':row['status'],
    'error_kind':(row.get('error') or {}).get('kind'),'output_hash':row.get('output_hash'),
    'checks':row.get('checks'), 'verdict':review_verdict(j,row.get('checks'),flow=row['flow']) if row['status']=='completed' else 'unavailable',
    **{k:j.get(k) for k in ('grounding_status','correctness','coverage','usefulness','layout')},
    'unsupported_claim_count':len(j.get('unsupported_claims',[])), 'costs':costs,
    'source_fingerprint':row.get('source_fingerprint'),
    'generation_capture_hashes':list((row.get('evidence') or {}).get('generation_capture_hashes',{}).values()),
    'latency_seconds':(row.get('metrics') or {}).get('latency_seconds'),
    'local_sdk_latency_seconds':((row.get('metrics') or {}).get('local_sdk_span') or {}).get('latency_seconds'),
    'resources':(row.get('metrics') or {}).get('resources')})
 result['runs'].append({'run':directory.name,'experiment_fingerprint':bundle['fingerprint'],
   'implementation_sha256':bundle['config'].get('implementation_sha256'),
   'retrieval_mode':bundle['config']['retrieval_mode'],'candidate':bundle['config'].get('candidate'),
   'review_version':bundle['config'].get('review_version'),
   'bindings_fingerprint':fingerprint(bundle['config']['bindings']),
   'model_environment':bundle['config'].get('model_environment'),
   'reported_usd':str(sum((D(c.get('cost_usd','0')) for c in calls),D(0))),
   'unknown_reserved_usd':str(sum((D(c['reserved_usd']) for c in calls if c['status']=='reserved'),D(0))),
   'artifact_recovery':(directory/'sheet-recovery.json').exists(),
   'cases':rows})
# Saved reviews and production windows also spend from the round. Count every
# registered child ledger once, including those without a generation bundle.
ledger=json.loads((ROUND/'round-budget.json').read_text())
result['allocations']=ledger['allocations'];result['allocation_transfers']=ledger.get('transfers',[])
result['saved_reviews']=[]
result['budget_children']=[]
for name, registration in sorted(ledger['runs'].items()):
 directory=ROUND/registration['directory']
 budget_path=directory/'budget.json'
 if budget_path.exists():
  calls=json.loads(budget_path.read_text())['calls']
  reported=sum((D(c.get('cost_usd','0')) for c in calls),D(0))
  held=sum((D(c['reserved_usd']) for c in calls if c['status']=='reserved'),D(0))
 else:
  reported=D(0);held=D(registration['committed_usd'])
 result['budget_children'].append({'run':name,'phase':registration['phase'],
     'reported_usd':str(reported),'unknown_reserved_usd':str(held)})
 review_path=directory/'automated_reviews.json'
 if review_path.exists():
  review=json.loads(review_path.read_text());rows=[]
  for row in review['cases']:
   j=row.get('judgment') or {}
   rows.append({**{k:row.get(k) for k in ('run','case_id','flow','output_hash','status','verdict')},
       **{k:j.get(k) for k in ('grounding_status','correctness','coverage','usefulness','layout')},
       'error_kind':(row.get('error') or {}).get('kind')})
  result['saved_reviews'].append({'run':name,'review_version':review['config']['review_version'],
       'judge_model':review['config']['judge_model'],
       'writer_model_labels_hidden':review['config'].get('writer_model_labels_hidden',False),
       'fingerprint':review['fingerprint'],'cases':rows})
result['provider_reported_usd']=str(sum((D(r['reported_usd']) for r in result['budget_children']),D(0)))
result['unknown_reserved_usd']=str(sum((D(r['unknown_reserved_usd']) for r in result['budget_children']),D(0)))
result['conservatively_committed_usd']=str(D(result['provider_reported_usd'])+D(result['unknown_reserved_usd']))
final = next((run for run in result['runs'] if run['run'] == 'final-54'), None)
if final and len(final['cases']) == 54 and all(row['status'] in {'completed', 'failed'} for row in final['cases']):
 result['selection']={'retained_candidates':[17], 'new_model_defaults':[],
     'reason':'Larger citations preserve content and page counts in six paired renders; model/prompt gains did not reliably replicate.'}
 result['status']='native selection complete; production comparison pending'
 window=ROUND/'production-paired/after-window.json'
 if window.exists():
  state=json.loads(window.read_text())
  result['production_revision']=(state.get('health') or {}).get('build_revision')
  result['status']='native selection complete; production comparison '+state['status']
p=ROOT/'evaluation/round3_screen_results.json'
p.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:result[k] for k in ('provider_reported_usd','unknown_reserved_usd','conservatively_committed_usd')}))
