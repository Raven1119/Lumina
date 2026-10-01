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
            try:
                self.pool.expire_questions()
            except Exception:
                pass
        events=self.bus.pending('mind')
        if events:
            first=events[0]
            if first['kind']=='user.message':
                self._attempt(first,lambda:self.runner.run(first))
            else:
                group=[event for event in events if event['kind']==first['kind']]
                if len(group)==1:
                    self._attempt(first,lambda:self.runner.run_events([first]))
                else:
                    try:
                        self.runner.run_events(group)
                    except Exception:
                        for event in group:
                            self._attempt(event,lambda event=event:self.runner.run_events([event]))
        if self.pool is not None:
            for event in self.bus.pending('execution'):
                self._attempt(event,lambda event=event:self.pool.handle(event))
        for event in self.bus.pending('dream'):
            if self.bus.get(event['id'].removesuffix(':spoke')+':finished'):
                self._attempt(event,lambda event=event:self._deliver_dream(event))
        return bool(events or (self.pool is not None and self.bus.pending('execution')))

    def _deliver_dream(self,event):
        self.on_spoke(event['body']['response'])
        self.bus.ack(event['id'])

    def _attempt(self,event,operation):
        try:
            operation()
        except Exception as exc:
            limit=self.runner.config['nervous']['event_max_attempts']
            dead=self.bus.event_failed(event,exc,limit)
            if dead and event['kind']=='user.message' and self.bus.get(event['id']+':reply') is None:
                self.bus.put(event['id']+':reply',
                    response('这次没能回应：事件处理失败',kind='error'))

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
                    # A mailbox-wide storage fault must not kill the worker.
                    worked=False
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
