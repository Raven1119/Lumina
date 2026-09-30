"""One serial Mind worker; event dispatch is mechanical and owns no judgment."""
import threading

from core.dialogue_io import response

class DialogueScheduler:
    def __init__(self,bus,runner,language,on_spoke,pool=None):
        self.bus,self.runner,self.language,self.on_spoke=bus,runner,language,on_spoke
        self.pool=pool
        self._stop=threading.Event()
        self._thread=None
        self._start_lock=threading.Lock()

    def emit(self,event_id,kind,body):
        self.bus.publish(event_id,kind,body)
        # The serial worker synchronously services the Language mailbox.
        for event in self.bus.pending('language'):
            self.language.handle(event)
        result=self.bus.get(event_id+':done')
        if result is None:
            raise RuntimeError('language_receipt_missing')
        return result

    def drain_once(self):
        if self.pool is not None:
            self.pool.expire_questions()
        events=self.bus.pending('mind')
        if events:
            first=events[0]
            if first['kind']=='user.message':
                self.runner.run(first)
            else:
                self.runner.run_events([event for event in events if event['kind']==first['kind']])
        if self.pool is not None:
            for event in self.bus.pending('execution'):
                self.pool.handle(event)
        for event in self.bus.pending('dream'):
            if self.bus.get(event['id'].removesuffix(':spoke')+':finished'):
                self.on_spoke(event['body']['response'])
                self.bus.ack(event['id'])
        return bool(events or (self.pool is not None and self.bus.pending('execution')))

    def start(self):
        with self._start_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            if self.pool is not None:
                self.pool.start()
            self._thread=threading.Thread(target=self._work,name='lumina-dialogue',daemon=True)
            self._thread.start()

    def _work(self):
        try:
            while not self._stop.is_set():
                try:
                    worked=self.drain_once()
                except Exception:
                    # Retain the event and journals. Surface a bounded failure,
                    # and retry recovery only after a future wake-up/restart.
                    for event in self.bus.pending('mind')[:1]:
                        self.bus.put(event['id']+':reply',response())
                    self._stop.set()
                    break
                if not worked:
                    with self.bus.changed:
                        self.bus.changed.wait(0.1)
        finally:
            self.bus.close()

    def stop(self):
        self._stop.set()
        self.bus.wake()
        if self.pool is not None:
            self.pool.stop()
        if self._thread:
            self._thread.join() # no detached thought may write after shutdown
        self.bus.close()
