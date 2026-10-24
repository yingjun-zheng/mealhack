"""套餐组合优化引擎：枚举 → 真实核价 → 双目标帕累托前沿 → 场景化推荐。

设计要点
--------
1. 与 MCP 层完全解耦：只吃标准化后的 Item / Slot / SetMeal，
   真实价格由 price_fn 回调（生产中接 calculate-price）注入。
2. 不做"推荐一个答案"这种事——那是 LLM 拍脑袋。这里输出可行域边界，
   并明确标注"每一点都不可被支配"。
3. 帕累托支持任意双目标，因为套餐价恒定时价格维度会退化，
   真正有意义的权衡是"热量 vs 蛋白质"（减脂 vs 增肌）。
4. 组合数可能爆炸，内置分层采样，优先保留各指标极值点。
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from itertools import product
from typing import Callable, Iterable, Sequence

# ---------------------------------------------------------------- 数据模型


@dataclass(frozen=True)
class Item:
    """单个餐品。price 为单买价，用于反算拆分价值。"""

    code: str
    name: str
    price: float = 0.0
    kcal: float = 0.0
    protein: float = 0.0
    fat: float = 0.0
    carb: float = 0.0
    sodium: float = 0.0


@dataclass
class Slot:
    """套餐里的一个可替换槽位，items[0] 视为默认选项。"""

    name: str
    items: list[Item] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError(f"槽位 {self.name} 没有候选餐品")


@dataclass
class SetMeal:
    name: str
    base_price: float
    slots: list[Slot] = field(default_factory=list)


@dataclass
class Combo:
    picks: tuple[int, ...]
    items: tuple[Item, ...]
    price: float

    @property
    def kcal(self) -> float:
        return sum(i.kcal for i in self.items)

    @property
    def protein(self) -> float:
        return sum(i.protein for i in self.items)

    @property
    def fat(self) -> float:
        return sum(i.fat for i in self.items)

    @property
    def carb(self) -> float:
        return sum(i.carb for i in self.items)

    @property
    def sodium(self) -> float:
        return sum(i.sodium for i in self.items)

    @property
    def parts_value(self) -> float:
        """拆分价值：各项按单买价加起来值多少钱。"""
        return sum(i.price for i in self.items)

    @property
    def arbitrage(self) -> float:
        """套利倍数 = 拆分价值 / 实付价。>1 表示这单赚了。"""
        return self.parts_value / self.price if self.price > 0 else 0.0

    @property
    def protein_per_yuan(self) -> float:
        return self.protein / self.price if self.price > 0 else 0.0

    @property
    def kcal_per_yuan(self) -> float:
        return self.kcal / self.price if self.price > 0 else 0.0

    def label(self) -> str:
        return " + ".join(i.name for i in self.items)


# ---------------------------------------------------------------- 枚举


def _cost(slots: Sequence[Slot]) -> int:
    return math.prod(len(s.items) for s in slots)


def _slim(slots: Sequence[Slot], top_k: int = 4) -> list[Slot]:
    """组合爆炸时收缩候选集：保留各指标极值点 + 随机点，避免漏掉边界。"""

    score_fns: list[Callable[[Item], float]] = [
        lambda i: i.price,
        lambda i: -i.price,
        lambda i: i.protein,
        lambda i: i.kcal,
        lambda i: -i.kcal,
        lambda i: i.protein / max(i.price, 0.01),
        lambda i: i.protein / max(i.kcal, 1.0),
    ]

    slimmed: list[Slot] = []
    for slot in slots:
        keep: dict[str, Item] = {slot.items[0].code: slot.items[0]}
        for fn in score_fns:
            for item in sorted(slot.items, key=fn, reverse=True)[:top_k]:
                keep.setdefault(item.code, item)
        pool = [i for i in slot.items if i.code not in keep]
        random.shuffle(pool)
        for item in pool[:top_k]:
            keep.setdefault(item.code, item)
        slimmed.append(Slot(name=slot.name, items=list(keep.values())))
    return slimmed


def enumerate_combos(
    meal: SetMeal,
    price_fn: Callable[[tuple[Item, ...]], float] | None = None,
    limit: int = 300_000,
    seed: int = 1024,
) -> tuple[list[Combo], dict]:
    """枚举套餐所有可行组合。price_fn 缺省时用 base_price。"""
    random.seed(seed)
    slots = meal.slots
    raw_cost = _cost(slots)
    slimmed = False

    if raw_cost > limit:
        slots = _slim(slots)
        slimmed = True

    combos: list[Combo] = []
    for picks in product(*(range(len(s.items)) for s in slots)):
        items = tuple(s.items[p] for s, p in zip(slots, picks))
        price = price_fn(items) if price_fn else meal.base_price
        combos.append(Combo(picks=picks, items=items, price=price))

    meta = {
        "meal": meal.name,
        "raw_combinations": raw_cost,
        "evaluated": len(combos),
        "slimmed": slimmed,
        "slots": {s.name: len(s.items) for s in slots},
    }
    return combos, meta


# ---------------------------------------------------------------- 帕累托


def pareto_front(
    combos: Iterable[Combo],
    metric_a: Callable[[Combo], float],
    metric_b: Callable[[Combo], float],
    lower_a: bool = True,
    lower_b: bool = False,
    tol: float = 1e-9,
) -> list[Combo]:
    """通用双目标帕累托前沿。默认：价格越低越好、指标 b 越高越好。

    排序扫描法 O(n log n)。先做方向归一（都转成"越小越好"），
    再按 a 升序扫描，维护 b 的最优值。
    """
    sign_a = 1.0 if lower_a else -1.0
    sign_b = 1.0 if lower_b else -1.0

    def na(c: Combo) -> float:
        return sign_a * metric_a(c)

    def nb(c: Combo) -> float:
        return sign_b * metric_b(c)

    ranked = sorted(combos, key=lambda c: (round(na(c), 6), round(nb(c), 6)))
    front: list[Combo] = []
    best = math.inf
    for combo in ranked:
        value = nb(combo)
        if value < best - tol:
            front.append(combo)
            best = value
    return front


def knee_point(
    front: Sequence[Combo],
    metric_a: Callable[[Combo], float],
    metric_b: Callable[[Combo], float],
    lower_a: bool = True,
    lower_b: bool = False,
) -> Combo | None:
    """膝盖点：最大弦距法。

    把前沿两端的点连成一条弦，离这条弦最远的点就是"膝盖"——
    也就是"再往前推，代价开始不划算"的那个拐点。

    不用"离理想点最近"：那个定义会偏向保守端，实测会在一个价格跨度
    15 元的套餐里挑出最贵的方案，纯属数学正确、直觉荒谬。
    """
    if not front:
        return None
    if len(front) < 3:
        return front[0]

    a_vals = [metric_a(c) for c in front]
    b_vals = [metric_b(c) for c in front]
    a_lo, a_hi = min(a_vals), max(a_vals)
    b_lo, b_hi = min(b_vals), max(b_vals)

    def norm(x: float, lo: float, hi: float) -> float:
        return 0.0 if hi - lo < 1e-12 else (x - lo) / (hi - lo)

    pts = [
        (
            norm(metric_a(c), a_lo, a_hi) if lower_a else 1.0 - norm(metric_a(c), a_lo, a_hi),
            norm(metric_b(c), b_lo, b_hi) if lower_b else 1.0 - norm(metric_b(c), b_lo, b_hi),
        )
        for c in front
    ]

    x0, y0 = pts[0]
    x1, y1 = pts[-1]
    dx, dy = x1 - x0, y1 - y0
    span = math.hypot(dx, dy) or 1.0

    best, best_d = front[0], -1.0
    for combo, (x, y) in zip(front, pts):
        distance = abs(dx * (y - y0) - dy * (x - x0)) / span
        if distance > best_d:
            best, best_d = combo, distance
    return best


# ---------------------------------------------------------------- 求解


def best_under(
    combos: Iterable[Combo],
    predicate: Callable[[Combo], bool],
    key: Callable[[Combo], float],
    reverse: bool = False,
) -> Combo | None:
    """在硬约束下取最优。约束无解时返回 None——这一点很重要，
    产品必须敢说"你要的东西不存在"，而不是编一个凑数的。"""
    pool = [c for c in combos if predicate(c)]
    return max(pool, key=key) if reverse else min(pool, key=key)


def rank_by(combos: Iterable[Combo], key: Callable[[Combo], float], n: int = 8) -> list[Combo]:
    return sorted(combos, key=key, reverse=True)[:n]


# ---------------------------------------------------------------- 报告

HEAD = "| 组合 | 实付 | kcal | 蛋白质 | 拆分价值 | 套利 | 蛋白/元 |"
SEP = "|---|---|---|---|---|---|---|"


def _row(combo: Combo) -> str:
    cells = [
        combo.label(),
        f"{combo.price:.2f}",
        f"{combo.kcal:.0f}",
        f"{combo.protein:.1f}",
        f"{combo.parts_value:.2f}",
        f"{combo.arbitrage:.2f}x",
        f"{combo.protein_per_yuan:.2f}",
    ]
    return "| " + " | ".join(cells) + " |"


def render(combos: Sequence[Combo], meta: dict, top: int = 5) -> str:
    out: list[str] = []
    out.append(f"### {meta['meal']}")
    line = f"枚举 {meta['raw_combinations']} 种组合"
    if meta["slimmed"]:
        line += f"（候选过多，分层采样后实评 {meta['evaluated']} 种）"
    else:
        line += f"，全部实评 {meta['evaluated']} 种"
    out.append(line)
    out.append("")

    price_min = min(c.price for c in combos)
    price_max = max(c.price for c in combos)
    out.append(
        f"实付区间 {price_min:.2f} ~ {price_max:.2f} 元，"
        f"热量区间 {min(c.kcal for c in combos):.0f} ~ {max(c.kcal for c in combos):.0f} kcal"
    )
    out.append("")

    scenarios = [
        ("套利最优（同样的钱拿到最多东西）", lambda c: c.arbitrage),
        ("增肌最优（每元蛋白质最多）", lambda c: c.protein_per_yuan),
        ("省钱最优（热量性价比）", lambda c: c.kcal_per_yuan),
    ]
    for title, key in scenarios:
        out.append(f"**{title}**")
        out.append("")
        out.append(HEAD)
        out.append(SEP)
        out.extend(_row(c) for c in rank_by(combos, key, top))
        out.append("")

    front = pareto_front(combos, lambda c: c.kcal, lambda c: c.protein)
    out.append("**帕累托前沿：热量 vs 蛋白质（每一点都无法被同时更优的方案替代）**")
    out.append("")
    out.append(HEAD)
    out.append(SEP)
    out.extend(_row(c) for c in front)
    out.append("")

    knee = knee_point(front, lambda c: c.kcal, lambda c: c.protein)
    if knee:
        out.append(
            f"**拐点**：{knee.label()} — 实付 {knee.price:.2f} 元 / "
            f"{knee.kcal:.0f} kcal / 蛋白 {knee.protein:.1f}g（套利 {knee.arbitrage:.2f}x）"
        )
    return "\n".join(out)


# ---------------------------------------------------------------- 自测

if __name__ == "__main__":
    burger = Slot(
        "主食",
        [
            Item("B01", "巨无霸", 18.0, 550, 25, 30, 45, 950),
            Item("B02", "双层吉士堡", 22.0, 650, 30, 38, 50, 1200),
            Item("B03", "麦香鸡", 14.0, 400, 20, 20, 40, 800),
            Item("B04", "板烧鸡腿堡", 19.0, 470, 26, 20, 42, 900),
        ],
    )
    side = Slot(
        "小食",
        [
            Item("S01", "中薯条", 11.0, 350, 4, 17, 45, 300),
            Item("S02", "大薯条", 14.0, 480, 5, 23, 60, 400),
            Item("S03", "玉米杯", 8.0, 100, 3, 2, 20, 30),
            Item("S04", "麦乐鸡5块", 12.0, 250, 15, 15, 15, 500),
        ],
    )
    drink = Slot(
        "饮品",
        [
            Item("D01", "中可乐", 9.0, 210, 0, 0, 53, 25),
            Item("D02", "零度可乐", 9.0, 0, 0, 0, 0, 30),
            Item("D03", "橙汁", 10.0, 150, 1, 0, 36, 10),
            Item("D04", "纯牛奶", 11.0, 130, 7, 5, 10, 120),
        ],
    )
    meal = SetMeal("1+1+1 随心配", 25.5, [burger, side, drink])

    def demo_price(items: tuple[Item, ...]) -> float:
        """模拟"部分替换要加价"：大薯 +2，纯牛奶 +1。"""
        extra = 0.0
        for item in items:
            if item.code == "S02":
                extra += 2.0
            elif item.code == "D04":
                extra += 1.0
        return meal.base_price + extra

    combos, meta = enumerate_combos(meal, price_fn=demo_price)
    print(render(combos, meta))
    print()
    hard = best_under(
        combos,
        lambda c: c.protein >= 45 and c.kcal <= 1100,
        lambda c: c.price,
    )
    print(f"硬约束求解（蛋白≥45g 且热量≤1100kcal，取最便宜）：{hard.label() if hard else '无解'}")
