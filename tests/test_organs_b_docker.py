"""Opt-in real Docker boundaries with scripted decisions and synthetic data."""
import os
import time
import uuid
from pathlib import Path

import pytest

from Execution.execution import ClaimComplete, IPythonCode, Wait
from Execution.pool import HelperPool
from Execution.sandbox import DockerIPython
from Nervous.bus import EventBus
from config.lumina import load_config


pytestmark = pytest.mark.skipif(not os.environ.get('LUMINA_DOCKER_TEST_WORKSPACE'),
                                 reason='set a disposable Docker-mountable workspace')


def _workspace():
    root = Path(os.environ['LUMINA_DOCKER_TEST_WORKSPACE']).resolve()/uuid.uuid4().hex
    (root/'tasks'/'Hsmoke').mkdir(parents=True)
    (root/'tasks'/'Hsmoke').chmod(0o777)
    (root/'inbox').mkdir()
    (root/'inbox'/'source.txt').write_text('synthetic')
    return root


def test_two_mounts_are_read_only_outside_own_task_and_ask_mind_works():
    root = _workspace()
    control = DockerIPython(root, task_directory=root/'tasks'/'Hsmoke', timeout=30)
    try:
        assert 'synthetic' in control.execute("from pathlib import Path; print(Path('/workspace/inbox/source.txt').read_text())").output
        assert control.execute("from pathlib import Path; Path('out.txt').write_text('done')").ok
        assert not control.execute("from pathlib import Path; Path('/workspace/inbox/forbidden').write_text('bad')").ok
        result = control.execute("ask_mind('Which date?')")
        assert result.ok and result.cognitive_request and 'Which date?' in result.cognitive_request
        assert not (root/'inbox'/'forbidden').exists()
        assert (root/'tasks'/'Hsmoke'/'out.txt').read_text() == 'done'
    finally:
        control.close()


class Scripted:
    identifier = 'scripted'
    tool_contracts = ('ipython(code: str)', 'wait(event_type: str)', 'claim_complete()')
    def __init__(self):
        self.actions = iter([IPythonCode("from pathlib import Path; Path('result.txt').write_text(Path('/workspace/inbox/source.txt').read_text()); Path('.lumina-complete').write_text('done')"),
                             ClaimComplete()])
        self.calls = 0
    def decide(self, request):
        self.calls += 1
        return next(self.actions)


def test_pool_runs_execution_v2_in_docker_and_reports(tmp_path):
    root = _workspace()
    bus = EventBus(tmp_path/'bus.sqlite')
    models = []
    def model_factory(helper, prompt):
        model = Scripted(); models.append(model); return model
    pool = HelperPool(bus, load_config(), workspace=root, model_factory=model_factory)
    contract = {'目标':'复制合成输入', '理由':'做隔离测试', '验收':'输出等于输入', '背景':'无私有数据'}
    bus.publish('spawn','mind.spawn',{'helper':'Hsmoke','contract':contract})
    pool.handle(bus.pending('execution')[0])
    try:
        deadline = time.monotonic()+15
        while pool.get('Hsmoke')['status'] not in ('已完成','失败') and time.monotonic()<deadline:
            time.sleep(.02)
        assert pool.get('Hsmoke')['status'] == '已完成'
        assert (root/'tasks'/'Hsmoke'/'result.txt').read_text() == 'synthetic'
        assert models[0].calls == 2
        assert bus.pending('mind')[0]['kind'] == 'agent.report'
    finally:
        pool.stop()


def test_pool_restarts_real_v2_wait_and_delivers_mind_reply(tmp_path):
    root = _workspace()
    path = tmp_path/'bus.sqlite'
    contract = {'目标':'等日期口径后写明选择', '理由':'验证重启',
                '验收':'answer.txt 是 event_date', '背景':'合成口径'}
    class First:
        identifier='scripted';tool_contracts=('ipython(code: str)','wait(event_type: str)','claim_complete()')
        def __init__(self):self.actions=iter([IPythonCode("ask_mind('Which date?')"),Wait('MIND_REPLY')])
        def decide(self,request):return next(self.actions)
    bus=EventBus(path)
    first=HelperPool(bus,load_config(),workspace=root,model_factory=lambda *_:First())
    bus.publish('spawn','mind.spawn',{'helper':'Hsmoke','contract':contract})
    first.handle(bus.pending('execution')[0])
    try:
        deadline=time.monotonic()+15
        while not bus.pending('mind') and time.monotonic()<deadline:time.sleep(.02)
        assert [event['kind'] for event in bus.pending('mind')]==['agent.question']
    finally:first.stop()
    class Second:
        identifier='scripted';tool_contracts=First.tool_contracts
        def __init__(self):
            self.actions=iter([IPythonCode("from pathlib import Path; Path('answer.txt').write_text('event_date'); Path('.lumina-complete').write_text('done')"),ClaimComplete()])
            self.checked=False
        def decide(self,request):
            if not self.checked:
                assert 'event_date' in request.context
                self.checked=True
            return next(self.actions)
    reopened=EventBus(path)
    second=HelperPool(reopened,load_config(),workspace=root,model_factory=lambda *_:Second())
    second.start()
    try:
        reopened.publish('reply','mind.reply',{'helper':'Hsmoke','content':'event_date'})
        second.handle(reopened.pending('execution')[0])
        deadline=time.monotonic()+15
        while second.get('Hsmoke')['status'] not in ('已完成','失败') and time.monotonic()<deadline:
            time.sleep(.02)
        assert second.get('Hsmoke')['status']=='已完成'
        assert (root/'tasks'/'Hsmoke'/'answer.txt').read_text()=='event_date'
    finally:second.stop()
