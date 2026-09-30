"""Stage B language policy checks with scripted model and isolated owners."""
from pathlib import Path
from Conversation_Memory.answer import answer_system

from test_organs_a2 import a2, send
from test_organs_a1 import AT


def test_proactive_only_skips_dialogue_and_renders_proactive(tmp_path):
    runtime, scheduler, model, _ = a2(
        tmp_path, [{'回复': '对话原话'}], language={'render': 'proactive_only'})
    assert send(scheduler)['response']['text'] == '对话原话'
    assert model.rephrases == []
    user = scheduler.bus.get('t1:user')
    rendered = scheduler.emit('active:1', 'mind.say', {
        'text': '主动原话', 'user': user, 'kind': 'model', 'phase': 'model_chat',
        'protocol': 'a2', 'proactive': True, 'rephrase': False,
        'memory_block': '', 'status_line': '', 'recent': [],
    })
    assert rendered['final_text'] == '这是她最终说的话。'
    assert len(model.rephrases) == 1
    assert [turn.text for turn in runtime._hot_store.list_all_raw()] == [
        '你好', '对话原话', '这是她最终说的话。']
    assert '你的英文名是 Lumina' in answer_system(
        AT, '', Path('prompts/chat_background.md').read_text(), protocol='a2')
    assert not Path('prompts/mind_identity.md').exists()


def test_never_sends_proactive_speech_verbatim(tmp_path):
    runtime, scheduler, model, _ = a2(
        tmp_path, [{'回复': '对话原话'}], language={'render': 'never'})
    assert send(scheduler)['response']['text'] == '对话原话'
    user = scheduler.bus.get('t1:user')
    rendered = scheduler.emit('active:never', 'mind.say', {
        'text': '主动原话', 'user': user, 'kind': 'model', 'phase': 'model_chat',
        'protocol': 'a2', 'proactive': True, 'memory_block': '',
        'status_line': '', 'recent': [],
    })
    assert rendered['final_text'] == '主动原话'
    assert rendered['rephrase_status'] == 'verbatim'
    assert model.rephrases == []
    assert [turn.text for turn in runtime._hot_store.list_all_raw()] == [
        '你好', '对话原话', '主动原话']
