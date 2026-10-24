"""提交前脱敏：清理 samples/ 下的真实返回快照。

处理两类东西：
1. 手机号——即使是官方文档里的示例值（delivery-create-address 的参数说明里就带了一个），
   也不该出现在公开仓库里，审核会按个人信息处理。
2. traceId——服务端内部请求追踪标识，对复现没有价值，删掉。

门店名称与地址属于公开商业信息，予以保留（技术说明需要具体案例）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"

PHONE = re.compile(r"(?<![0-9])1[3-9]\d{9}(?![0-9])")
DROP_KEYS = {"traceid", "trace_id"}


def clean(node: object) -> object:
    if isinstance(node, dict):
        return {
            k: clean(v)
            for k, v in node.items()
            if k.lower() not in DROP_KEYS
        }
    if isinstance(node, list):
        return [clean(x) for x in node]
    if isinstance(node, str):
        return PHONE.sub("138****0000", node)
    return node


def main() -> int:
    if not SAMPLES.exists():
        print("没有 samples 目录")
        return 1

    changed = 0
    for path in sorted(SAMPLES.glob("*.json")):
        raw = path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"  跳过（非 JSON）：{path.name}")
            continue

        cleaned = clean(data)
        out = json.dumps(cleaned, ensure_ascii=False, indent=2)
        if out != raw:
            path.write_text(out, encoding="utf-8")
            changed += 1
            print(f"  已脱敏：{path.name}")
        else:
            print(f"  无需改动：{path.name}")

    print(f"\n完成，{changed} 个文件被脱敏")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
