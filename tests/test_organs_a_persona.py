"""Persona paragraph ownership and A2 speech through Language, without a provider."""
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from Conversation_Memory.answer import answer_system
from config.lumina import load_config
from test_organs_a1 import AT
from test_organs_a2 import a2, send

ROOT = Path(__file__).resolve().parents[1]
VOICE_NUMBERS = (1, 2, 6, 7, 10, 11, 12, 17)


def paragraphs(path):
    return [part.strip() for part in re.split(r'\n\s*\n', path.read_text(encoding='utf-8').strip())]


def appendix(letter):
    card = (ROOT/'docs/TASK_organs_a_persona.md').read_text(encoding='utf-8')
    match = re.search(rf'## 附录 {letter}：.*?~~~text\n(.*?)\n~~~', card, re.S)
    assert match
    return match.group(1)


def test_persona_paragraphs_are_verbatim_ordered_and_fully_assigned():
    original = paragraphs(ROOT/'prompts/chat_background.md')
    assert len(original) == 17
    mind = paragraphs(ROOT/'prompts/chat_background.md')
    voice = paragraphs(ROOT/'prompts/language_voice.md')
    assert mind == original
    assert voice == [original[index-1] for index in VOICE_NUMBERS]
    assert all(part in mind or part in voice for part in original)
    assert len(mind) == 17 and len(voice) == 8
    dialogue = (ROOT/'prompts/dialogue_a2_persona.md').read_text().strip()
    old_part = '\n'.join(line for line in dialogue.splitlines() if not any(
        marker in line for marker in ('{"派活"', '{"答复"', '{"取消"', '{"搁置"',
                                '这四种行动不会在下一步给你结果')))
    assert old_part == appendix('B')
    assert (ROOT/'prompts/language_persona.md').read_text().strip() == appendix('C')
    assert (ROOT/'Memory_lab/judge/persona_prompt_organs_a.md').read_text().strip() == appendix('D')


def test_mind_gets_judgment_and_memory_rules_voice_gets_style_only():
    system = answer_system(AT, '记忆块', (ROOT/'prompts/chat_background.md').read_text(), protocol='a2')
    assert '谈到过去的具体对话或事件时' in system
    assert '关于记忆，你应当了解真实的自己' in system
    assert '你的语言准确、简洁、从容' in system
    assert '你是林素的思考中枢' in system and '"重组"' not in system
    assert '记忆块' not in system


def test_always_renders_every_speech_and_uses_final_text_for_hot_and_trace(tmp_path):
    runtime, scheduler, model, _ = a2(tmp_path, [
        {'回复':'第一句','重组':False,'行动':[{'回忆':'线索'}]},
        {'回复':'第二句','重组':False,'行动':[{'回忆':'接着说'}]},
        {'回复':'第三句','重组':False}], language={'render':'always'})
    finals = iter(('整理后第一句', '整理后第二句', '整理后第三句'))
    def render(system, messages):
        model.rephrases.append((system, messages))
        return next(finals)
    model.complete_text = render
    assert send(scheduler)['response']['text'] == '整理后第一句'
    assert len(model.rephrases) == 3
    assert [turn.text for turn in runtime._hot_store.list_all_raw()] == [
        '你好','整理后第一句','整理后第二句','整理后第三句']
    for step, raw, final in ((1,'第一句','整理后第一句'),
                             (2,'第二句','整理后第二句'),
                             (3,'第三句','整理后第三句')):
        said = scheduler.bus.get(f't1:{step}:0:done')
        assert said['mind_text'] == raw and said['final_text'] == final
        assert said['rephrase_status'] == 'rephrased'
    assert next(iter(runtime._memory.traces.values()))[-1] == '整理后第一句\n整理后第二句\n整理后第三句'
    assert '你的语言准确、简洁、从容' in model.rephrases[0][0]
    assert '谈到过去的具体对话或事件时' not in model.rephrases[0][0]
    assert '她要说的话：\n第一句' in model.rephrases[0][1][0]['content']
    assert '记忆和最近的对话只用来把握语气和分寸' in model.rephrases[0][0]


def test_identical_language_output_is_recorded_as_success(tmp_path):
    runtime, scheduler, model, _ = a2(tmp_path,[{'回复':'原样说出'}],language={'render':'always'})
    def unchanged(system, messages):
        model.rephrases.append((system,messages))
        return '原样说出'
    model.complete_text = unchanged
    assert send(scheduler)['response']['text'] == '原样说出'
    assert scheduler.bus.get('t1:1:0:done')['rephrase_status'] == 'unchanged'
    assert scheduler.bus.get('t1:1:0:rephrase')['status'] == 'unchanged'
    assert len(model.rephrases) == 1
    assert runtime._hot_store.list_all_raw()[-1].text == '原样说出'


def test_language_failure_falls_back_to_mind_words_once(tmp_path):
    runtime, scheduler, model, _ = a2(tmp_path,[{'回复':'还按原话说'}],language={'render':'always'})
    seen=[]
    def fail(system, messages):
        seen.append((system,messages))
        raise RuntimeError('private model error')
    model.complete_text = fail
    assert send(scheduler)['response']['text'] == '还按原话说'
    assert scheduler.bus.get('t1:1:0:done')['rephrase_status'] == 'fallback'
    assert runtime._hot_store.list_all_raw()[-1].text == '还按原话说'
    assert next(iter(runtime._memory.traces.values()))[-1] == '还按原话说'
    scheduler.drain_once()
    assert len(seen) == 1


def test_mind_choice_still_honors_explicit_field(tmp_path):
    runtime, scheduler, model, _ = a2(tmp_path,[
        {'回复':'选择不重组','重组':False},
        {'回复':'选择重组','重组':True}],language={'render':'mind_choice'})
    assert send(scheduler,'t1')['response']['text'] == '选择不重组'
    assert send(scheduler,'t2')['response']['text'] == '这是她最终说的话。'
    assert len(model.rephrases) == 1
    assert scheduler.bus.get('t1:1:0:done')['rephrase_status'] == 'verbatim'
    assert scheduler.bus.get('t2:1:0:done')['rephrase_status'] == 'rephrased'
    assert load_config()['language']['render'] == 'never'


def test_first_speech_waits_for_language_call(tmp_path):
    _, scheduler, model, _ = a2(tmp_path,[{'回复':'Mind 已决定'}],language={'render':'always'})
    entered, release = threading.Event(), threading.Event()
    def render(*_):
        entered.set()
        assert release.wait(5)
        return '语言完成'
    model.complete_text = render
    scheduler.bus.publish('t1','user.message',{'message':'你好'})
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(scheduler.drain_once)
        try:
            assert entered.wait(5)
            assert scheduler.bus.get('t1:reply') is None
            assert scheduler.bus.get('t1:1:0:done') is None
        finally:
            release.set()
        assert future.result(timeout=5)
    assert scheduler.bus.get('t1:reply')['response']['text'] == '语言完成'
