"""三个方块的确定性预实验。用 DBCAD 后端的 Python 环境运行，标准输出为 JSON。

用法：python pilot.py --worker /path/to/dbcad_worker [--code-root /path/to/code]
版本保存在临时内存库，几何由真实 worker 产生。全程不调用模型。
"""
import argparse
import json
import math
import sys
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


TARGET_GAP = 2.0
TOLERANCE = 0.01


def near(a, b):
    return math.isclose(a, b, abs_tol=TOLERANCE, rel_tol=0)


def identify(rows):
    # 三种宽度在这个平移实验中不变，使判卷独立于清单顺序和变化后的哈希。
    parts = {}
    for name, width in (("A", 6), ("B", 20), ("C", 4)):
        matches = [r for r in rows if near(r["bbox_max"][0] - r["bbox_min"][0], width)]
        if len(matches) != 1:
            raise ValueError(f"应能按宽度唯一识别 {name}")
        parts[name] = matches[0]
    if len(rows) != 3:
        raise ValueError("场景应恰有三个零件")
    return parts


def gap(rows):
    parts = identify(rows)
    return parts["A"]["bbox_min"][0] - parts["B"]["bbox_max"][0]


def run_case(client, moved):
    from app.command_result import manifest_from_result
    from app.contracts import SceneComponent

    response = client.post("/api/v1/projects", json={"name": f"gap-{moved}"})
    response.raise_for_status()
    project = "/api/v1/projects/" + response.json()["id"]

    def snapshot():
        result = client.get(project + "/snapshot")
        result.raise_for_status()
        return result.json()

    def command(action, params, base, actor, **extra):
        return client.post(project + "/collab/commands", json={
            "action": action, "params": params, "base_version": base, "author": actor,
            "command_id": str(uuid4()),
            "character": {"id": actor, "kind": actor, "display_name": actor}, **extra,
        })

    def candidate(state, offset):
        rows = state["manifest"]
        part = identify(rows)["A"]["hash_sat"]
        preview = project + "/previews/" + uuid4().hex
        try:
            result = client.post(preview + "/execute", json={
                "manifest": rows, "revision": 0,
                "command": {"action": "component.translate",
                            "params": {"component": part, "offset": [offset, 0, 0]}},
            })
            result.raise_for_status()
            manifest, parents, _, _ = manifest_from_result(
                [SceneComponent(**r) for r in rows], result.json()
            )
            return {"manifest": [asdict(c) for c in manifest],
                    "projection_parents": {digest: parent for digest, _, parent in parents}}
        finally:
            client.delete(preview).raise_for_status()

    def publish(state, proposed):
        return command("scene.publish", proposed, state["version"], "agent",
                       observed_dependencies=[r["hash_sat"] for r in state["manifest"]])

    for width, center in ((6, [33, 0, 0]), (20, [10, 0, 0]), (4, [60, 25, 0])):
        command("component.create", {"kind": "cube", "width": width, "height": 6,
                "depth": 6, "position": center}, snapshot()["version"], "human").raise_for_status()
    original = snapshot()
    old_offset = TARGET_GAP - gap(original["manifest"])
    old_candidate = candidate(original, old_offset)
    if moved != "none":
        reference = identify(original["manifest"])[moved]["hash_sat"]
        command("component.translate", {"component": reference, "offset": [5, 0, 0]},
                original["version"], "human").raise_for_status()

    current = snapshot()
    first = publish(original, old_candidate)
    expected_status = 200 if moved == "none" else 409
    if first.status_code != expected_status:
        raise RuntimeError(f"{moved}: HTTP {first.status_code}, {first.text}")
    old_offset_gap = gap(old_candidate["manifest"])
    offset = old_offset
    admission = None
    if first.status_code == 409:
        admission = first.json()["detail"]["admission"]
        if snapshot() != current:
            raise RuntimeError("被拒绝的预览改变了正式版本")
        # 在新的私有草稿里对照：沿用旧位移会怎样？这个草稿不提交。
        old_offset_gap = gap(candidate(current, old_offset)["manifest"])
        offset = TARGET_GAP - gap(current["manifest"])
        publish(current, candidate(current, offset)).raise_for_status()
    final = snapshot()
    before, after = identify(current["manifest"]), identify(final["manifest"])
    response = client.post(project + "/geometry/query", json={
        "base_version": final["version"],
        "query": {"kind": "interference", "a": after["A"]["hash_sat"], "b": after["B"]["hash_sat"]},
    })
    response.raise_for_status()
    measured = response.json()["result"]
    preserved = all(before[k]["hash_sat"] == after[k]["hash_sat"] for k in ("B", "C"))
    aligned = all(near(before["A"][bound][axis], after["A"][bound][axis])
                  for axis in (1, 2) for bound in ("bbox_min", "bbox_max"))
    if not (near(gap(final["manifest"]), TARGET_GAP) and near(measured["distance"], TARGET_GAP)
            and not measured["interferes"] and preserved and aligned
            and final["version"] == current["version"] + 1):
        raise RuntimeError("距离、干涉、非目标组件、Y/Z 位置或版本的验收失败")
    return {"collaborator_moved": moved, "first_publish_http": first.status_code,
            "admission_on_rejection": admission, "original_x_offset_mm": round(old_offset, 5),
            "old_offset_on_current_scene_signed_gap_mm": round(old_offset_gap, 5),
            "verified_x_offset_mm": round(offset, 5), "acis_distance_mm": round(measured["distance"], 5),
            "interference": measured["interferes"], "non_target_components_preserved": preserved,
            "a_yz_preserved": aligned, "versions": [original["version"], current["version"], final["version"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True, type=Path)
    parser.add_argument("--code-root", type=Path,
                        default=Path(__file__).resolve().parents[2] / "DBCAD-mac6" / "code")
    args = parser.parse_args()
    if not args.worker.is_file():
        parser.error("需要已构建的 DBCAD worker 启动器")
    sys.path.insert(0, str(args.code_root.resolve() / "CAD_DB" / "backend"))
    sys.dont_write_bytecode = True
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.headless import create_app
    from app.v1_store import initialize_v1_store, shutdown_v1_store
    from app.worker_pool import get_worker_pool

    @asynccontextmanager
    async def lifespan(_):
        initialize_v1_store(memory=True)
        try:
            yield
        finally:
            await get_worker_pool().shutdown()
            shutdown_v1_store()

    with TemporaryDirectory(prefix="dbcad-gap-assets-") as assets:
        settings.worker_exe = str(args.worker.resolve())
        settings.component_root = assets
        settings.api_password = ""  # 仅本进程的测试客户端。没有监听端口。
        with TestClient(create_app(lifespan)) as client:
            storage = client.get("/health").json()["storage_backend"]
            if storage != "MemoryV1Store":
                raise RuntimeError(f"意外的版本库：{storage}")
            trials = [run_case(client, part) for part in ("none", "C", "B")]
    print(json.dumps({"source_baseline": "b715e61", "storage": storage, "llm_calls": 0,
                      "target_gap_mm": TARGET_GAP, "tolerance_mm": TOLERANCE, "trials": trials},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
