"""Cache-backed, blinded batch judging of packet files."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lab.llm import CachedLLM,RealLLM  # noqa: E402


def validate(text,items):
    text=text.strip()
    fenced=re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```',text,flags=re.S|re.I)
    if fenced:text=fenced.group(1)
    data=json.loads(text)
    if not isinstance(data,dict) or not isinstance(data.get('items'),list):
        raise ValueError('items array missing')
    expected={item['id']:item for item in items}
    rows=data['items']
    if len(rows)!=len(items) or {row.get('id') for row in rows if isinstance(row,dict)}!=set(expected):
        raise ValueError('item ids mismatch')
    for row in rows:
        item=expected[row['id']]
        for side in ('X','Y'):
            answer=row.get(side)
            if not isinstance(answer,dict):raise ValueError(f'{side} score missing')
            for field,allowed in (('should',{0,0.5,1}),('should_not',{0,1})):
                values=answer.get(field)
                if not isinstance(values,list) or len(values)!=len(item['评分要点'][field]):
                    raise ValueError(f'{side}.{field} length')
                if any(type(value) not in (int,float) or value not in allowed for value in values):
                    raise ValueError(f'{side}.{field} value')
            if type(answer.get('wrong')) is not bool:raise ValueError(f'{side}.wrong')
            if type(answer.get('alive')) is not int or not 1<=answer['alive']<=5:
                raise ValueError(f'{side}.alive')
            if not isinstance(answer.get('note'),str):raise ValueError(f'{side}.note')
        if row.get('prefer') not in ('X','Y','tie') or not isinstance(row.get('reason'),str):
            raise ValueError('preference')
    return rows


def recover_individually(llm,system,transcript,items,attempt_base=0):
    """Retry invalid multi-item batches as single items without inventing a score."""
    valid=[];missing=[]
    for item in items:
        shapes={item['id']:{field:len(item['评分要点'][field]) for field in ('should','should_not')}}
        payload={'items':[item],'required_score_lengths_by_id':shapes}
        if transcript is not None:payload['transcript']=transcript
        user=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
        error=''
        for attempt in range(6):
            response=llm.complete(system=system,messages=[{'role':'user','content':user}],
                                  max_tokens=6000,purpose='judge',attempt=attempt_base+attempt)
            try:
                valid.extend(validate(response.text,[item]))
                break
            except (ValueError,TypeError,KeyError,AttributeError) as exc:
                error=f'{type(exc).__name__}: {exc}'
        else:
            skeleton={'items':[{'id':item['id'],
                                'X':{'should':[None]*shapes[item['id']]['should'],
                                     'should_not':[None]*shapes[item['id']]['should_not'],
                                     'wrong':None,'note':'','alive':None},
                                'Y':{'should':[None]*shapes[item['id']]['should'],
                                     'should_not':[None]*shapes[item['id']]['should_not'],
                                     'wrong':None,'note':'','alive':None},
                                'prefer':None,'reason':''}]}
            shaped=json.dumps({**payload,
                               'schema_reminder':'按下面骨架逐一填写每个 null；不得删减 should 或 should_not 数组元素。',
                               'output_skeleton_fill_nulls':skeleton},ensure_ascii=False,separators=(',',':'))
            for attempt in range(3):
                response=llm.complete(system=system,messages=[{'role':'user','content':shaped}],
                                      max_tokens=6000,purpose='judge',attempt=attempt_base+6+attempt)
                try:
                    valid.extend(validate(response.text,[item]))
                    break
                except (ValueError,TypeError,KeyError,AttributeError) as exc:
                    error=f'{type(exc).__name__}: {exc}'
            else:
                missing.append({'ids':[item['id']],'error':error})
    return valid,missing


def _skip(item,reason):
    return {'id':item['id'],'X':None,'Y':None,'prefer':'tie','reason':reason,'skipped':reason}


def run_v2(round_dir:Path,tag:str,batch_size:int,model:str):
    if batch_size not in (5,10):raise ValueError('v2 judge uses batches of 5 or archived size 10')
    if not re.fullmatch(r'[A-Za-z0-9_-]+',tag):raise ValueError('unsafe tag')
    system=(ROOT/'judge'/'judge_prompt_v2.md').read_text(encoding='utf-8')
    output=round_dir/f'judge_{tag}';output.mkdir(parents=True,exist_ok=True)
    base=RealLLM()
    if model:base.model=model
    llm=CachedLLM(base,ROOT/'cache'/'llm',allow_new={'judge'})
    missing=[];estimated=0;skipped_identical=0;skipped_tie=0
    for set_name in json.loads((round_dir/'meta.json').read_text(encoding='utf-8'))['sets']:
        first=json.loads((round_dir/f'packet_{set_name}_1.json').read_text(encoding='utf-8'))['items']
        second=json.loads((round_dir/f'packet_{set_name}_2.json').read_text(encoding='utf-8'))['items']
        outcomes={}
        for order,items in ((1,first),(2,second)):
            to_judge=[];results=[]
            for item in items:
                if order==1 and item['X']==item['Y']:
                    results.append(_skip(item,'identical_answers'));skipped_identical+=1
                elif order==2 and outcomes[item['id']]!='X' and outcomes[item['id']]!='Y':
                    results.append(_skip(item,'first_pass_tie'));skipped_tie+=1
                else:to_judge.append(item)
            estimated+=(len(to_judge)+batch_size-1)//batch_size
            for start in range(0,len(to_judge),batch_size):
                batch=to_judge[start:start+batch_size]
                shapes={item['id']:{field:len(item['评分要点'][field]) for field in ('should','should_not')}
                        for item in batch}
                user=json.dumps({'items':batch,'required_score_lengths_by_id':shapes},
                                ensure_ascii=False,separators=(',',':'))
                error=''
                for attempt in range(3):
                    response=llm.complete(system=system,messages=[{'role':'user','content':user}],
                                          max_tokens=6000,purpose='judge',attempt=attempt)
                    try:
                        results.extend(validate(response.text,batch));break
                    except (ValueError,TypeError,KeyError,AttributeError) as exc:
                        error=f'{type(exc).__name__}: {exc}'
                else:
                    recovered,failed=recover_individually(llm,system,None,batch)
                    results.extend(recovered)
                    missing.extend({'set':set_name,'order':order,**row} for row in failed)
                    print(f'{set_name} order {order} batch {start//batch_size+1}: {error}; recovery {len(recovered)}/{len(batch)}',flush=True)
            results.sort(key=lambda row:int(row['id'].rsplit('-',1)[-1]))
            (output/f'out_{set_name}_{order}.json').write_text(json.dumps({'items':results},ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
            if order==1:outcomes={row['id']:row['prefer'] for row in results}
            print(f'{set_name} order {order}: {len(results)}/{len(items)} outcomes',flush=True)
    status={'expected_batches':estimated,'new_model_calls':llm.new_calls,
            'new_input_tokens':llm.new_input_tokens,'new_output_tokens':llm.new_output_tokens,
            'cache_hits':llm.hits,'identical_skipped':skipped_identical,
            'second_pass_ties_skipped':skipped_tie,'missing_batches':missing}
    (output/'run_status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(status,ensure_ascii=False),flush=True)
    return status


def run(round_dir:Path,tag:str,batch_size:int,model:str):
    meta=json.loads((round_dir/'meta.json').read_text(encoding='utf-8')) if (round_dir/'meta.json').exists() else {}
    if meta.get('evidence_mode'):return run_v2(round_dir,tag,batch_size,model)
    if not re.fullmatch(r'[A-Za-z0-9_-]+',tag):raise ValueError('unsafe tag')
    if batch_size<1:raise ValueError('batch must be positive')
    packets=sorted(round_dir.glob('packet_*_?.json'))
    if not packets:raise ValueError('no packets')
    system=(ROOT/'judge'/'judge_prompt_v1.md').read_text(encoding='utf-8')
    output=round_dir/f'judge_{tag}'
    output.mkdir(parents=True,exist_ok=True)
    expected_calls=sum((len(json.loads(p.read_text(encoding='utf-8'))['items'])+batch_size-1)//batch_size
                       for p in packets)
    payload_counts=Counter()
    for packet_path in packets:
        source=json.loads(packet_path.read_text(encoding='utf-8'))
        for start in range(0,len(source['items']),batch_size):
            batch=source['items'][start:start+batch_size]
            payload_counts[json.dumps({'transcript':source['transcript'],'items':batch},
                                      ensure_ascii=False,separators=(',',':'))]+=1
    print(f'预计新调用上限：{expected_calls} 批（无重试，缓存命中可降低实际值）',flush=True)
    base=RealLLM()
    if model:base.model=model
    llm=CachedLLM(base,ROOT/'cache'/'llm',allow_new={'judge'})
    old_cache=CachedLLM(base,ROOT/'cache'/'llm',cache_only=True)
    missing=[]
    for path in packets:
        packet=json.loads(path.read_text(encoding='utf-8'))
        results=[]
        for start in range(0,len(packet['items']),batch_size):
            batch=packet['items'][start:start+batch_size]
            old_user=json.dumps({'transcript':packet['transcript'],'items':batch},ensure_ascii=False,separators=(',',':'))
            collision=payload_counts[old_user]>1
            batch_attempt_base=(int(hashlib.sha256(f'{round_dir.name}:{path.stem}:{start}'.encode()).hexdigest()[:8],16)*10
                                if collision else 0)
            shapes={item['id']:{field:len(item['评分要点'][field]) for field in ('should','should_not')}
                    for item in batch}
            user=json.dumps({'transcript':packet['transcript'],'items':batch,
                             'required_score_lengths_by_id':shapes},ensure_ascii=False,separators=(',',':'))
            error=''
            # Earlier valid cached judgments retain the same rubric and packet contents.
            from lab.llm import CacheMiss
            reused=False
            if not collision:
                for attempt in range(3):
                    try:
                        previous=old_cache.complete(system=system,messages=[{'role':'user','content':old_user}],
                                                    max_tokens=6000,purpose='judge',attempt=attempt)
                        valid=validate(previous.text,batch)
                    except (CacheMiss,ValueError,TypeError,KeyError,AttributeError):
                        continue
                    results.extend(valid);reused=True;break
            if reused:continue
            for attempt in range(3):
                response=llm.complete(system=system,messages=[{'role':'user','content':user}],
                                      max_tokens=6000,purpose='judge',attempt=batch_attempt_base+attempt)
                try:
                    results.extend(validate(response.text,batch))
                    break
                except (ValueError,TypeError,KeyError,AttributeError) as exc:
                    error=f'{type(exc).__name__}: {exc}'
            else:
                base_attempt=int(hashlib.sha256(f'{round_dir.name}:{path.stem}'.encode()).hexdigest()[:8],16)*10
                recovered,failed=recover_individually(llm,system,packet['transcript'],batch,base_attempt)
                results.extend(recovered)
                missing.extend({'packet':path.name,**row} for row in failed)
                print(f'{path.name} batch {start//batch_size+1}: {error}; single-item recovery {len(recovered)}/{len(batch)}',flush=True)
        target=output/path.name.replace('packet_','out_')
        target.write_text(json.dumps({'items':results},ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
        print(f'{path.name}: {len(results)}/{len(packet["items"])} valid',flush=True)
    status={'expected_batches':expected_calls,'new_model_calls':llm.new_calls,
            'new_input_tokens':llm.new_input_tokens,'new_output_tokens':llm.new_output_tokens,
            'cache_hits':llm.hits+old_cache.hits,'missing_batches':missing}
    (output/'run_status.json').write_text(json.dumps(status,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(status,ensure_ascii=False),flush=True)
    return status


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--round',required=True,type=Path)
    ap.add_argument('--model',help='override LUMINA_MEMLAB_MODEL for this judging run')
    ap.add_argument('--tag',default='deepseek')
    ap.add_argument('--batch',type=int,default=10)
    args=ap.parse_args()
    run(args.round,args.tag,args.batch,args.model)


if __name__=='__main__':main()
