# MCP 集成说明

本文档记录 MealHack 实际调用的麦当劳 MCP Server、Tool、调用流程、关键参数陷阱与业务价值。
所有内容均来自真实联调（门店 3230251，沈阳中华路联营餐厅，2026-10-24）。

---

## 1. 接入方式

| 项 | 值 |
|---|---|
| Server | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP |
| 协议版本 | `2025-06-18` |
| 鉴权 | `Authorization: Bearer <MCD_MCP_TOKEN>` |
| 限流 | 600 次/分钟（本项目客户端默认 150ms 间隔，约 400 次/分钟） |
| 实现 | 零第三方依赖，纯 Python 标准库（`src/mcp_client.py`） |

---

## 2. 实际调用的 Tool

| Tool | 调用次数/次运行 | 用途 | 关键返回字段 |
|---|---|---|---|
| `query-nearby-stores` | 1 | 按城市+地标定位到店自取门店 | `data[].storeCode` |
| `query-meals` | 1 | 拉取门店全量菜单与套餐 | `data.meals{}` / `data.categories[]` |
| `query-meal-detail` | 1 | **解析套餐的槽位与可替换项** | `data.rounds[].choices[].diffPrice` |
| `list-nutrition-foods` | 1 | 拉取全量营养表 | `data`（CSV 纯文本，160 条） |
| `calculate-price` | 6~154 | **对自定义组合真实核价** | `data.price`（单位：分） |

> 本项目**只读**，从不调用 `create-order`，不产生任何真实订单。

---

## 3. 调用流程

```
query-nearby-stores(beType=1, searchType=2, city="沈阳", keyword="太原街")
    └─> storeCode = "3230251"
         │
query-meals(storeCode, orderType=1, beType=1)
    └─> 124 个商品，其中 36 个套餐，含 currentPrice / originalPrice
         │
query-meal-detail(storeCode, orderType=1, beType=1, code="9900005456")
    └─> data.rounds = [
          {id:1, name:"麦辣鸡腿汉堡", choices:[{code:"1440", isDefault:1, diffPrice:"+ ¥0"}]},
          {id:2, name:"选择套餐内小食", choices:[7 项，diffPrice 从 -¥1.5 到 +¥2.5]},
          {id:3, name:"选择套餐内饮料", choices:[22 项，diffPrice 从 ¥0 到 +¥11]}
        ]
         │
  枚举 7 × 22 = 154 种组合
         │
calculate-price(storeCode, orderType=1, beType=1, items=[{
    productCode: "9900005456", quantity: 1,
    roundList: [
      {round:"1", comboItemList:[{code:"1440", quantity:1}]},
      {round:"2", comboItemList:[{code:"4437", quantity:1}]},
      {round:"3", comboItemList:[{code:"3050", quantity:1}]}
    ]}])
    └─> data.price = 3200（分，即 32.00 元）
```

**核心发现**：`calculate-price` 接受任意 `roundList` 组合，且不下单、不扣费、不占库存。
这让"对上百种组合做真实核价"成为可能——本项目全部结论都建立在此之上。

---

## 4. 踩过的坑（真实联调记录）

这几条都是实际跑出来的，不是从文档抄的。任何后来者都会遇到。

### 坑 1：`content[].text` 是「展示说明 + JSON」的混合文本

MCP 返回的 `content[0].text` 并不总是纯 JSON，而是在 JSON 前面拼了一段给 LLM 看的展示规则：

```
每个门店的基本信息按单行展示：
- **门店名称**: data[].storeName
...
{"success":true,"code":200,"data":[{"storeCode":"3230251",...}]}
```

直接 `json.loads()` 必然失败。解法是扫描文本、按括号平衡提取出其中最大的合法 JSON 负载
（实现见 `mcp_client.extract_json()`）。

### 坑 2：两个接口的金额单位不一致

| 接口 | 字段 | 值 | 单位 |
|---|---|---|---|
| `query-meals` | `currentPrice` | `"33.5"` | **元**（且是字符串） |
| `calculate-price` | `data.price` | `3350` | **分** |

混用会让套餐价直接差 100 倍。本项目在 `solve.py` 里显式区分两种来源，不共用转换函数。

### 坑 3：`beCode` 的存在与否取决于 `beType`

- `beType=1`（到店自取）：返回的门店**没有** `beCode`，后续工具调用**不能**传该参数（传了报错）
- `beType=5`（得来速）：门店**有** `beCode`，后续调用**必须**传

