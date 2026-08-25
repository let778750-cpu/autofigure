"""Route-neutral reference oracle: one frozen evaluation truth per reference hash.

同一冻结参考图（按 SHA-256 识别）在所有输入路线间共享同一份 closed-world
inventory 真值。oracle 存放于路线无关目录
``examples/oracles/<reference_sha256 前 16 位>/oracle.json``；该目录不是案例
（无 run.json），cases 扫描自然忽略。

首次 freeze 某参考图时创建 oracle；此后同参考图的 freeze 必须复现同一真值，
不一致即 fail-closed 拒绝（``oracle:inventory-mismatch``）。真值重授权是人工
动作：删除对应 ``oracle.json`` 后重新 freeze；工具不提供自动覆盖。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from tools import common
from tools.contracts import ContractError, read_json, write_json
from tools.reference_inventory import canonical_sha256

SCHEMA_VERSION = "1.0.0"
ORACLE_KIND = "reference_oracle"
# Freeze 状态字段（status/frozen_at）记录冻结动作本身而非参考图真值，
# 进入 oracle 前必须剥离，否则同一真值在不同时间冻结会得到不同哈希。
VOLATILE_INVENTORY_KEYS = ("status", "frozen_at")


def oracle_path(run: common.Run) -> Path:
    """Return the route-neutral oracle path for a case's frozen reference."""

    return common.oracle_path_for(
        common.cases_root_for(run), run.load_meta()["source_sha256"]
    )


def oracle_inventory_payload(inventory: dict[str, Any]) -> dict[str, Any]:
    """Return the truth-bearing inventory payload without freeze-state fields."""

    payload = copy.deepcopy(inventory)
    for key in VOLATILE_INVENTORY_KEYS:
        payload.pop(key, None)
    return payload


def oracle_sha256(oracle: dict[str, Any]) -> str:
    """Canonical digest of the oracle document excluding the digest field itself."""

    return canonical_sha256(
        {key: value for key, value in oracle.items() if key != "oracle_sha256"}
    )


def build_oracle(reference_sha256: str, inventory: dict[str, Any]) -> dict[str, Any]:
    """Create the oracle document binding one reference hash to one inventory truth."""

    oracle: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": ORACLE_KIND,
        "reference_sha256": reference_sha256,
        "inventory": oracle_inventory_payload(inventory),
    }
    oracle["oracle_sha256"] = oracle_sha256(oracle)
    return oracle


def load_oracle(path: Path) -> dict[str, Any]:
    """Read and self-verify an oracle document; raise ContractError on any drift."""

    oracle = read_json(path)
    if (
        oracle.get("schema_version") != SCHEMA_VERSION
        or oracle.get("kind") != ORACLE_KIND
        or not isinstance(oracle.get("reference_sha256"), str)
        or not isinstance(oracle.get("inventory"), dict)
        or oracle.get("oracle_sha256") != oracle_sha256(oracle)
    ):
        raise ContractError(f"invalid reference oracle: {path}")
    return oracle


def write_oracle(path: Path, oracle: dict[str, Any]) -> None:
    """Atomically persist an oracle document."""

    write_json(path, oracle)


def oracle_matches(oracle: dict[str, Any], inventory: dict[str, Any]) -> bool:
    """Return whether a case inventory carries exactly the oracle's frozen truth."""

    return canonical_sha256(oracle["inventory"]) == canonical_sha256(
        oracle_inventory_payload(inventory)
    )


def main() -> int:
    import sys

    sys.stdout.write("tools.reference_oracle 是库模块；oracle 由 autofigure freeze 创建与校验。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
