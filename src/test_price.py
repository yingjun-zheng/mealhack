"""决定性验证：calculate-price 是否接受自定义 roundList，且价格随 diffPrice 变化。

三个用例：
  A 默认组合（中薯条 + 可乐中杯）     预期 33.5
  B 最便宜组合（小杯玉米杯 -1.5）      预期 32.0
  C 最贵组合（咖喱鸡 +2.5，热黑巧 +11） 预期 47.0
三者若全部命中，方案 A 成立。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_client import McdMCP, McpError  # noqa: E402

STORE = "3230251"
MEAL = "9900005456"
BASE = 33.5

CASES = {
    "A 默认组合": ("4810", "3050", 33.5),
    "B 最便宜": ("4437", "3050", 32.0),
    "C 最贵": ("521881", "515945", 47.0),
}


def build(side: str, drink: str) -> list[dict]:
    return [
        {"round": "1", "comboItemList": [{"code": "1440", "quantity": 1}]},
        {"round": "2", "comboItemList": [{"code": side, "quantity": 1}]},
        {"round": "3", "comboItemList": [{"code": drink, "quantity": 1}]},
    ]


def find_price(node: object, hits: list) -> None:
    """从返回里找所有可能的金额字段，先宽后窄。"""
    if isinstance(node, dict):
        for key, val in node.items():
            low = key.lower()
            if "price" in low or "amount" in low or "total" in low:
                if isinstance(val, (int, float, str)):
                    hits.append((key, val))
            find_price(val, hits)
    elif isinstance(node, list):
        for item in node:
            find_price(item, hits)


def main() -> int:
    cli = McdMCP()
    cli.initialize()

    for label, (side, drink, expect) in CASES.items():
        payload = {
            "storeCode": STORE,
            "orderType": 1,
            "beType": 1,
            "items": [
                {
                    "productCode": MEAL,
                    "quantity": 1,
                    "roundList": build(side, drink),
                }
            ],
        }
        print("=" * 66)
        print(f"{label}  小食={side} 饮品={drink}  预期 {expect}")
        try:
            result = cli.call("calculate-price", payload)
        except McpError as exc:
            print(f"  调用失败: {exc}")
            continue

        Path(f"samples/price_{label.split()[0]}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if isinstance(result, dict):
            data = result.get("data", result)
            hits: list = []
            find_price(data, hits)
            # 只展示看起来像"应付总额"的字段
            interesting = [(k, v) for k, v in hits if any(
                t in k.lower() for t in ("total", "payable", "actual", "final", "amount")
            )]
            shown = interesting or hits[:8]
            print(f"  返回字段 sample: {shown[:10]}")
            print(f"  (完整响应已存 samples/price_{label.split()[0]}.json)")
        else:
            print(f"  非 dict 返回: {str(result)[:200]}")
        print()

    print(f"累计请求 {cli.calls} 次")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except McpError as exc:
        print(f"\n[失败] {exc}")
        raise SystemExit(1) from None
