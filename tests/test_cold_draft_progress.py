"""Memory-v1 cursor advances independently of Cold's historical state."""
from datetime import datetime, timezone

from Conversation_Memory.engine.embed import BGE_M3_REVISION, BGE_M3_WEIGHTS_SHA256, HashEmbedder
from Conversation_Memory.facade import MemoryV1
from core.cold_draft_store import ColdDraftStore
from core.memory_adapter import draft_turns_to_memory

AT=datetime(2026,8,1,tzinfo=timezone.utc)


class LocalBGE(HashEmbedder):
    identity={**HashEmbedder.identity,'model':'BAAI/bge-m3',
              'revision':BGE_M3_REVISION,'weights_sha256':BGE_M3_WEIGHTS_SHA256}


class Model:
    model='offline'
    def complete(self,**kwargs):
        class Result:
            text='{"ops":[]}'
            usage={'input_tokens':0,'output_tokens':0}
            cache_key='offline'
            cache_hit=False
        return Result()


def _cold(path):
    owner=ColdDraftStore(path)
    owner.append_segment([
        {'role':'user','text':'一段真实原文','turn_id':'t01','created_at':AT.isoformat(),
         'source_timezone':'UTC','timezone_source':'client'},
        {'role':'assistant','text':'我的回答','turn_id':'t02','created_at':AT.isoformat(),
         'source_timezone':'UTC','timezone_source':'client'}],segment_id='s01')
    return owner


def test_cursor_start_and_end_are_separate_from_cold_state(tmp_path):
    owner=_cold(tmp_path/'cold.jsonl')
    memory=MemoryV1(tmp_path/'memory',embedder_factory=LocalBGE,model_factory=Model)
    turns=draft_turns_to_memory(owner.list_all_turns(),AT,skip_untimed=True)
    assert not memory.has_cold_cursor()
    memory.set_cursor_to_end(turns)
    assert memory.unintegrated_turn_count(turns)==0
    memory.set_cursor_to_start()
    assert memory.unintegrated_turn_count(turns)==2
    assert owner.mark_consumed('s01')
    assert memory.unintegrated_turn_count(turns)==2
    assert memory.dream_once(turns).status=='applied'
    assert memory.unintegrated_turn_count(turns)==0


def test_failed_window_does_not_advance_cursor(tmp_path):
    class Bad(Model):
        def complete(self,**kwargs):
            value=super().complete(**kwargs)
            value.text='invalid'
            return value
    owner=_cold(tmp_path/'cold.jsonl')
    turns=draft_turns_to_memory(owner.list_all_turns(),AT,skip_untimed=True)
    memory=MemoryV1(tmp_path/'memory',embedder_factory=LocalBGE,model_factory=Bad)
    memory.set_cursor_to_start()
    assert memory.dream_once(turns).status=='failed'
    assert memory.unintegrated_turn_count(turns)==2
