"""Count failed HTTP attempts in the patch's hard cap; preserve unknown usage."""
import sqlite3
import pytest
import organs_b2_patch_real_scenarios as patch


def test_patch_budget_stops_before_attempt_51_and_keeps_unknown_usage(tmp_path,monkeypatch):
    ledger=tmp_path/'budget.sqlite'
    monkeypatch.setattr(patch,'LEDGER',ledger)
    monkeypatch.setattr(patch.base,'LEDGER',ledger)
    monkeypatch.setattr(patch.base,'CAP',patch.CAP)
    budget=patch.Budget('synthetic')
    for index in range(50):
        reserved=budget.reserve('helper','configured-model')
        budget.finish(reserved,'transport_unknown' if index==0 else '200',
                      None if index==0 else {'prompt_tokens':2,'completion_tokens':3})
    with pytest.raises(RuntimeError,match='cap reached'):
        budget.reserve('mind_dialogue','configured-model')
    assert sqlite3.connect(ledger).execute('SELECT COUNT(*) FROM calls').fetchone()[0]==50
    assert budget.summary()==[{'purpose':'helper','model':'configured-model','calls':50,
        'input_tokens':98,'output_tokens':147,'unknown_usage_attempts':1}]


def test_scenarios_use_exact_task_card_user_messages():
    assert patch.R3 == ('请派帮手从 https://example.com/synthetic-data.csv 下载数据，核对行数后把结果放进它的任务目录。'
                       '这个任务确实需要网络；若环境无法联网，就照实回报做不到，别编造文件。')
    assert patch.R1 == ('请派一个帮手把 inbox 里的 a.csv、b.csv、c.csv 三个表合成一张，按 date 从早到晚排序，'
                       '保留 date,name,value 三列，结果放到它的任务目录里。做完告诉我结果文件的位置。')