文档里写得很清楚，但只在描述里，schema 的 `required` 里没有体现——按 `required` 写代码就会踩。

### 坑 4：营养数据是 CSV 纯文本，且名字对不齐

`list-nutrition-foods` 返回的 `data` 是 6016 字符的**字符串**，格式为：

```
[160]{productName,nutritionDescription,energyKj,energyKcal,protein,fat,carbohydrate,sodium,calcium}:
  猪柳麦满分,null,1288,308,16,16,24,781,213
  中薯条,null,1210,289,4,12,38,165,18
```

且名字存在差异：套餐里叫「纯牛奶」，营养表里叫「纯牛奶（盒装）」。
必须做归一化（全半角统一、去括号、去 ™ 符号）后模糊匹配。

---

### 坑 5：多份槽位（`quantity>1`）的套餐，`roundList` 替换会静默失效

这是最危险的一个坑，因为它**不报错，而是给你一个看起来正常的错误价格**。

「全明星双人分享餐八件套」（`9900011056`）有 6 个 round，其中 `round 5`（小食）和 `round 6`（饮料）的 `quantity=2`，即各需选 2 份。

实测结果：

| 传法 | 返回 price | 说明 |
|---|---|---|
| 只传 round 1-4 | 11700（117 元） | 退回原价 |
| round 5/6 各传 1 个商品（quantity=1） | `data` 为空 | 直接无返回 |
| round 5/6 各传 1 个商品（quantity=2） | 11700 | 退回原价 |
| round 5/6 各传 2 个不同商品 | 11800 | 原价 +100 分，仍非套餐价 |

而该套餐的现价是 **49.90 元**，原价 117 元。

**结论**：对含多份槽位的套餐，`roundList` 替换根本不生效，服务端会退回按原价/单品价计算，
且返回 `success: true`、`code: 200`——从响应上看不出任何异常。

若直接采信这个价格，会得出"八件套要 117 元"的荒谬结论。

**本项目的处理**：检测到 `round.quantity > 1` 时，跳过官方核价并**显式标注"价格未经官方验证"**，
而不是假装自己算对了。三件套/四件套（全部 round quantity=1）不受影响，实测核价一致率 100%。

> 这也说明一件事：MCP 的 `success` 字段只代表"请求成功"，不代表"业务结果正确"。
> 任何自动化流程都必须对返回值的**合理性**做校验，不能只看状态码。

---

## 5. 一个必须坚持的原则：缺数据就剔除，不填 0

实测中，`choices` 里的 4 个新品（如「月下相思扭扭薯」）在营养表中不存在。

若把缺失值按 0 计入，这些组合会在**「低热量排行榜」上虚假登顶**——因为热量被算成了 0。
这会直接输出错误结论，而且错得非常隐蔽。

本项目的处理：**缺营养数据的餐品直接排除出组合空间**，并在输出里明确告警。
代价是组合数从 154 降到 84，但结论可信。宁可少算，不能算错。

---

## 6. 业务价值

麦当劳官方给出的套餐默认搭配，是一个**未经优化的中间解**。

以「麦辣鸡腿汉堡三件套」为例（真实核价数据）：

| | 实付 | 热量 | 蛋白质 |
|---|---|---|---|
| 默认搭配（中薯条 + 可乐中杯） | 33.50 元 | 921 kcal | 28.0 g |
| 拐点方案（大薯条 + 纯牛奶） | 36.00 元 | 993 kcal | 37.0 g |

而同一份套餐的 154 种合法组合，实付区间从 **32.00 元到 47.00 元，相差 15 元**。

这个价差不是隐藏优惠，而是**一直摆在菜单上、但人脑无法穷举的选择空间**。
`calculate-price` 的存在，让这个空间第一次变得可计算。

---

## 7. 可复现性

```bash
export MCD_MCP_TOKEN="<your_token>"
python src/probe.py                              # dump 全部 35 个 tool schema
python src/explore.py                            # 全链路探测，产物落 samples/
python src/solve.py --meal 9900005456            # 求解（抽样 12 个官方核价校验）
python src/solve.py --meal 9900005456 --full-verify   # 全量官方核价
python src/pareto.py                             # 纯离线算法自测，无需 token
```

核价结果缓存在 `samples/price_cache.json`，同一组合不重复请求。
