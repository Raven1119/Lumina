"""Watch lifecycle at the caller-owned atomic attention boundary."""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from Nervous.watch import applicable, apply_watches, poll_watches, signal_disposition, valid_matches


NOW = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
INTENTIONS = {'research': {'id': 'research', 'revision': 1, 'commitment': 'committed'}}


def watch(identity='observe', revision=1, *, spec='source.changed',
          status='active', intention='research', **params):
    return {'id': identity, 'revision': revision, 'spec_id': spec, 'spec_version': 1,
            'params': params or {'file': 'measurements.json'}, 'status': status,
            'intention_id': intention, 'reason': 'Reconsider when the source changes.',
            'origin_refs': ['owner:request-1']}


def observation(ref):
    return {'file': 'measurements.json', 'ref': ref, 'kind': 'observed_text',
            'chars': 1 if ref else 0, 'missing': ref is None}


def test_observed_a_b_a_are_distinct_and_quiet_restart_does_not_repeat():
    state = {}
    observed = observation(None)
    sample = lambda relative: copy.deepcopy(observed)
    apply_watches(state, [watch()], INTENTIONS, sample, NOW)
    assert poll_watches(state, sample, NOW) == []

    events = []
    for ref in ['evidence:A', 'evidence:B', 'evidence:A']:
        observed = observation(ref)
        emitted = poll_watches(state, sample, NOW)
        assert len(emitted) == 1
        assert emitted[0]['after']['ref'] == ref
        assert applicable(state, emitted[0])
        events.extend(emitted)
        state = json.loads(json.dumps(state))  # the caller's persisted state survives restart
        assert poll_watches(state, sample, NOW) == []
    assert len({event['occurrence_id'] for event in events}) == 3
    assert events[0]['before']['missing'] is True
    assert events[2]['before']['ref'] == 'evidence:B'


def test_overdue_review_is_one_opportunity_until_explicitly_rescheduled():
    state = {}
    due_at = '2026-09-10T11:00:00Z'
    sample = lambda relative: pytest.fail('A due watch does not inspect the environment')
    initial = watch(spec='review.due', intention=None, due_at=due_at)
    apply_watches(state, [initial], {}, sample, NOW)
    assert poll_watches(state, sample, NOW) == []
    late = NOW + timedelta(hours=2)
    event, = poll_watches(state, sample, late)
    assert event['due_at'] == due_at
    assert event['observed_at'] == '2026-09-10T12:00:00+00:00'
    state = json.loads(json.dumps(state))
    assert applicable(state, event)  # original event stays deliverable while Mind is busy
    assert poll_watches(state, sample, late) == []
    apply_watches(state, [initial], {}, sample, late)
    assert poll_watches(state, sample, late) == []

    cancelled = watch(revision=2, spec='review.due', intention=None,
                      status='cancelled', due_at=due_at)
    apply_watches(state, [cancelled], {}, sample, late)
    assert not applicable(state, event)
    assert poll_watches(state, sample, late) == []
    rescheduled = watch(revision=3, spec='review.due', intention=None,
                        due_at='2026-09-10T13:00:00Z')
    apply_watches(state, [rescheduled], {}, sample, late)
    replacement, = poll_watches(state, sample, NOW + timedelta(hours=3))
    assert replacement['occurrence_id'] != event['occurrence_id']
    assert replacement['watch_matches'][0]['watch_revision'] == 3
    assert [v['watch']['revision'] for v in state['watches']['observe']['history']] == [1, 2]


def test_same_change_has_multiple_reasons_and_stale_reason_is_filtered():
    state, samples = {}, []
    observed = observation('evidence:A')

    def sample(relative):
        samples.append(relative)
        return observed

    apply_watches(state, [watch('first'), watch('second')], INTENTIONS, sample, NOW)
    assert samples == ['measurements.json']
    observed = observation('evidence:B')
    samples.clear()
    event, = poll_watches(state, sample, NOW)
    assert samples == ['measurements.json']
    assert [m['watch_id'] for m in event['watch_matches']] == ['first', 'second']
    original = copy.deepcopy(event)
    apply_watches(state, [watch('first', revision=2, status='cancelled')],
                  INTENTIONS, sample, NOW)
    assert applicable(state, event)
    assert [m['watch_id'] for m in valid_matches(state, event)] == ['second']
    assert event == original
    apply_watches(state, [watch('second', revision=2, status='cancelled')],
                  INTENTIONS, sample, NOW)
    assert not applicable(state, event)


