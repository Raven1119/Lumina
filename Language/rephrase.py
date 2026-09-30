"""Language's one-call request seam, shared with the blind evaluation arm."""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def language_request(meaning,memory_block,status_line,recent,*,recent_turns=6):
    voice=(ROOT/'prompts/language_voice.md').read_text(encoding='utf-8').strip()
    task=(ROOT/'prompts/language_persona.md').read_text(encoding='utf-8').strip()
    system=voice+'\n\n'+task
    dialogue='\n'.join(('他' if t['role']=='user' else 'Lumina')+'：'+t.get('text',t.get('content',''))
                       for t in recent[-recent_turns:])
    user='\n\n'.join(('最近的对话：\n'+dialogue,'这次的记忆块：\n'+memory_block,
                      '她此刻的状态行：\n'+status_line,'她要说的话：\n'+meaning))
    return system,[{'role':'user','content':user}]
