"""Cold keeps original ordered turns while Memory uses a derived index."""
from datetime import datetime, timezone

from core.cold_draft_store import ColdDraftStore
from core.memory_adapter import draft_turns_to_memory


def test_cold_turns_include_consumed_segments_without_rewriting_text(tmp_path):
    path=tmp_path/'cold.jsonl'
    owner=ColdDraftStore(path)
    owner.append_segment([{'role':'user','text':'原文 A'},
                          {'role':'assistant','text':'原文 B'}],segment_id='legacy')
    before=path.read_bytes()
    assert [t.text for t in owner.list_all_turns()]==['原文 A','原文 B']
    assert path.read_bytes()==before
    assert owner.mark_consumed('legacy')
    assert [t.text for t in ColdDraftStore(path).list_all_turns()]==['原文 A','原文 B']


def test_untimed_legacy_turns_are_skipped_for_dream_input(tmp_path):
    owner=ColdDraftStore(tmp_path/'cold.jsonl')
    owner.append_segment([{'role':'user','text':'旧无时间'},
                          {'role':'assistant','text':'旧回答'}],segment_id='legacy')
    turns=draft_turns_to_memory(owner.list_all_turns(),
        datetime(2026,8,1,tzinfo=timezone.utc),skip_untimed=True)
    assert turns==[]
