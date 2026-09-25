"""Frozen v1 parameters and one-variable-at-a-time stage switches."""
from __future__ import annotations

from dataclasses import dataclass, replace
from math import log

TAU = -0.5 * log(21)  # one salience-1 write has pi=.5 after 21 days
THETA = (-0.5 * log(60) - TAU) / log(1 / 9)  # pi=.1 after 60 days


@dataclass(frozen=True)
class Config:
    preset: str = "P6"
    d: float = 0.5
    min_interval_hours: float = 1.0
    tau: float = TAU
    theta: float = THETA
    pi_min: float = 0.05
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
    llm_model: str = "deepseek-v4-pro"
    dream_max_tokens: int = 4096
    temperature: float = 0.0
    dream_enabled: bool = True
    raw_enabled: bool = True
    recall_strengthening: bool = True
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
    cfg = Config(preset=name, c_sem={"bge-m3": .60, "minilm": .50, "hash": .30}.get(embedder, .60),
                 dup_cos=.85 if embedder == "hash" else .90)
    if name not in {"B0", "B1", "P1", "P2", "P2g", "P3", "P4", "P5", "P6"}:
        raise ValueError(f"unknown preset: {name}")
    if name in {"B0", "B1"}:
        return replace(cfg, dream_enabled=False, raw_enabled=name == "B1",
                       recall_strengthening=False, semantic_channel=False,
                       time_channel=False, entity_channel=False, diffusion=False,
                       semantic_layer=False, time_layer=False, entity_layer=False,
                       event_layer=False, cooccur=False, corecall=False,
                       salience_core=False, remote_slot=False)
    order = ["P1", "P2", "P2g", "P3", "P4", "P5", "P6"]
    i = order.index(name)
    return replace(cfg, time_channel=i >= 3, entity_channel=i >= 4,
                   diffusion=i >= 2, semantic_layer=i >= 2,
                   time_layer=i >= 3, entity_layer=i >= 4,
                   event_layer=i >= 5, relate_edges=i >= 5,
                   merge_edges=i >= 5, cooccur=i >= 6, corecall=i >= 6,
                   salience_core=i >= 1, remote_slot=i >= 6)
