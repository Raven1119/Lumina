"""Small deterministic relation vocabulary used by Recall admission."""

from __future__ import annotations

import unicodedata


UNRESOLVED = None

_ALIASES: dict[str, tuple[str, ...]] = {
    "FILTER_MESH_CLASS": (
        "filter mesh classification", "mesh category",
        "滤网目数等级", "筛网规格",
    ),
    "ROTOR_BALANCE_GRADE": (
        "rotor balance grade", "balance quality grade",
        "转子平衡等级", "平衡品质级别",
    ),
    "CABLE_SHEATH_COMPOUND": (
        "cable sheath compound", "jacket polymer",
        "电缆护套材料", "外被材质",
    ),
    "VALVE_SEAT_COMPOUND": (
        "valve seat compound", "seat material",
        "阀座材质", "密封座材料",
    ),
    "ALARM_CLEARANCE_POLICY": (
        "alarm clearance rule", "acknowledgement behavior",
        "报警清除规则", "确认策略",
    ),
    "SUPPORT_CONTACT_CHANNEL": (
        "support contact route", "service desk route",
        "服务联系渠道", "客服联络方式",
    ),
    "CAMERA_CAPTURE_RATE": (
        "camera capture rate", "frames per second",
        "相机采集速率", "采样帧频",
    ),
    "PORT_LINK_MODE": (
        "network port link mode", "link behavior",
        "网络端口链路模式", "端口工作方式",
    ),
    "RESERVOIR_USABLE_VOLUME": (
        "usable reservoir volume", "working capacity",
        "储液罐可用容积", "有效容量",
    ),
    "SENSOR_INSTALLATION_ANGLE": (
        "sensor installation angle", "orientation angle",
        "传感器安装角度", "装配倾角",
    ),
}


def _normalize(surface: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", surface).casefold().split())


_ALIAS_INDEX = {
    _normalize(alias): canonical_id
    for canonical_id, aliases in _ALIASES.items()
    for alias in aliases
}
assert len(_ALIASES) == 10
assert len(_ALIAS_INDEX) == 40


def _resolve(surface: str, context: str) -> str | None:
    direct = _ALIAS_INDEX.get(_normalize(surface))
    if direct is not None:
        return direct
    normalized_surface = _normalize(surface)
    normalized_context = _normalize(context)
    if "class" in normalized_surface:
        if "rotor" in normalized_context and "balance" in normalized_context:
            return "ROTOR_BALANCE_GRADE"
        if "filter" in normalized_context and "mesh" in normalized_context:
            return "FILTER_MESH_CLASS"
    if "material" in normalized_surface:
        if "valve" in normalized_context and "seat" in normalized_context:
            return "VALVE_SEAT_COMPOUND"
        if "cable" in normalized_context and "sheath" in normalized_context:
            return "CABLE_SHEATH_COMPOUND"
    return UNRESOLVED


class ControlledRelationResolver:
    """Resolve explicitly supplied relation surfaces without guessing."""

    def resolve_query_relations(
        self,
        surfaces: tuple[str, ...],
    ) -> tuple[str | None, ...]:
        return tuple(_resolve(surface, surface) for surface in surfaces)

    def resolve_memory(
        self,
        *,
        relation: str,
        subject: str,
        value: str,
        text: str,
    ) -> str | None:
        return _resolve(relation, f"{subject} {relation} {value} {text}")
