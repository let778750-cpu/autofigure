"""Reference oracle: per-case copy of the one frozen evaluation truth. 

同一冻结参考图（按 SHA-256 识别）在所有输入路线间共享同一份 closed-world
inventory 真值。oracle 以**案例内副本**形态存放于各案例的
``qa/reference-oracle.json``（机器证据统一进 qa/ 的既有约定）；跨路线一致性
由两级 fail-closed 强制保证，而不是靠共享文件：

1. **freeze 时对等校验**：freeze 一个案例时，其 inventory 必须与同参考图其他
   案例已有的 oracle 副本逐字节一致（``oracle:peer-inventory-mismatch``），
   否则拒绝冻结；
2. **cases --check 巡检**：同一参考图的全部现存 oracle 副本必须一致
   （``oracle-divergence``），不一致即案例合同检查失败。

真值重授权是人工动作：删除**该参考图全部案例**的 oracle 副本后逐案例重新
freeze（只删一份会立刻被对等校验拦下）。工具不提供自动覆盖。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from tools.core import common
from tools.core.contracts import ContractError, read_json, write_json
from tools.assets.reference_inventory import canonical_sha256

SCHEMA_VERSION = "1.0.0"
ORACLE_KIND = "reference_oracle"
ORACLE_FILENAME = "reference-oracle.json"
# Freeze 状态字段（status/frozen_at）记录冻结动作本身而非参考图真值，
# 进入 oracle 前必须剥离，否则同一真值在不同时间冻结会得到不同哈希。
VOLATILE_INVENTORY_KEYS = ("status", "frozen_at")


def oracle_path(run: common.Run) -> Path:
    """Return this case's own oracle copy path (qa/ machine evidence)."""

    return run.qa_dir / ORACLE_FILENAME


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


def peer_oracles(run: common.Run) -> dict[str, dict[str, Any] | str]:
    """Return sibling cases' oracle copies for the same frozen reference.

    结果映射 ``<route>/<case> -> oracle dict``；同级案例存在 oracle 副本但内容
    无法自校验时映射为 ``"invalid"``，供 fail-closed 对等校验消费。
    """

    reference_sha256 = run.load_meta()["source_sha256"]
    cases_root = common.cases_root_for(run)
    peers: dict[str, dict[str, Any] | str] = {}
    if not cases_root.is_dir():
        return peers
    for route_dir in sorted(path for path in cases_root.iterdir() if path.is_dir()):
        if route_dir.name in {"oracles", "generated"}:
            continue
        for case_dir in sorted(path for path in route_dir.iterdir() if path.is_dir()):
            if case_dir.resolve() == run.root.resolve():
                continue
            meta_path = case_dir / "run.json"
            if not meta_path.is_file():
                continue
            try:
                meta = read_json(meta_path)
            except Exception:
                continue
            if meta.get("source_sha256") != reference_sha256:
                continue
            oracle_file = case_dir / "qa" / ORACLE_FILENAME
            if not oracle_file.is_file():
                continue
            try:
                peers[f"{route_dir.name}/{case_dir.name}"] = load_oracle(oracle_file)
            except ContractError:
                peers[f"{route_dir.name}/{case_dir.name}"] = "invalid"
    return peers


def main() -> int:
    import sys

    sys.stdout.write("tools.assets.reference_oracle 是库模块；oracle 由 autofigure freeze 创建与校验。\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
