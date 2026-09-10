"""Watch effects and observations inside the existing Nervous transaction.

The caller supplies owner observations and a UTC clock, then persists returned
signals with the mutated attention state. There is no filesystem or model access
here. Repeating a transaction before persistence derives the same occurrence IDs.
"""
import copy
from datetime import datetime, timezone

from Nervous.storage import fingerprint

MAX_WATCHES = 32


def _timestamp(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('watch_clock_requires_timezone')
    return now.astimezone(timezone.utc).isoformat()


def _sample(sample, relative):
    result = copy.deepcopy(sample(relative))
    if (not isinstance(result, dict) or result.get('file') != relative
            or type(result.get('missing')) is not bool
            or (result.get('ref') is not None and not isinstance(result['ref'], str))
            or (result['missing'] != (result.get('ref') is None))):
        raise ValueError('invalid_watch_observation')
    return result


def _lifecycle(record, intentions):
    watch = record['watch']
    if watch['status'] == 'cancelled':
        return 'cancelled'
    if record['lifecycle'] == 'closed':
        return 'closed'
    if watch['intention_id'] is not None:
        intention = intentions.get(watch['intention_id'])
        if intention is None:
            raise ValueError('watch_intention_missing')
        if intention['commitment'] == 'closed':
            return 'closed'
        if intention['commitment'] != 'committed':
            return 'deferred'
    return 'notified' if record['notified_at'] is not None else 'active'


def apply_watches(attention_state, updates, current_intentions, sample, now):
    """Apply accepted versions idempotently; reject partial/conflicting bundles.

    Only Mind's already accepted data enters this seam. Sampling establishes a
    new source subscription's baseline; replay of an applied effect never samples
    again. Closing an Intention retires its subscription, while a pause preserves
    the baseline and outstanding notification for a later committed revision.
    """
    watches = copy.deepcopy(attention_state.get('watches', {}))
    changed = set()
    registered_at = _timestamp(now)
    samples = {}
    for update in updates:
        identity, revision = update['id'], update['revision']
        if identity in changed:
            raise ValueError('duplicate_watch_update')
        changed.add(identity)
        previous = watches.get(identity)
        if previous and revision == previous['watch']['revision']:
            if update != previous['watch']:
                raise ValueError('watch_revision_conflict')
            continue
        if type(revision) is not int or revision != (previous['watch']['revision'] + 1 if previous else 1):
            raise ValueError('stale_watch_revision')
        if (update['spec_id'] not in {'source.changed', 'review.due'}
                or update['spec_version'] != 1
                or update['status'] not in {'active', 'cancelled'}):
            raise ValueError('unsupported_watch_contract')
        baseline = None
        if update['status'] == 'active' and update['spec_id'] == 'source.changed':
            relative = update['params']['file']
            if relative not in samples:
                samples[relative] = _sample(sample, relative)
            baseline = samples[relative]
        elif update['spec_id'] == 'review.due':
            due = datetime.fromisoformat(update['params']['due_at'].replace('Z', '+00:00'))
            if due.tzinfo is None:
                raise ValueError('watch_due_requires_timezone')
        history = previous['history'] + [{key: value for key, value in previous.items()
                                          if key != 'history'}] if previous else []
        watches[identity] = {'watch': copy.deepcopy(update), 'history': history,
            'lifecycle': 'active', 'baseline': copy.deepcopy(baseline),
            'last_observation': copy.deepcopy(baseline), 'sequence': 0,
            'registered_at': registered_at, 'notified_at': None}
    if len(watches) > MAX_WATCHES:
        raise ValueError('watch_capacity_exhausted')
    for record in watches.values():
        record['lifecycle'] = _lifecycle(record, current_intentions)
    attention_state['watches'] = watches


def _match(record):
    watch = record['watch']
    return {'watch_id': watch['id'], 'watch_revision': watch['revision'],
            'sequence': record['sequence'], 'intention_id': watch['intention_id'],
            'reason': watch['reason'], 'origin_refs': list(watch['origin_refs'])}


def poll_watches(attention_state, sample, now):
    """Sample each active source once and create finite occurrence records.

    Watch notifications are not cognitive acknowledgements. The caller retains
    each resulting event pending while Mind is busy. Independent due opportunities
    remain independent even when their planned times are identical.
    """
    observed_at = _timestamp(now)
    watches = copy.deepcopy(attention_state.get('watches', {}))
    samples, grouped, signals = {}, {}, []
    for record in watches.values():
        if record['lifecycle'] != 'active':
            continue
        watch = record['watch']
        if watch['spec_id'] == 'source.changed':
            relative = watch['params']['file']
            if relative not in samples:
                samples[relative] = _sample(sample, relative)
            before, after = record['last_observation'], samples[relative]
            if before['ref'] == after['ref']:
                continue
            record['sequence'] += 1
            record['last_observation'] = copy.deepcopy(after)
            key = (relative, before['ref'], after['ref'])
            signal = grouped.get(key)
            if signal is None:
                signal = {'kind': 'source.changed', 'file': relative,
                          'before': before, 'after': copy.deepcopy(after),
                          'observed_at': observed_at, 'watch_matches': []}
                signals.append(signal)
                grouped[key] = signal
            signal['watch_matches'].append(_match(record))
        else:
            due = datetime.fromisoformat(watch['params']['due_at'].replace('Z', '+00:00'))
            if now < due:
                continue
            record['sequence'] += 1
            record['lifecycle'] = 'notified'
            record['notified_at'] = observed_at
            signals.append({'kind': 'review.due', 'due_at': watch['params']['due_at'],
                            'observed_at': observed_at, 'watch_matches': [_match(record)]})
    for signal in signals:
        # The version and monotonic sequence identify an occurrence. Time and
        # snapshot contents alone would merge A -> B -> A or replay new timers.
        signal['occurrence_id'] = 'watch:' + fingerprint({
            'kind': signal['kind'], 'matches': sorted(
                ((m['watch_id'], m['watch_revision'], m['sequence'])
                 for m in signal['watch_matches']))})[:32]
    attention_state['watches'] = watches
    return signals


def valid_matches(attention_state, signal):
    """Return current applicable reasons; the original event remains immutable."""
    result = []
    for match in signal.get('watch_matches', []):
        record = attention_state.get('watches', {}).get(match['watch_id'])
        if (record and record['watch']['revision'] == match['watch_revision']
                and record['lifecycle'] in {'active', 'notified'}):
            result.append(copy.deepcopy(match))
    return result


def applicable(attention_state, signal):
    return bool(valid_matches(attention_state, signal))


def signal_disposition(attention_state, signal):
    """A paused opportunity remains pending; an obsolete version may be retired."""
    if applicable(attention_state, signal):
        return 'ready'
    for match in signal.get('watch_matches', []):
        record = attention_state.get('watches', {}).get(match['watch_id'])
        if (record and record['watch']['revision'] == match['watch_revision']
                and record['lifecycle'] == 'deferred'):
            return 'deferred'
    return 'obsolete'