def test_watch_registration_retry_does_not_replace_the_original_baseline():
    state = {}
    observed = observation('evidence:A')
    sample = lambda relative: observed
    initial = watch()
    apply_watches(state, [initial], INTENTIONS, sample, NOW)
    observed = observation('evidence:B')
    apply_watches(state, [initial], INTENTIONS,
                  lambda relative: pytest.fail('Effect replay must not resample'), NOW)
    event, = poll_watches(state, sample, NOW)
    assert event['before']['ref'] == 'evidence:A'
    saved = copy.deepcopy(state)
    with pytest.raises(ValueError, match='watch_revision_conflict'):
        apply_watches(state, [{**initial, 'reason': 'Changed without a new revision'}],
                      INTENTIONS, sample, NOW)
    assert state == saved
    with pytest.raises(ValueError, match='stale_watch_revision'):
        apply_watches(state, [watch(revision=3)], INTENTIONS, sample, NOW)
    assert state == saved


def test_pause_defers_existing_obligation_and_close_retires_subscription():
    state = {}
    observed = observation('evidence:A')
    sample = lambda relative: observed
    apply_watches(state, [watch()], INTENTIONS, sample, NOW)
    observed = observation('evidence:B')
    event, = poll_watches(state, sample, NOW)
    paused = {'research': {**INTENTIONS['research'], 'commitment': 'paused', 'revision': 2}}
    apply_watches(state, [], paused, sample, NOW)
    assert not applicable(state, event)
    assert signal_disposition(state, event) == 'deferred'
    assert poll_watches(state, lambda relative: pytest.fail('Paused watches do not poll'), NOW) == []
    resumed = {'research': {**INTENTIONS['research'], 'revision': 3}}
    apply_watches(state, [], resumed, sample, NOW)
    assert applicable(state, event)
    assert signal_disposition(state, event) == 'ready'
    assert poll_watches(state, sample, NOW) == []
    closed = {'research': {**INTENTIONS['research'], 'commitment': 'closed', 'revision': 4}}
    apply_watches(state, [], closed, sample, NOW)
    assert not applicable(state, event)
    assert signal_disposition(state, event) == 'obsolete'
    assert poll_watches(state, sample, NOW) == []
    apply_watches(state, [], resumed, sample, NOW)
    assert not applicable(state, event)  # recommitting alone does not resurrect a closed watch
    apply_watches(state, [watch(revision=2)], resumed, sample, NOW)
    assert poll_watches(state, sample, NOW) == []


def test_same_time_reviews_and_different_source_baselines_are_not_merged():
    state = {}
    observed = observation('evidence:A')
    sample = lambda relative: observed
    apply_watches(state, [watch('first')], INTENTIONS, sample, NOW)
    observed = observation('evidence:B')
    apply_watches(state, [watch('second')], INTENTIONS, sample, NOW)
    observed = observation('evidence:C')
    events = poll_watches(state, sample, NOW)
    assert len(events) == 2
    assert {event['before']['ref'] for event in events} == {'evidence:A', 'evidence:B'}
    apply_watches(state, [watch('due-1', spec='review.due', due_at=NOW.isoformat()),
                          watch('due-2', spec='review.due', due_at=NOW.isoformat())],
                  INTENTIONS, sample, NOW)
    events = poll_watches(state, sample, NOW)
    assert len(events) == 2
    assert len({event['occurrence_id'] for event in events}) == 2


def test_interrupted_caller_transaction_repeats_identity_without_partial_state():
    state = {}
    observed = observation('evidence:A')
    sample = lambda relative: observed
    apply_watches(state, [watch()], INTENTIONS, sample, NOW)
    persisted = json.loads(json.dumps(state))
    observed = observation('evidence:B')
    interrupted, = poll_watches(state, sample, NOW)
    # Neither events nor modified state were saved by the interrupted caller.
    recovered, = poll_watches(persisted, sample, NOW + timedelta(minutes=1))
    assert recovered['occurrence_id'] == interrupted['occurrence_id']
    assert recovered['observed_at'] != interrupted['observed_at']
    assert poll_watches(persisted, sample, NOW) == []


def test_rejected_update_bundle_is_atomic_and_capacity_does_not_drop_old_watches():
    state = {}
    sample = lambda relative: observation(None)
    apply_watches(state, [watch()], INTENTIONS, sample, NOW)
    saved = copy.deepcopy(state)
    with pytest.raises(ValueError, match='stale_watch_revision'):
        apply_watches(state, [watch('new'), watch(revision=4)], INTENTIONS, sample, NOW)
    assert state == saved
    with pytest.raises(ValueError, match='watch_capacity_exhausted'):
        apply_watches(state, [watch(f'extra-{i}') for i in range(32)],
                      INTENTIONS, sample, NOW)
    assert state == saved
