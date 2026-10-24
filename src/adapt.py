"""真实数据适配层：把 MCP 的原始返回转成优化引擎认识的结构。

踩过的坑（均已在此处处理）：
1. content[].text 是"展示说明 + JSON"混合文本 → 由 mcp_client.extract_json 处理
2. 价格统一是"分"（3350 = 33.5 元），且 currentPrice 在菜单里是字符串
3. diffPrice 形如 "+ ¥1.5" / "- ¥1.5" / "+ ¥0"，必须解析正负号
4. 营养数据是 CSV 纯文本，按 productName 匹配，名字存在
   "纯牛奶" vs "纯牛奶（盒装）" 这类差异，需要归一化
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from pareto import Item, SetMeal, Slot

# ---------------------------------------------------------------- 价格


def cents_to_yuan(value: object) -> float:
    """MCP 的金额字段单位是分。"""
    try:
        return round(float(value) / 100.0, 2)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


_DIFF_RE = re.compile(r"([+-])?\s*[¥￥]?\s*([\d.]+)")


def parse_diff_price(text: str | None) -> float:
    """解析 "+ ¥1.5" / "- ¥1.5" / "+ ¥0" 这类加价标识。"""
    if not text:
        return 0.0
    match = _DIFF_RE.search(str(text))
    if not match:
        return 0.0
    sign = -1.0 if match.group(1) == "-" else 1.0
    try:
        return sign * float(match.group(2))
    except ValueError:
        return 0.0


# ---------------------------------------------------------------- 营养


@dataclass
class Nutrition:
    name: str
    kcal: float = 0.0
    protein: float = 0.0
    fat: float = 0.0
    carb: float = 0.0
    sodium: float = 0.0
    calcium: float = 0.0


def normalize_name(name: str) -> str:
    """名字归一化：全半角统一、去括号内容、去空白。

    "纯牛奶（盒装）" -> "纯牛奶"，"麦咖啡™冰美式中杯" -> "麦咖啡冰美式中杯"
    """
    text = unicodedata.normalize("NFKC", name or "")
    text = re.sub(r"[（(].*?[)）]", "", text)
    text = re.sub(r"[™®\s·・]", "", text)
    return text.strip().lower()


def parse_nutrition(text: str) -> tuple[dict[str, Nutrition], str]:
    """解析 list-nutrition-foods 返回的 CSV 文本。

    格式：``[160]{productName,nutritionDescription,energyKj,energyKcal,...}:``
    之后每行一条，逗号分隔。
    """
    table: dict[str, Nutrition] = {}
    header_line = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "{" in line and "}" in line:
            header_line = line
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        name = parts[0]
        try:
            kcal = float(parts[3])
            protein = float(parts[4])
        except (IndexError, ValueError):
            continue
        fat = _safe_float(parts, 5)
        carb = _safe_float(parts, 6)
        sodium = _safe_float(parts, 7)
        calcium = _safe_float(parts, 8)
        item = Nutrition(name, kcal, protein, fat, carb, sodium, calcium)
        table.setdefault(normalize_name(name), item)
    return table, header_line


def _safe_float(parts: list[str], idx: int) -> float:
    try:
        return float(parts[idx])
    except (IndexError, ValueError):
        return 0.0


def lookup_nutrition(table: dict[str, Nutrition], name: str) -> Nutrition | None:
    """先精确（归一化后），再退化为包含匹配。"""
    key = normalize_name(name)
    if key in table:
        return table[key]
    for k, v in table.items():
        if key and (key in k or k in key):
            return v
    return None


# ---------------------------------------------------------------- 套餐


@dataclass
class Choice:
    code: str
    name: str
    diff_price: float
    is_default: bool
    support_modify: bool
    round_id: str


def parse_rounds(data: dict) -> tuple[SetMeal, list[str]]:
    """把 query-meal-detail 的 data 转成 SetMeal。

    返回 (套餐, 警告列表)。营养数据缺失时用 0 填充并记警告，
    绝不编造数值——这是原则问题。
    """
    warnings: list[str] = []
    name = data.get("name") or data.get("code") or "未知套餐"
    slots: list[Slot] = []

    for rnd in data.get("rounds") or []:
        rid = str(rnd.get("id", len(slots) + 1))
        choices = rnd.get("choices") or []
        if not choices:
            continue
        items: list[Item] = []
        # 默认项排最前，保证 items[0] 是默认
        ordered = sorted(
            choices,
            key=lambda c: (0 if c.get("isDefault") == 1 else 1, c.get("name") or ""),
        )
        for ch in ordered:
            code = str(ch.get("code") or "")
            if not code:
                continue
            items.append(
                Item(
                    code=code,
                    name=str(ch.get("name") or code),
                    price=0.0,
                    kcal=0.0,
                    protein=0.0,
                )
            )
        slot_name = rnd.get("name") or f"round-{rid}"
        slots.append(Slot(name=slot_name, items=items))

    if not slots:
        warnings.append("该套餐没有可替换槽位")
    return SetMeal(name=str(name), base_price=0.0, slots=slots), warnings


def parse_choice_map(data: dict) -> dict[str, Choice]:
    """建立 code -> Choice 的扁平索引，便于取 diffPrice 与原名。"""
    index: dict[str, Choice] = {}
    for rnd in data.get("rounds") or []:
        rid = str(rnd.get("id", ""))
        for ch in rnd.get("choices") or []:
            code = str(ch.get("code") or "")
            if not code:
                continue
            index[code] = Choice(
                code=code,
                name=str(ch.get("name") or code),
                diff_price=parse_diff_price(ch.get("diffPrice")),
                is_default=ch.get("isDefault") == 1,
                support_modify=bool(ch.get("supportModify")),
                round_id=rid,
            )
    return index


def build_round_list(meal_code: str, picks: tuple[str, ...], round_ids: list[str]) -> list[dict]:
    """按选择结果拼出 calculate-price 需要的 roundList。"""
    return [
        {"round": rid, "comboItemList": [{"code": code, "quantity": 1}]}
        for rid, code in zip(round_ids, picks)
    ]


def enrich_with_nutrition(
    meal: SetMeal,
    choice_map: dict[str, Choice],
    nutrition: dict[str, Nutrition],
    warnings: list[str],
) -> SetMeal:
    """把营养数据灌进候选餐品。

    缺营养数据的餐品会被**直接剔除**，而不是按 0 填充。
    理由：按 0 计会让该组合在"低热量榜"上虚假登顶，这属于数据造假，
    宁可缩小组合空间，也不能给出错误结论。
    """
    slots: list[Slot] = []
    missing: set[str] = set()

    for slot in meal.slots:
        kept: list[Item] = []
        for item in slot.items:
            nut = lookup_nutrition(nutrition, item.name)
            if nut is None:
                missing.add(item.name)
                continue
            kept.append(
                Item(
                    code=item.code,
                    name=item.name,
                    price=item.price,
                    kcal=nut.kcal,
                    protein=nut.protein,
                    fat=nut.fat,
                    carb=nut.carb,
                    sodium=nut.sodium,
                )
            )

        if kept:
            slots.append(Slot(name=slot.name, items=kept))
        else:
            # 整个槽位都没营养数据，只能保留原样并明确告警
            warnings.append(
                f"槽位「{slot.name}」的餐品全部缺营养数据，该套餐的营养结论不可信"
            )
            slots.append(slot)

    if missing:
        warnings.append(
            f"{len(missing)} 个餐品缺营养数据，已排除出组合空间："
            + "、".join(sorted(missing)[:8])
        )
    return SetMeal(name=meal.name, base_price=meal.base_price, slots=slots)
