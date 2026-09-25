#!/usr/bin/env python3
"""Build and validate the Conversation Memory evaluation set.

Authoring inputs (hand-written, versioned):
  scripts/<id>.txt   dialogue scripts with inline gold tags
  gold/<id>.json     plants (what was planted) and probes (what is asked, when, and what should surface)

Generated outputs (never hand-edited):
  built/<id>.dialogue.json   what the system under test is allowed to see (no tags)
  built/<id>.gold.json       resolved gold for the evaluator (tags -> turn ids, Hot/raw diagnostics)

Usage:
  python build.py            build + validate every set, non-zero exit on any error
  python build.py dev_a      build + validate one set

Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TZ = timezone(timedelta(hours=8))

# Mirrors core/hot_draft_compactor.py defaults: compaction runs when raw turns
# exceed 24 and keeps the most recent 12 (aligned to complete user/assistant pairs).
HOT_MAX_RAW = 24
HOT_RETAIN = 12
RAW_WINDOW_DAYS = 14

CATEGORIES = {
    "理解当下", "时间", "实体", "远联想", "接续", "变化",
    "淡忘", "保留", "自己的看法", "否定", "对照",
}

HEADER = re.compile(r"^## (\S+) (\d{4}-\d{2}-\d{2}) (\d{2}):(\d{2})\s*$")
TURN = re.compile(r"^(U|L):\s*(.+?)\s*$")
TAGS = re.compile(r"\{([^{}]*)\}\s*$")
TAG_NAME = re.compile(r"#([A-Za-z0-9_\-]+)")


class BuildError(Exception):
    pass


def _h(text: str) -> int:
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16)


def parse_time(value: str) -> datetime:
    """Accept 'YYYY-MM-DD HH:MM' in the set's +08:00 timezone."""
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def parse_script(path: Path):
    meta: dict[str, str] = {}
    sessions = []
    current = None
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("@"):
            key, _, value = line[1:].partition(":")
            meta[key.strip()] = value.strip()
            continue
        m = HEADER.match(line)
        if m:
            sid, day, hh, mm = m.groups()
            start = datetime.strptime(f"{day} {hh}:{mm}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
            current = {"id": sid, "start": start, "turns": []}
            sessions.append(current)
            continue
        m = TURN.match(line)
        if not m:
            raise BuildError(f"{path.name}:{lineno}: unrecognised line: {raw!r}")
        if current is None:
            raise BuildError(f"{path.name}:{lineno}: turn before first session header")
        role, body = m.groups()
        tags: list[str] = []
        tm = TAGS.search(body)
        if tm:
            tags = TAG_NAME.findall(tm.group(1))
            body = body[: tm.start()].rstrip()
        if not body:
            raise BuildError(f"{path.name}:{lineno}: empty turn text")
        current["turns"].append({"role": "user" if role == "U" else "assistant",
                                 "text": body, "tags": tags, "line": lineno})
    return meta, sessions


def assign_ids_and_times(set_id: str, sessions):
    errors = []
    seen = set()
    prev_end = None
    for s in sessions:
        if s["id"] in seen:
            errors.append(f"duplicate session id {s['id']}")
        seen.add(s["id"])
        if not s["turns"]:
            errors.append(f"session {s['id']} has no turns")
            continue
        t = s["start"]
        if prev_end is not None and t <= prev_end:
            errors.append(f"session {s['id']} starts before previous session ends")
        for i, turn in enumerate(s["turns"]):
            expected = "user" if i % 2 == 0 else "assistant"
            if turn["role"] != expected:
                errors.append(f"session {s['id']} line {turn['line']}: expected {expected} turn")
            turn["id"] = f"{s['id']}-t{i + 1:02d}"
            if i > 0:
                seed = _h(f"{set_id}/{turn['id']}")
                if turn["role"] == "assistant":
                    t = t + timedelta(seconds=10 + seed % 50)
                else:
                    t = t + timedelta(seconds=30 + seed % 180)
            turn["time"] = t
        if len(s["turns"]) % 2:
            errors.append(f"session {s['id']} ends on a user turn")
        s["end"] = t
        prev_end = t
    return errors


def simulate_hot(all_turns, until: datetime):
    """Turns still in Hot at `until`, under the pair-aligned 24/12 compaction rule."""
    hot = []
    for turn in all_turns:
        if turn["time"] >= until:
            break
        hot.append(turn)
        if turn["role"] == "assistant" and len(hot) > HOT_MAX_RAW:
            desired = len(hot) - HOT_RETAIN
            boundary = desired - (desired % 2)
            hot = hot[boundary:]
    return {t["id"] for t in hot}


def build_one(name: str) -> tuple[list[str], list[str], dict]:
    errors: list[str] = []
    warnings: list[str] = []
    script_path = ROOT / "scripts" / f"{name}.txt"
    gold_path = ROOT / "gold" / f"{name}.json"
    meta, sessions = parse_script(script_path)
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    set_id = meta.get("id", name)
    if gold.get("id") != set_id:
        errors.append(f"gold id {gold.get('id')!r} != script id {set_id!r}")
    errors += assign_ids_and_times(set_id, sessions)

    all_turns = [t for s in sessions for t in s["turns"]]
    by_id = {t["id"]: t for t in all_turns}
    tag_map: dict[str, list[str]] = {}
    for t in all_turns:
        for tag in t["tags"]:
            tag_map.setdefault(tag, []).append(t["id"])

    plant_tags = set()
    for p in gold.get("plants", []):
        for tag in p["tags"]:
            plant_tags.add(tag)
            if tag not in tag_map:
                errors.append(f"plant tag #{tag} never appears in script")
    for tag in sorted(set(tag_map) - plant_tags):
        warnings.append(f"script tag #{tag} is not declared in any plant")

    spans = [(s["start"], s["end"], s["id"]) for s in sessions if s["turns"]]

    def resolve(tags, probe_id, when, field):
        ids = []
        for tag in tags:
            if tag not in tag_map:
                errors.append(f"{probe_id}: {field} references unknown tag #{tag}")
                continue
            before = [i for i in tag_map[tag] if by_id[i]["time"] < when]
            if not before:
                errors.append(f"{probe_id}: {field} tag #{tag} has no turn before probe time")
            ids += before
        return ids

    built_probes = []
    probe_ids = set()
    for p in gold.get("probes", []):
        pid = p["id"]
        if pid in probe_ids:
            errors.append(f"duplicate probe id {pid}")
        probe_ids.add(pid)
        if p["category"] not in CATEGORIES:
            errors.append(f"{pid}: unknown category {p['category']}")
        when = parse_time(p["time"])
        for start, end, sid in spans:
            if start - timedelta(minutes=5) <= when <= end + timedelta(minutes=5):
                errors.append(f"{pid}: probe time falls inside or next to session {sid}")
        hot = simulate_hot(all_turns, when)

        def expectation(block, label):
            must = [resolve(group, pid, when, f"{label}.must_surface") for group in block.get("must_surface", [])]
            must_not = resolve(block.get("must_not_surface", []), pid, when, f"{label}.must_not_surface")
            return must, must_not

        must, must_not = expectation(p, "base")
        hot_groups = [g for g in must if set(g) & hot]
        if must and len(hot_groups) == len(must):
            errors.append(f"{pid}: every must_surface group is still visible in Hot; the probe would not test memory")
        elif hot_groups:
            warnings.append(f"{pid}: {len(hot_groups)}/{len(must)} must_surface groups visible in Hot")
        raw_cutoff = when - timedelta(days=RAW_WINDOW_DAYS)
        raw_gold = sorted({i for g in must for i in g if by_id[i]["time"] >= raw_cutoff and i not in hot})

        variants = []
        for v in p.get("gap_variants", []):
            offset = v["offset_days"]
            vwhen = when + timedelta(days=offset)
            label = f"+{offset}d"
            vmust = [resolve(g, pid, vwhen, f"{label}.must_surface") for g in v.get("must_surface", [])]
            vnot = resolve(v.get("must_not_surface", []), pid, vwhen, f"{label}.must_not_surface")
            variants.append({"offset_days": offset, "time": vwhen.isoformat(),
                             "must_surface": vmust, "must_not_surface": vnot,
                             "must_surface_tags": v.get("must_surface", []),
                             "must_not_surface_tags": v.get("must_not_surface", [])})
        if not p.get("rubric", {}).get("should") and p["category"] != "对照":
            warnings.append(f"{pid}: rubric.should is empty")

        built_probes.append({
            "id": pid,
            "category": p["category"],
            "time": when.isoformat(),
            "message": p["message"],
            "soft": bool(p.get("soft", False)),
            "must_surface": must,
            "must_not_surface": must_not,
            "must_surface_tags": p.get("must_surface", []),
            "must_not_surface_tags": p.get("must_not_surface", []),
            "rubric": p.get("rubric", {}),
            "gap_variants": variants,
            "diagnostics": {
                "hot_visible_gold": sorted({i for g in must for i in g} & hot),
                "raw_window_gold": raw_gold,
            },
            "note": p.get("note", ""),
        })

    dialogue = {
        "id": set_id,
        "split": meta.get("split", ""),
        "timezone": "+08:00",
        "persona_note": meta.get("persona", ""),
        "sessions": [{
            "id": s["id"],
            "start": s["start"].isoformat(),
            "turns": [{"id": t["id"], "role": t["role"], "time": t["time"].isoformat(), "text": t["text"]}
                      for t in s["turns"]],
        } for s in sessions],
    }
    gold_out = {
        "id": set_id,
        "split": meta.get("split", ""),
        "tags": tag_map,
        "plants": gold.get("plants", []),
        "probes": built_probes,
    }
    days = sorted({s["start"].date() for s in sessions})
    stats = {
        "sessions": len(sessions),
        "turns": len(all_turns),
        "user_chars": sum(len(t["text"]) for t in all_turns if t["role"] == "user"),
        "assistant_chars": sum(len(t["text"]) for t in all_turns if t["role"] == "assistant"),
        "span": f"{days[0]} .. {days[-1]}" if days else "",
        "probes": len(built_probes),
        "by_category": {c: sum(1 for p in built_probes if p["category"] == c) for c in sorted(CATEGORIES)},
        "tagged_turns": sum(1 for t in all_turns if t["tags"]),
    }
    if not errors:
        out = ROOT / "built"
        out.mkdir(exist_ok=True)
        (out / f"{name}.dialogue.json").write_text(json.dumps(dialogue, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / f"{name}.gold.json").write_text(json.dumps(gold_out, ensure_ascii=False, indent=1), encoding="utf-8")
    return errors, warnings, stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    args = parser.parse_args()
    names = args.names or sorted(p.stem for p in (ROOT / "scripts").glob("*.txt"))
    failed = False
    for name in names:
        try:
            errors, warnings, stats = build_one(name)
        except BuildError as exc:
            errors, warnings, stats = [str(exc)], [], {}
        print(f"== {name}")
        for key, value in stats.items():
            print(f"   {key}: {value}")
        for w in warnings:
            print(f"   WARN  {w}")
        for e in errors:
            print(f"   ERROR {e}")
        failed |= bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
