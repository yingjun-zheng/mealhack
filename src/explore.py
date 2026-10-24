"""决策性验证：走通 门店 → 菜单 → 套餐详情 → 组合核价 全链路。

只做探测和 dump，不做业务判断。目的是拿到真实返回结构，
确认"套餐替换树"是否可枚举、calculate-price 是否接受自定义 roundList。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp_client import McdMCP, McpError  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "samples"
CITY = "沈阳"
KEYWORD = "太原街"


def save(name: str, obj: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.json"
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  已保存 samples/{path.name}")


def brief(obj: object, max_len: int = 700) -> str:
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    return text if len(text) <= max_len else text[:max_len] + "\n  ...(已截断)"


def walk_keys(obj: object, depth: int = 0, prefix: str = "") -> None:
    """把嵌套结构压成 key 路径，避免被超长 JSON 淹没。"""
    pad = "  " * depth
    if depth > 3:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                size = f"[{len(v)}]" if isinstance(v, list) else ""
                print(f"{pad}{k}{size}")
                walk_keys(v, depth + 1)
            else:
                s = str(v)
                s = s[:40] + "…" if len(s) > 40 else s
                print(f"{pad}{k} = {s}")
    elif isinstance(obj, list) and obj:
        print(f"{pad}(首元素)")
        walk_keys(obj[0], depth + 1)


def main() -> int:
    cli = McdMCP()
    cli.initialize()
    print(f"session={cli.session_id}\n")

    # ---------- 1. 门店 ----------
    print("[1] query-nearby-stores")
    stores = cli.call(
        "query-nearby-stores",
        {"beType": 1, "searchType": 2, "city": CITY, "keyword": KEYWORD},
    )
    save("stores", stores)
    print(brief(stores, 900))
    print()

    # 从返回里取门店（明确路径，别用递归瞎猜——会命中 code:200）
    store_code = None
    store_name = None
    if isinstance(stores, dict):
        data = stores.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            store_code = str(first.get("storeCode") or "") or None
            store_name = first.get("storeName")
    if not store_code:
        print("!! 没能从返回中提取 storeCode，人工检查 samples/stores.json")
        return 1
    print(f"使用门店: {store_name} ({store_code})\n")

    # ---------- 2. 菜单 ----------
    print("[2] query-meals")
    meals = cli.call(
        "query-meals",
        {"storeCode": store_code, "orderType": 1, "beType": 1},
    )
    save("meals", meals)
    walk_keys(meals)
    print()

    # ---------- 3. 找套餐 ----------
    print("[3] 在菜单里定位套餐类商品")
    candidates: list[tuple[str, str]] = []

    def collect(node: object) -> None:
        if isinstance(node, dict):
            code = node.get("code") or node.get("productCode")
            name = node.get("name") or node.get("productName") or ""
            is_combo = any(
                k in node for k in ("roundList", "comboItemList", "isCombo", "comboType")
            )
            if code and isinstance(name, str) and is_combo:
                candidates.append((str(code), name))
            for v in node.values():
                collect(v)
        elif isinstance(node, list):
            for v in node:
                collect(v)

    collect(meals)
    print(f"  命中 {len(candidates)} 个疑似套餐")
    for code, name in candidates[:12]:
        print(f"    {code}  {name}")
    print()

    if not candidates:
        print("!! 未找到含 roundList 的套餐，需检查菜单结构")
        return 1

    # ---------- 4. 套餐详情 ----------
    target_code, target_name = candidates[0]
    print(f"[4] query-meal-detail  -> {target_name} ({target_code})")
    detail = cli.call(
        "query-meal-detail",
        {
            "storeCode": store_code,
            "orderType": 1,
            "beType": 1,
            "code": target_code,
        },
    )
    save("meal_detail", detail)
    walk_keys(detail)
    print()

    print(f"累计请求 {cli.calls} 次")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except McpError as exc:
        print(f"\n[失败] {exc}")
        raise SystemExit(1) from None
