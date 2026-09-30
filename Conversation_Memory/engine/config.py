"""Frozen v1 parameters and one-variable-at-a-time stage switches."""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import log

TAU = -0.5 * log(21)  # one salience-1 write has pi=.5 after 21 days
THETA = (-0.5 * log(60) - TAU) / log(1 / 9)  # pi=.1 after 60 days


@dataclass(frozen=True)
class Config:
    preset: str = "P8"
    d: float = 0.5
    min_interval_hours: float = 1.0
    tau: float = TAU
    theta: float = THETA
    pi_min: float = 0.05
    pi_recall: float = 0.0
    dormant_days: float = 30.0
    w_write: float = 1.0
    w_recall: float = 0.5
    recall_dedup_hours: float = 6.0
    merge_parent_salience_factor: float = 0.5
    alpha_S: float = 1.0
    alpha_T: float = 1.0
    alpha_E: float = 1.0
    semantic_seeds: int = 20
    semantic_temperature: float = 0.05
    time_seed_decay_hours: float = 12.0
    entity_recent_weight: float = 0.3
    w_sem: float = 0.15
    w_time: float = 0.25
    w_ent: float = 0.3
    w_event: float = 0.3
    lambda_: float = 0.5
    steps: int = 3
    k_sem: int = 10
    c_sem: float = 0.6
    sigma_time_hours: float = 6.0
    kappa: float = 3.0
    rho: float = 0.5
    beta: float = 0.3
    event_decay_days: float = 60.0
    epsilon: float = 0.02
    degree_cap: int = 32
    near_cap: int = 6
    core_cap: int = 4
    dup_cos: float = 0.9
    remote_ratio: float = 0.5
    raw_window_days: float = 14.0
    raw_topk: int = 6
    raw_interval_cap: int = 16
    cue_recent_turns: int = 2
    cue_recent_weight: float = 0.3
    cue_time_weight: float = 0.2
    dream_trigger_turns: int = 40
    dream_window_max_turns: int = 40
    remind_cap: int = 30
    remind_chunk_turns: int = 6
    remind_per_chunk: int = 8
    remind_dup_cos: float = 0.8
    remind_entity_cap: int = 10
    entities_in_prompt_cap: int = 40
    llm_model: str = "deepseek-flash"
    dream_max_tokens: int = 4096
    temperature: float = 0.0
    dream_enabled: bool = True
    raw_enabled: bool = True
    recall_strengthening: bool = True
    usage_source: str = 'surfaced'
    usage_mode: str = 'dream_call'
    usage_reply_source: str = 'shadow'
    usage_min_overlap: int = 3
    integrate_prompt: str = 'v1'
    render_version: str = 'v1'
    answer_prompt: str = 'v2'
    pattern_enabled: bool = False
    pattern_cooccur_min: float = 0.3
    pattern_cos_low: float = 0.45
    pattern_cos_high: float = 0.80
    pattern_max_groups: int = 6
    pattern_prompt: str = 'v1'
    pattern_candidate_types: tuple[str, ...] = ('cooccur', 'relate', 'similar')
    pattern_similar_exclude_relate: bool = True
    pattern_kind_cos: float = 0.70
    pattern_max_per_type: int = 0
    pattern_recur_max_members: int = 6
    pattern_sameday_max_members: int = 8
    pattern_sameday_max_days: int = 4
    pattern_dup_cos: float = 0.90
    pattern_require_instances: bool = False
    pattern_protect: bool = False
    trace_enabled: bool = False
    pattern_trace_candidates: tuple[str, ...] = ()
    pattern_trace_kind_cos: float = 0.70
    pattern_trace_min_days: int = 3
    answer_parse: str = 'strict'
    usage_short_match: str = 'stoplist'
    usage_quote_check: bool = False
    usage_context_turns: int = 6
    near_pattern_exclude: bool = False
    assoc_rule: str = 'score'
    assoc_pi_min: float = 0.2
    assoc_abs_ratio: float = 0.2
    semantic_channel: bool = True
    time_channel: bool = True
    entity_channel: bool = True
    diffusion: bool = True
    semantic_layer: bool = True
    time_layer: bool = True
    entity_layer: bool = True
    event_layer: bool = True
    relate_edges: bool = True
    merge_edges: bool = True
    cooccur: bool = True
    corecall: bool = True
    salience_core: bool = True
    remote_slot: bool = True


def preset(name: str, embedder: str = "bge-m3") -> Config:
    if name not in {'B1','P8','P9'}:
        raise ValueError(f"unknown preset: {name}")
    cfg = Config(preset=name, pi_recall=0.0,
                 c_sem={"bge-m3": .60, "minilm": .50, "hash": .30}.get(embedder, .60),
                 dup_cos=.85 if embedder == "hash" else .90)
    if name == 'B1':
        return replace(cfg, dream_enabled=False, raw_enabled=True,
                       recall_strengthening=False, semantic_channel=False,
                       time_channel=False, entity_channel=False, diffusion=False,
                       semantic_layer=False, time_layer=False, entity_layer=False,
                       event_layer=False, cooccur=False, corecall=False,
                       salience_core=False, remote_slot=False)
    cfg=replace(cfg, time_channel=True, entity_channel=True,
                   diffusion=True, semantic_layer=True,
                   time_layer=True, entity_layer=True,
                   event_layer=True, relate_edges=True,
                   merge_edges=True, cooccur=True, corecall=True,
                   salience_core=True, remote_slot=True)
    if name in ("P8", "P9"):
        scale=cfg.c_sem/.60
        cfg=replace(cfg,pattern_enabled=True,
                       pattern_cos_low=min(.999,.45*scale),
                       pattern_cos_high=min(.999,.80*scale),
                       assoc_rule='lift',render_version='v3',answer_prompt='v4',
                       integrate_prompt='v3',usage_source='dream',
                       usage_mode='integrate',usage_reply_source='scripted')
        if name in ('P8','P9'):
            cfg=replace(cfg,pattern_prompt='v2',
                           pattern_candidate_types=('similar','recur','sameday'),
                           pattern_similar_exclude_relate=False,
                           pattern_kind_cos=.75,pattern_max_per_type=2,
                           pattern_require_instances=True,pattern_protect=True,
                           usage_quote_check=True,integrate_prompt='v4',
                           render_version='v4',answer_prompt='v5')
            if name=='P9':
                return replace(cfg,trace_enabled=True,
                               pattern_trace_candidates=('recur','sameday'),
                               pattern_candidate_types=('similar',),
                               pattern_trace_kind_cos=.75,pattern_trace_min_days=3,
                               pattern_prompt='v3',answer_parse='tolerant',
                               usage_short_match='entity')
            return cfg
        return cfg
    raise AssertionError('unreachable')
