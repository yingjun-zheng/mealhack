"""MealHack 主入口：套餐组合求解。

用法：
    python src/solve.py --city 沈阳 --keyword 太原街 --meal 9900005456
    python src/solve.py --list-meals            # 只看门店有哪些套餐
    python src/solve.py --meal <code> --full-verify   # 全量官方核价（慢）

定价策略：
    默认用 diffPrice 本地推算（秒级），再抽样 12 个组合调 calculate-price 交叉验证。
    --full-verify 则对每个组合都调官方核价（154 组合约 25 秒）。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adapt import (  # noqa: E402
    build_round_list,
    cents_to_yuan,
    enrich_with_nutrition,
    parse_choice_map,
    parse_nutrition,
    parse_rounds,
)
from mcp_client import McdMCP, McpError  # noqa: E402
from pareto import (  # noqa: E402
    Combo,
    enumerate_combos,
    knee_point,
    pareto_front,
    rank_by,
)

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "samples" / "price_cache.json"


# ---------------------------------------------------------------- 门店 / 菜单


def find_store(cli: McdMCP, city: str, keyword: str) -> tuple[str, str]:
    result = cli.call(
        "query-nearby-stores",
        {"beType": 1, "searchType": 2, "city": city, "keyword": keyword},
    )
    data = (result or {}).get("data") or []
    if not data:
        raise McpError(f"未找到门店：{city} {keyword}")
    first = data[0]
    return str(first["storeCode"]), str(first.get("storeName") or "")


def load_meals(cli: McdMCP, store: str) -> tuple[dict, dict]:
    result = cli.call("query-meals", {"storeCode": store, "orderType": 1, "beType": 1})
    data = (result or {}).get("data") or {}
    return data.get("meals") or {}, data


def pick_meal(meals: dict, keyword: str | None) -> tuple[str, str]:
    """按 code 或名字关键字挑套餐。优先选名字含"套"的。"""
    if keyword and keyword in meals:
        return keyword, meals[keyword].get("name", keyword)

    combos = [
        (code, info.get("name", ""))
        for code, info in meals.items()
        if any(t in (info.get("name") or "") for t in ("套", "件套", "组合", "桶"))
    ]
    if keyword:
        matched = [c for c in combos if keyword in c[1]]
        if matched:
            return matched[0]
    if combos:
        return combos[0]
    raise McpError("菜单里没找到套餐，试试 --list-meals 看看有什么")


# ---------------------------------------------------------------- 定价


def load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_cache(cache: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def official_price(
    cli: McdMCP, store: str, meal_code: str, picks: tuple[str, ...], round_ids: list[str]
) -> float | None:
    """调 calculate-price 拿真实价格（元）。失败返回 None。"""
    payload = {
        "storeCode": store,
        "orderType": 1,
        "beType": 1,
        "items": [
            {
                "productCode": meal_code,
                "quantity": 1,
                "roundList": build_round_list(meal_code, picks, round_ids),
            }
        ],
    }
    try:
        result = cli.call("calculate-price", payload)
    except McpError:
        return None
    data = (result or {}).get("data") or {}
    for key in ("price", "productPrice", "originalPrice"):
        if key in data:
            return cents_to_yuan(data[key])
    return None


# ---------------------------------------------------------------- 分析


def domination_count(base: Combo, pool: list[Combo]) -> int:
    """有多少组合"价格不高于它、蛋白质不低于它、且至少一项严格更优"。

    这就是"你以为最优的默认搭配，其实被多少个方案碾压"。
    """
    count = 0
    for other in pool:
        if other is base:
            continue
        better_price = other.price <= base.price + 1e-9
        better_protein = other.protein >= base.protein - 1e-9
        strict = (other.price < base.price - 1e-9) or (other.protein > base.protein + 1e-9)
        if better_price and better_protein and strict:
            count += 1
    return count


HEAD = "| 组合 | 实付 | 热量 | 蛋白质 | 脂肪 | 钠 | 蛋白/元 |"
SEP = "|---|---|---|---|---|---|---|"


def row(combo: Combo) -> str:
    cells = [
        combo.label(),
        f"{combo.price:.2f}",
        f"{combo.kcal:.0f}",
        f"{combo.protein:.1f}",
        f"{combo.fat:.1f}",
        f"{combo.sodium:.0f}",
        f"{combo.protein_per_yuan:.2f}",
    ]
    return "| " + " | ".join(cells) + " |"


# ---------------------------------------------------------------- 主流程


def main() -> int:
    parser = argparse.ArgumentParser(description="MealHack 套餐组合求解器")
    parser.add_argument("--city", default="沈阳")
    parser.add_argument("--keyword", default="太原街")
    parser.add_argument("--store", help="直接指定 storeCode")
    parser.add_argument("--meal", help="套餐 code 或名字关键字")
    parser.add_argument("--list-meals", action="store_true", help="列出门店所有套餐")
    parser.add_argument("--full-verify", action="store_true", help="全量官方核价")
    parser.add_argument("--top", type=int, default=6)
    parser.add_argument("--json", help="把求解结果导出为 JSON（供网页可视化使用）")
    args = parser.parse_args()

    cli = McdMCP()
    cli.initialize()

    if args.store:
        store, store_name = args.store, "(手动指定)"
    else:
        store, store_name = find_store(cli, args.city, args.keyword)
    print(f"门店：{store_name}  [{store}]")

    meals, _ = load_meals(cli, store)
    print(f"菜单：{len(meals)} 个商品")

    if args.list_meals:
        print()
        print("=== 套餐类商品 ===")
        for code, info in meals.items():
            name = info.get("name", "")
            if any(t in name for t in ("套", "件套", "组合", "桶")):
                cur = info.get("currentPrice", "?")
                org = info.get("originalPrice", "?")
                print(f"  {code:>12}  {name:<28} 现价 {cur}  原价 {org}")
        return 0

    meal_code, meal_name = pick_meal(meals, args.meal)
    # 注意：query-meals 的 currentPrice 单位是「元」（字符串 "33.5"），
    # 而 calculate-price 返回的 data.price 单位是「分」（3350）。两者不能混用。
    base_price = float(meals[meal_code].get("currentPrice") or 0)
    print(f"套餐：{meal_name}  [{meal_code}]  现价 {base_price:.2f} 元")
    print()

    detail = cli.call(
        "query-meal-detail",
        {"storeCode": store, "orderType": 1, "beType": 1, "code": meal_code},
    )
    detail_data = (detail or {}).get("data") or {}
    choice_map = parse_choice_map(detail_data)
    meal, warnings = parse_rounds(detail_data)
    round_ids = [str(r.get("id")) for r in detail_data.get("rounds") or [] if r.get("choices")]

    # 多份槽位（quantity>1）的套餐，calculate-price 会静默退回原价而不是报错。
    # 实测八件套传完整 roundList 仍返回 11700（原价），说明替换根本不生效。
    # 这类套餐只能给本地推算价，必须明确告知用户"未经官方验证"。
    multi_rounds = [
        r
        for r in detail_data.get("rounds") or []
        if int(r.get("quantity") or 1) > 1 and r.get("choices")
    ]
    price_unverified = bool(multi_rounds)
    if price_unverified:
        qty = int(multi_rounds[0].get("quantity") or 2)
        print(
            f"  [重要] 该套餐有 {len(multi_rounds)} 个槽位需要 {qty} 份商品。"
            "calculate-price 对这类套餐无法正确核价（会静默退回原价），"
            "因此下面所有价格均为**本地推算，未经官方验证**。"
        )

    nutrition_raw = cli.call("list-nutrition-foods", {})
    nutrition, _ = parse_nutrition(str((nutrition_raw or {}).get("data") or ""))
    print(f"营养库：{len(nutrition)} 条")

    meal = enrich_with_nutrition(meal, choice_map, nutrition, warnings)
    for w in warnings:
        print(f"  [警告] {w}")
    print()

    # 价格 = 现价 + 所选额外加价（默认项加价为 0）
    def local_price(items: tuple) -> float:
        extra = 0.0
        for item in items:
            ch = choice_map.get(item.code)
            if ch:
                extra += ch.diff_price
        return round(base_price + extra, 2)

    combos, meta = enumerate_combos(meal, price_fn=local_price)
    print(
        f"组合空间：{meta['raw_combinations']} 种"
        + ("（已分层采样）" if meta["slimmed"] else "（全部枚举）")
        + f"，实评 {meta['evaluated']} 种"
    )
    print()

    # ---- 定价校验 ----
    verify_rate: float | None = None
    if price_unverified:
        print("  已跳过官方核价校验（该套餐结构不支持 roundList 替换）")
        print()
    else:
        sample_n = len(combos) if args.full_verify else min(12, len(combos))
        sample = combos if args.full_verify else random.Random(1024).sample(combos, sample_n)
        cache = load_cache()
        mismatches = 0
        checked = 0
        print(f"官方核价校验中（{sample_n} 个组合）…")
        for combo in sample:
            picks = tuple(item.code for item in combo.items)
            key = f"{meal_code}|" + ",".join(picks)
            if key in cache:
                official = cache[key]
            else:
                official = official_price(cli, store, meal_code, picks, round_ids)
                if official is not None:
                    cache[key] = official
                else:
                    continue
            checked += 1
            if abs(official - combo.price) > 0.011:
                mismatches += 1
                combo.price = official  # 以官方价为准
        save_cache(cache)
        if checked:
            rate = (checked - mismatches) / checked * 100
            verify_rate = round(rate, 1)
            print(f"  校验 {checked} 个，本地推算与官方一致率 {rate:.1f}%（请求 {cli.calls} 次）")
        else:
            # 静默失败比报错更危险，必须显式告知
            print("  [警告] 全部抽样核价失败，本次结论未经官方验证")
        print()

    # ---- 分析 ----
    default_combo = next(
        (c for c in combos if all(choice_map.get(i.code) and choice_map[i.code].is_default for i in c.items)),
        combos[0],
    )
    dominated = domination_count(default_combo, combos)

    prices = [c.price for c in combos]
    print("=" * 72)
    print(f"实付区间：{min(prices):.2f} ~ {max(prices):.2f} 元（相差 {max(prices) - min(prices):.2f} 元）")
    print(f"默认搭配：{default_combo.label()}")
    print(
        f"  → 实付 {default_combo.price:.2f} 元 / {default_combo.kcal:.0f} kcal / "
        f"蛋白 {default_combo.protein:.1f}g"
    )
    print(f"  → 被 {dominated} 个组合支配（更便宜且蛋白不更少）")
    print("=" * 72)
    print()

    for title, key in [
        ("增肌最优（每元蛋白质）", lambda c: c.protein_per_yuan),
        ("低热量最优（减脂视角）", lambda c: -c.kcal),
        ("最便宜", lambda c: -c.price),
    ]:
        print(f"**{title}**")
        print()
        print(HEAD)
        print(SEP)
        for combo in rank_by(combos, key, args.top):
            print(row(combo))
        print()

    # 主视图：价格 vs 蛋白质 —— 这才是"该推荐什么"的维度
    front_price = pareto_front(combos, lambda c: c.price, lambda c: c.protein)
    print("**帕累托前沿：价格 vs 蛋白质**")
    print()
    print("*每一点都无法被「更便宜且蛋白更高」的方案替代*")
    print()
    print(HEAD)
    print(SEP)
    for combo in front_price:
        print(row(combo))
    print()

    knee = knee_point(front_price, lambda c: c.price, lambda c: c.protein)
    if knee:
        d_price = default_combo.price - knee.price
        d_kcal = knee.kcal - default_combo.kcal
        d_protein = knee.protein - default_combo.protein
        print(f"**拐点推荐**：{knee.label()}")
        print()
        print(
            f"| | 实付 | 热量 | 蛋白质 |\n"
            f"|---|---|---|---|\n"
            f"| 默认搭配 | {default_combo.price:.2f} | {default_combo.kcal:.0f} | "
            f"{default_combo.protein:.1f} |\n"
            f"| 拐点方案 | {knee.price:.2f} | {knee.kcal:.0f} | {knee.protein:.1f} |\n"
            f"| 差额 | {-d_price:+.2f} | {d_kcal:+.0f} | {d_protein:+.1f} |"
        )
        print()

    # 边际收益：让用户看见"断崖"在哪，而不是只丢一个答案给他
    segments: list[tuple] = []
    if len(front_price) > 1:
        for prev, cur in zip(front_price, front_price[1:]):
            dp = cur.price - prev.price
            dv = cur.protein - prev.protein
            if dp <= 1e-9:
                continue
            segments.append((prev, cur, dp, dv, dv / dp))

        if segments:
            worst = min(s[4] for s in segments)
            print("**边际收益：每多花 1 元能换多少蛋白**")
            print()
            print("| 价格区间 | 多花 | 蛋白增加 | 每元换蛋白 |")
            print("|---|---|---|---|")
            for prev, cur, dp, dv, ratio in segments:
                flag = "  ← 加钱不划算了" if abs(ratio - worst) < 1e-9 else ""
                print(
                    f"| {prev.price:.2f} → {cur.price:.2f} | {dp:+.2f} | "
                    f"{dv:+.1f} | {ratio:.2f}{flag} |"
                )
            print()

    # 副视图：热量 vs 蛋白质 —— 减脂视角
    front_kcal = pareto_front(combos, lambda c: c.kcal, lambda c: c.protein)
    print("**帕累托前沿：热量 vs 蛋白质**（减脂视角）")
    print()
    print(HEAD)
    print(SEP)
    for combo in front_kcal:
        print(row(combo))
    print()

    if args.json:

        def idx_of(target: Combo | None) -> int | None:
            if target is None:
                return None
            for i, candidate in enumerate(combos):
                if candidate.picks == target.picks:
                    return i
            return None

        payload = {
            "store": {"code": store, "name": store_name},
            "meal": {"code": meal_code, "name": meal_name, "base_price": base_price},
            "meta": {
                "combination_count": len(combos),
                "price_verified": not price_unverified,
                "verify_rate": verify_rate,
                "front_size": len(front_price),
            },
            "warnings": warnings,
            "default_index": idx_of(default_combo),
            "knee_index": idx_of(knee),
            "front_indices": [i for i in (idx_of(c) for c in front_price) if i is not None],
            "combos": [
                {
                    "label": c.label(),
                    "price": round(c.price, 2),
                    "kcal": round(c.kcal),
                    "protein": round(c.protein, 1),
                    "fat": round(c.fat, 1),
                    "sodium": round(c.sodium),
                }
                for c in combos
            ],
            "marginal": [
                {
                    "from_price": round(a.price, 2),
                    "to_price": round(b.price, 2),
                    "delta_price": round(dp, 2),
                    "delta_protein": round(dv, 1),
                    "per_yuan": round(ratio, 2),
                }
                for a, b, dp, dv, ratio in segments
            ],
        }
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"已导出 {path}")

    print(f"累计请求 {cli.calls} 次")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except McpError as exc:
        print(f"\n[失败] {exc}")
        raise SystemExit(1) from None
