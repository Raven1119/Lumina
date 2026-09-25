"""Build deterministic evaluation inputs for offline tests."""

import pytest

from eval_set.build import build_one


@pytest.fixture(scope='session', autouse=True)
def built_eval_sets():
    for name in ('dev_a','dev_b','holdout_c'):
        errors,_,_=build_one(name)
        assert not errors, (name,errors)
