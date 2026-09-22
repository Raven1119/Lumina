"""Group legal visited Facts into verified bodies before output competition."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from math import ceil, isfinite

from .models import AssociativeMemoryContext, AssociativeSelection, BodyMemoryEvidence, MemoryContext, MemoryEvidence, SourceMemoryContext, SourceProvenance
from ._reliable_recall import DIRECT_SHARE
from Conversation_Memory.recall.rendering import render_reliable_fact


@dataclass(frozen=True)
class BodyRecallPolicy:
    # Explicit ablations share the production whole-question seed search.
    propagate: bool = True
    return_bodies: bool = True
    max_payload_blocks: int = 16
    max_payload_bytes: int = 262144
    max_visible_blocks: int = 3
    max_visible_bytes: int = 20000

    def __post_init__(self):
        if type(self.propagate) is not bool or type(self.return_bodies) is not bool:
            raise ValueError("body_policy_invalid")
        for name in ("max_payload_blocks", "max_payload_bytes", "max_visible_blocks", "max_visible_bytes"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError("body_policy_invalid")
        if self.max_payload_blocks > 64 or self.max_payload_bytes > 1048576:
            raise ValueError("body_policy_invalid")


def recall_bodies(adapter, query, policy):
    from ._associative_recall import activate
    from ._reliable_recall import pack_reliable
    config = adapter.body_recall_policy
    policy = replace(policy, max_bytes=min(policy.max_bytes or config.max_visible_bytes, config.max_visible_bytes))
    activation = activate(adapter, query, seed_only=not config.propagate)
    adapter._last_first_hit_diagnostics = dict(activation.diagnostics)
    if not config.return_bodies:
        result = pack_reliable(adapter, query, policy, activation, include_sources=False, profile="reliable-v2")
        adapter._last_body_recall_diagnostics = {"representation": "fact", "payload_reads": 0,
            "payload_bytes": 0, "visible_units": len(result.facts.evidence), "propagate": config.propagate}
        return result
    return pack_bodies(adapter, query, policy, activation)


def pack_bodies(adapter, query, policy, activation):
    config = adapter.body_recall_policy
    diagnostics = {"representation": "verified_paraphrase", "propagate": config.propagate,
        "payload_reads": 0, "payload_bytes": 0, "payload_errors": {}, "groups": {},
        "unit_routes": {}, "protected_direct": [], "provider_requests": 0, "bge_pairs": 0,
        "relation_filter_applied": False, "relation_surfaces_requested": policy.relation_surfaces}
    legal, metadata, masses = {}, {}, {}
    for candidate, h, _attention in activation.candidates:
        try:
            eid = candidate.metadata["evidence_id"]
            provenance = SourceProvenance(**candidate.metadata["provenance"])
            if (not isinstance(eid, str) or not eid or not candidate.text.strip() or not isfinite(h) or h <= 0
                    or any(not isinstance(v, str) or not v.strip() for v in asdict(provenance).values())):
                continue
            legal[eid] = MemoryEvidence(eid, candidate.text, candidate.timestamp, provenance)
            metadata[eid], masses[eid] = candidate.metadata, h
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    seeds = [eid for eid in activation.seed_fact_ids if eid in legal]
    # Fetch only IDs referenced by bounded visited Facts. Seed payloads get I/O
    # priority; no all-payload scan, checkpoint read, or extra vector lookup.
    ordered = list(dict.fromkeys([*seeds, *sorted(legal, key=lambda x: (-masses[x], x))]))
    loaded = {}
    for eid in ordered:
        ref = metadata[eid].get("body_ref")
        if not isinstance(ref, str) or ref in loaded:
            continue
        remaining = config.max_payload_bytes - diagnostics["payload_bytes"]
        if diagnostics["payload_reads"] >= config.max_payload_blocks or remaining <= 0:
            loaded[ref] = None; diagnostics["payload_errors"][ref] = "body_read_budget"
            continue
        diagnostics["payload_reads"] += 1
        try:
            result = adapter.backend.read_memory_body(ref, max_bytes=remaining)
            diagnostics["payload_bytes"] += result.bytes_read
            loaded[ref] = result.payload
            if result.error:
                diagnostics["payload_errors"][ref] = result.error
        except Exception:
            loaded[ref] = None; diagnostics["payload_errors"][ref] = "body_unavailable"
    groups, owner, items = {}, {}, dict(legal)
    for eid in ordered:
        meta = metadata[eid]; ref = meta.get("body_ref")
        body = loaded.get(ref) if isinstance(ref, str) else None
        receipts = meta.get("formation_receipts")
        slot = next((s for s in body["slots"] if s.get("unit_id") == eid), None) if body else None
        valid = bool(body and slot and meta.get("body_unit_ref") == eid
                     and meta.get("body_digest") == body["body_digest"] and slot["text"] == legal[eid].text
                     and slot["provenance"] == asdict(legal[eid].provenance)
                     and isinstance(receipts, dict)
                     and all(receipts.get(k) == v for k, v in body["receipts"].items()))
        if valid:
            # A second visited node cannot supply a conflicting canonical view
            # for a sibling silently overwritten by body expansion.
            valid = all(s.get("unit_id") not in legal or
                        (s["text"] == legal[s["unit_id"]].text and
                         s["provenance"] == asdict(legal[s["unit_id"]].provenance))
                        for s in body["slots"] if s["status"] == "supported")
        if valid:
            key = ref
            if key not in groups:
                units = [s for s in body["slots"] if s["status"] == "supported"]
                for unit in units:
                    p = SourceProvenance(**unit["provenance"])
                    items[unit["unit_id"]] = BodyMemoryEvidence(unit["unit_id"], unit["text"], p.source_timestamp, p,
                        key, unit["unit_id"], tuple(unit["used_source_ids"]))
                groups[key] = {"units": [s["unit_id"] for s in units], "body": body, "triggers": [], "score": 0.0}
        else:
            key = "fact:" + eid
            groups[key] = {"units": [eid], "body": None, "triggers": [], "score": 0.0}
            if ref is not None:
                diagnostics["payload_errors"].setdefault(ref if isinstance(ref, str) else "invalid:" + eid, "body_binding_mismatch")
        group = groups[key]
        group["triggers"].append(eid)
        group["score"] = max(group["score"], masses[eid])
        owner[eid] = key
    # Assign siblings only after validating an actual trigger. A corrupt node's
    # fallback cannot lend its mass or direct status to an unrelated body.
    for key, group in groups.items():
        group["direct"] = any(eid in seeds for eid in group["triggers"])
        diagnostics["groups"][key] = {k: group[k] for k in ("triggers", "score", "direct", "units")}
    selected, selected_groups = [], []
    selected_ids = set()

    def rendered(ids, group_order):
        chunks, blocks = [], []
        for key in group_order:
            group = groups[key]; wanted = [eid for eid in group["units"] if eid in ids and eid not in chunks]
            if not wanted:
                continue
            body = group["body"]
            if body:
                complete = body["complete"] and len(wanted) == len(group["units"])
                label = "已核验转述正文" if complete else "已核验转述片段，非完整经过"
                gaps = [str(s["position"]+1) for s in body["slots"] if s["status"] != "supported"]
                header = f"[B{len(blocks)+1} {label}" + ("; 缺口=" + ",".join(gaps) if gaps else "") + "]"
            else:
                header = ""
            unit_blocks = []
            for eid in wanted:
                item = items[eid]
                block = render_reliable_fact(item, f"M{len(chunks)+1}", include_source_context=policy.include_source_context)
                chunks.append(eid); unit_blocks.append(block)
            blocks.append("\n".join([*([header] if header else []), *unit_blocks]))
        return "\n".join(blocks), chunks

    def try_add(key, wanted, *, caps, used, protected=False):
        missing = [eid for eid in wanted if eid not in selected_ids]
        if not missing:
            return True
        order = [*selected_groups, *([] if key in selected_groups else [key])]
        if groups[key]["body"]:
            covered = set(groups[key]["units"])
            replaced = [other for other in order if other != key and not groups[other]["body"]
                        and set(groups[other]["units"]) <= covered]
            if replaced:
                # Upgrade earlier bare anchors into this one parent without
                # consuming a second block or losing their protected IDs.
                first = min(order.index(other) for other in replaced + [key])
                order = [other for other in order if other not in replaced and other != key]
                order.insert(first, key)
        if len(order) > config.max_visible_blocks:
            return False
        proposed, _ = rendered(selected_ids | set(missing), order)
        before, _ = rendered(selected_ids, selected_groups)
        chars, size = len(proposed), len(proposed.encode("utf-8"))
        delta = (len(missing), max(0, chars-len(before)), max(0, size-len(before.encode("utf-8"))))
        if (len(selected_ids)+len(missing) > policy.max_evidence_items or chars > policy.max_chars or size > policy.max_bytes
                or any(used[i]+delta[i] > caps[i] for i in range(3))):
            # A presentation header must not strand a canonical Fact which
            # itself fits. Keep its complete text and provenance, never trim.
            if groups[key]["body"] and key not in selected_groups and len(missing) == 1:
                eid = missing[0]; fallback = "fact:" + eid
                if fallback not in groups:
                    groups[fallback] = {"units": [eid], "body": None, "triggers": [eid] if eid in legal else [],
                        "score": masses.get(eid, groups[key]["score"]), "direct": eid in seeds}
                    diagnostics["groups"][fallback] = {k: groups[fallback][k] for k in ("triggers", "score", "direct", "units")}
                return try_add(fallback, [eid], caps=caps, used=used, protected=protected)
            return False
        selected_groups[:] = order; selected.extend(missing); selected_ids.update(missing)
        for i in range(3):
            used[i] += delta[i]
        if protected:
            diagnostics["protected_direct"].extend(missing)
        return True

    total = (policy.max_evidence_items, policy.max_chars, policy.max_bytes)
    reserved = tuple(ceil(DIRECT_SHARE * value) for value in total)
    used = [0, 0, 0]
    # Protect canonical direct anchors before expanding a parent with siblings.
    for eid in seeds:
        try_add(owner[eid], [eid], caps=reserved, used=used, protected=True)
    direct = sorted((key for key, g in groups.items() if g["direct"]), key=lambda key: (-groups[key]["score"], key))
    associated = sorted((key for key, g in groups.items() if not g["direct"]), key=lambda key: (-groups[key]["score"], key))

    def take(pool, caps):
        used = [0, 0, 0]
        for key in pool:
            group = groups[key]
            if try_add(key, group["units"], caps=caps, used=used):
                continue
            # Complete units only, prioritizing triggering anchors; retain body
            # source order when displaying the selected subset.
            anchors = sorted(group["triggers"], key=lambda eid: (-masses[eid], eid))
            for eid in dict.fromkeys([*anchors, *group["units"]]):
                try_add(key, [eid], caps=caps, used=used)
    take(associated, tuple(total[i]-reserved[i] for i in range(3)))
    take(direct, total)
    take(associated, total)
    text, visible = rendered(selected_ids, selected_groups)
    for key in selected_groups:
        group = groups[key]
        for eid in group["units"]:
            if eid in selected_ids and eid not in diagnostics["unit_routes"]:
                diagnostics["unit_routes"][eid] = {
                    "body_ref": group["body"]["body_ref"] if group["body"] else None,
                    "route": "direct" if eid in seeds else "graph" if eid in group["triggers"] else "body_expansion",
                    "source_turn_ids": next((s["used_source_ids"] for s in group["body"]["slots"] if s.get("unit_id") == eid), []) if group["body"] else [],
                }
    omitted = set(items) - selected_ids
    partial = bool(omitted or diagnostics["payload_errors"] or activation.diagnostics.get("budget_exhausted")
                   or any(g["body"] and not g["body"]["complete"] for g in groups.values()))
    error = activation.safe_error_code or ("body_payload_partial" if diagnostics["payload_errors"] else None)
    diagnostics.update(visible_units=len(visible), visible_blocks=len(selected_groups), visible_chars=len(text),
                       visible_bytes=len(text.encode("utf-8")), truncated=partial)
    adapter._last_body_recall_diagnostics = diagnostics
    facts = MemoryContext(query, tuple(items[eid] for eid in visible), text, partial, error)
    selections = tuple(AssociativeSelection(eid, "direct" if eid in seeds else "associated", "verified_paraphrase"
                       if diagnostics["unit_routes"][eid]["body_ref"] else "fact") for eid in visible)
    return AssociativeMemoryContext(facts, SourceMemoryContext(query), text, partial, error, selections)
