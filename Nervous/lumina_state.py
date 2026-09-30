"""Actual live organ state; no estimated progress or invented personality state."""
import threading

class LuminaState:
    def __init__(self, dream_lock, bus=None):
        self._lock = threading.Lock()
        self._thinking = False
        self._dream_lock = dream_lock
        self._bus = bus
        self._pool = None
        self._focus = ''

    def attach_pool(self, pool):
        self._pool = pool

    def thinking(self, value, focus='在回你的消息'):
        with self._lock:
            self._thinking = bool(value)
            self._focus = focus if value else ''
        self.snapshot()

    def snapshot(self):
        with self._lock:
            thinking = self._thinking
            focus = self._focus
        # Idle concerns action execution; thought and Dream can coexist.
        states = ['空闲']
        if self._dream_lock.locked():
            states.append('做梦')
        helpers = self._pool.visible() if self._pool is not None else []
        executing = any(row['status'] in ('进行中','在等答复') for row in helpers)
        if executing:states.append('执行中')
        if not thinking and executing:
            current = next(row for row in helpers if row['status'] in ('进行中','在等答复'))
            focus = '帮手 '+current['id']+'：'+current['goal']
        value = {'states':states,'focus':focus if thinking or executing else '',
                 'thinking':thinking,'executing':False}
        if self._pool is not None:
            value['executing']=executing
            value['helpers']=[{'id':row['id'],'goal':row['goal'],'status':row['status'],
                               'outputs':row.get('outputs',[]),'question':row.get('question')}
                              for row in helpers]
        if self._bus is not None:
            self._bus.put('lumina',value,state=True)
        return value
