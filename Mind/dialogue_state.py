"""Thought handoff in the new bus state table, never in conversational memory."""
from datetime import datetime


def relative_time(at, now):
    seconds=max(0,(now-datetime.fromisoformat(at)).total_seconds())
    if seconds<60:return '刚才'
    if seconds<3600:return f'{int(seconds//60)}分钟前'
    if seconds<86400:return f'{int(seconds//3600)}小时前'
    return f'{int(seconds//86400)}天前'


class DialogueState:
    def __init__(self,bus,config):
        self.bus,self.config=bus,config['mind']

    def begin(self,tid,now,live):
        saved=self.bus.get(tid+':handoff_in')
        if saved is not None:return saved
        previous=self.bus.get('handoff',state=True) or {'thoughts':[],'carry':[]}
        thoughts=previous['thoughts'][-self.config['thought_window']:]
        carry=previous.get('carry',[])
        refs={row['id']:row for row in [*thoughts,*carry]}
        lines=[]
        status=' · '.join(live.get('states',[]))
        if live.get('focus'):status+=' · '+live['focus']
        if status:lines.append('· 此刻：'+status)
        tasks=live.get('helpers',[])
        if tasks:
            lines.append('手头任务：')
            lines.extend('· '+row['id']+'｜'+row['goal']+'｜'+row['status']+
                         (('（'+ '、'.join(row.get('outputs',[]))+'）') if row.get('outputs') else '')
                         for row in tasks)
        questions=[row for row in tasks if row.get('question')]
        if questions:
            lines.append('还没处理的帮手提问：')
            lines.extend('· '+row['id']+'｜'+row['question']['text'] for row in questions)
        if thoughts:
            lines.append('你最近的思绪（你自己的想法，未必对）：')
            lines.extend(f"· {r['id']}｜{relative_time(r['at'],now)}，{r['reason']}：{r['text']}" for r in thoughts)
        if carry:
            lines.append('你带着的东西：')
            lines.extend(f"· {r['id']}｜"+(relative_time(r['at'],now)+"，"+r['reason']+"：" if 'at' in r else "")+r['text'] for r in carry)
        value={'self':{},'tasks':tasks,'live':live,'status_line':status,
               'block':'你的状态：\n'+'\n'.join(lines) if lines else '',
               'references':refs,'thoughts':thoughts,'carry':carry}
        # Frozen input is authoritative even if the process dies before clearing.
        self.bus.put(tid+':handoff_in',value)
        self.bus.put('handoff',{**previous,'carry':[]},state=True)
        return value

    def finish(self,tid,now,text,carry,refs,*,reason='和他聊天时'):
        previous=self.bus.get('handoff',state=True) or {'thoughts':[],'carry':[]}
        if previous.get('completed')==tid:return
        thoughts=list(previous.get('thoughts',[]))
        text=text.strip()[:self.config['thought_max_chars']]
        if text:
            thoughts.append({'id':'思绪:'+tid,'text':text,'at':now.isoformat(),'reason':reason})
        # Only the window persists as active thoughts; explicit carries have a
        # separate one-thought lease, including references to an older thought.
        selected=[]
        for ref in carry:
            if ref in refs and ref not in {r['id'] for r in selected}:
                selected.append(refs[ref])
            if len(selected)>=self.config['carry_max']:break
        value={'self':{},'tasks':[],'thoughts':thoughts[-self.config['thought_window']:],
               'carry':selected,'completed':tid}
        self.bus.put(tid+':handoff_out',value)
        self.bus.put('handoff',value,state=True)
