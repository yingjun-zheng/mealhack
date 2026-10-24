"""把求解结果 data.json 生成为自包含的可视化网页。

产物 docs/index.html 是单文件：数据内嵌、零外部依赖、零 CDN，
双击即可打开，也可直接用 GitHub Pages 托管。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MealHack · 套餐黑客</title>
<style>
:root{
  --bg:#0d1117; --panel:#161b22; --panel2:#1c2128; --border:#30363d;
  --text:#e6edf3; --muted:#8b949e; --dim:#484f58;
  --red:#f85149; --green:#3fb950; --blue:#58a6ff; --amber:#d29922;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{
  background:var(--bg); color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif;
  line-height:1.6; padding:32px 20px 64px;
}
.wrap{max-width:1180px;margin:0 auto}
.top{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;margin-bottom:6px}
h1{font-size:22px;font-weight:600;letter-spacing:-.01em}
.tag{font-size:12px;color:var(--muted);border:.5px solid var(--border);border-radius:999px;padding:2px 9px}
.sub{color:var(--muted);font-size:13px;margin-bottom:26px}
.sub b{color:var(--text);font-weight:500}

.hero{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:22px}
.hero-card{background:var(--panel);border:.5px solid var(--border);border-radius:12px;padding:16px 18px}
.hero-card.hot{border-color:#5a2d2d;background:linear-gradient(180deg,#1f1517,#161b22)}
.hero-label{font-size:12px;color:var(--muted);margin-bottom:6px}
.hero-num{font-size:26px;font-weight:600;font-family:var(--mono);letter-spacing:-.02em}
.hero-num.red{color:var(--red)} .hero-num.green{color:var(--green)}
.hero-text{font-size:13px;color:var(--muted);margin-top:4px}

.grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(0,1fr);gap:14px;margin-bottom:22px}
.panel{background:var(--panel);border:.5px solid var(--border);border-radius:12px;padding:16px 18px}
.panel h2{font-size:14px;font-weight:500;margin-bottom:2px}
.panel .note{font-size:12px;color:var(--muted);margin-bottom:12px}
.chart-box{position:relative;height:400px}
canvas{width:100%;height:100%;display:block;cursor:crosshair}
#tip{
  position:absolute;pointer-events:none;background:#0d1117;border:.5px solid var(--border);
  border-radius:8px;padding:8px 10px;font-size:12px;display:none;z-index:9;
  box-shadow:0 8px 24px rgba(0,0,0,.5);max-width:280px;line-height:1.5;
}
#tip .tl{color:var(--text);font-weight:500;margin-bottom:3px}
#tip .tv{color:var(--muted);font-family:var(--mono);font-size:11px}

table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:500;color:var(--muted);font-size:12px;padding:7px 8px;border-bottom:.5px solid var(--border)}
td{padding:7px 8px;border-bottom:.5px solid #21262d;font-family:var(--mono);font-size:12px}
td:first-child{font-family:inherit;font-size:13px;color:var(--text)}
tr:last-child td{border-bottom:none}
tr.hl td{background:#1a2028}
tr.hl td:first-child{color:var(--red)}
.num{text-align:right}

.cmp{margin-bottom:16px}
.cmp-row{display:flex;justify-content:space-between;padding:7px 0;border-bottom:.5px solid #21262d;font-size:13px}
.cmp-row:last-child{border-bottom:none}
.cmp-row span:first-child{color:var(--muted)}
.cmp-row span:last-child{font-family:var(--mono)}
.delta{color:var(--green);font-family:var(--mono)}

.bar-row{margin-bottom:9px}
.bar-head{display:flex;justify-content:space-between;font-size:12px;margin-bottom:4px}
.bar-head .lb{color:var(--muted);font-family:var(--mono)}
.bar-head .vl{font-family:var(--mono);font-weight:500}
.bar-track{height:7px;background:#21262d;border-radius:4px;overflow:hidden}
.bar-fill{height:100%;border-radius:4px;background:var(--blue)}
.bar-fill.bad{background:var(--amber)}
.bar-fill.best{background:var(--green)}

.warn{margin-top:14px;background:#1f1a12;border:.5px solid #3d3018;border-radius:8px;padding:10px 12px;font-size:12px;color:#c9a961;line-height:1.6}
.foot{margin-top:26px;font-size:12px;color:var(--dim);line-height:1.8}
.foot b{color:var(--muted);font-weight:400}
.badge{display:inline-block;font-size:11px;padding:1px 7px;border-radius:999px;border:.5px solid var(--border);color:var(--muted);margin-left:6px;vertical-align:middle}
.badge.ok{color:var(--green);border-color:#1f5130}
.badge.no{color:var(--amber);border-color:#3d3018}

@media(max-width:860px){
  .hero,.grid{grid-template-columns:1fr}
  .chart-box{height:320px}
  body{padding:20px 14px 48px}
}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <h1>MealHack · 套餐黑客</h1>
    <span class="tag">麦当劳 MCP</span>
    <span class="tag" id="tagVerify"></span>
  </div>
  <div class="sub" id="subline"></div>

  <div class="hero">
    <div class="hero-card">
      <div class="hero-label">最划算的一段加价</div>
      <div class="hero-num green" id="bestNum"></div>
      <div class="hero-text" id="bestText"></div>
    </div>
    <div class="hero-card hot">
      <div class="hero-label">最不划算的一段加价</div>
      <div class="hero-num red" id="worstNum"></div>
      <div class="hero-text" id="worstText"></div>
    </div>
  </div>

  <div class="grid">
    <div class="panel">
      <h2>帕累托前沿：价格 vs 蛋白质</h2>
      <div class="note">
        <span style="color:#58a6ff">●</span> 帕累托前沿 &nbsp;
        <span style="color:#e6edf3">○</span> 默认搭配 &nbsp;
        <span style="color:#f85149">●</span> 拐点 &nbsp;
        <span style="color:#484f58">●</span> 其他组合
        <br>每一个蓝点都无法被「更便宜且蛋白更高」的方案替代。悬停查看具体组合。
      </div>
      <div class="chart-box">
        <canvas id="scatter"></canvas>
        <div id="tip"></div>
      </div>
    </div>

    <div class="panel">
      <h2>默认搭配 vs 拐点方案</h2>
      <div class="note">官方给你的默认选择，通常不是最优解。</div>
      <div class="cmp" id="cmp"></div>
      <h2 style="margin-top:18px">每多花 1 元，能换多少蛋白质</h2>
      <div class="note">断崖出现的位置，就是不该再往上加钱的位置。</div>
      <div id="bars"></div>
    </div>
  </div>

  <div class="panel">
    <h2>全部组合</h2>
    <div class="note" id="tableNote"></div>
    <div style="max-height:420px;overflow:auto">
      <table>
        <thead><tr>
          <th>组合</th><th class="num">实付</th><th class="num">热量</th>
          <th class="num">蛋白质</th><th class="num">脂肪</th><th class="num">钠</th>
          <th class="num">蛋白/元</th>
        </tr></thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
  </div>

  <div class="warn" id="warnBox" style="display:none"></div>

  <div class="foot">
    数据来自麦当劳官方 MCP 的 <b>calculate-price</b> 实时核价，非估算。本项目不调用下单接口，不产生任何真实订单。<br>
    参赛作品，由参赛者独立开发，非麦当劳官方产品。价格随门店、时段与优惠券变化，结论仅对当次核价有效。
  </div>
</div>

<script>
const DATA = __DATA__;

const fmt = n => n.toFixed(2);
const $ = id => document.getElementById(id);

const meal = DATA.meal, meta = DATA.meta, combos = DATA.combos;
const def = combos[DATA.default_index];
const knee = DATA.knee_index !== null ? combos[DATA.knee_index] : null;

/* ---------- 顶部信息 ---------- */
$('subline').innerHTML =
  `<b>${meal.name}</b> · 现价 <b>${fmt(meal.base_price)} 元</b> · ` +
  `${DATA.store.name} · 枚举 <b>${meta.combination_count}</b> 种合法组合`;

const vt = $('tagVerify');
if (meta.price_verified) {
  vt.className = 'tag ok';
  vt.textContent = `核价一致率 ${meta.verify_rate}%`;
} else {
  vt.className = 'tag no';
  vt.textContent = '价格未经验证';
}

/* ---------- 边际收益：最好的一段 / 最差的一段 ---------- */
const marg = DATA.marginal || [];
if (marg.length) {
  const best = marg.reduce((a,b) => b.per_yuan > a.per_yuan ? b : a);
  const worst = marg.reduce((a,b) => b.per_yuan < a.per_yuan ? b : a);
  $('bestNum').textContent = `+${fmt(best.per_yuan)} g/元`;
  $('bestText').textContent =
    `${fmt(best.from_price)} → ${fmt(best.to_price)} 元，多花 ${fmt(best.delta_price)} 元换来 ${best.delta_protein} g 蛋白`;
  $('worstNum').textContent = `${fmt(worst.per_yuan)} g/元`;
  $('worstText').textContent =
    `${fmt(worst.from_price)} → ${fmt(worst.to_price)} 元，多花 ${fmt(worst.delta_price)} 元只换来 ${worst.delta_protein} g 蛋白`;
}

/* ---------- 对比 ---------- */
if (knee) {
  const rows = [
    ['实付', fmt(def.price), fmt(knee.price)],
    ['热量', def.kcal + ' kcal', knee.kcal + ' kcal'],
    ['蛋白质', def.protein + ' g', knee.protein + ' g'],
    ['脂肪', def.fat + ' g', knee.fat + ' g'],
    ['钠', def.sodium + ' mg', knee.sodium + ' mg'],
  ];
  $('cmp').innerHTML = rows.map(([k,a,b]) => {
    const changed = a !== b;
    return `<div class="cmp-row"><span>${k}</span><span>${a} <span style="color:var(--dim)">→</span> <b style="color:${changed?'var(--red)':'var(--text)'}">${b}</b></span></div>`;
  }).join('') +
  `<div class="cmp-row" style="margin-top:6px;padding-top:9px;border-top:.5px solid var(--border)">
     <span style="color:var(--text)">差额</span>
     <span class="delta">${(knee.price-def.price)>=0?'+':''}${fmt(knee.price-def.price)} 元 /
     ${(knee.protein-def.protein)>=0?'+':''}${(knee.protein-def.protein).toFixed(1)} g 蛋白</span>
   </div>`;
}

/* ---------- 边际收益条 ---------- */
const maxPer = Math.max(...marg.map(m => m.per_yuan), 0.001);
const minPer = Math.min(...marg.map(m => m.per_yuan));
$('bars').innerHTML = marg.map(m => {
  const w = Math.max(3, (m.per_yuan / maxPer) * 100);
  const cls = Math.abs(m.per_yuan - minPer) < 1e-9 ? 'bad' : (m.per_yuan === maxPer ? 'best' : '');
  return `<div class="bar-row">
    <div class="bar-head">
      <span class="lb">${fmt(m.from_price)} → ${fmt(m.to_price)}</span>
      <span class="vl">${m.per_yuan.toFixed(2)}</span>
    </div>
    <div class="bar-track"><div class="bar-fill ${cls}" style="width:${w}%"></div></div>
  </div>`;
}).join('');

/* ---------- 表格 ---------- */
const sorted = combos.map((c,i)=>({c,i})).sort((a,b)=> b.c.protein - a.c.protein);
$('tableNote').textContent = `按蛋白质从高到低排列；红色行为拐点方案。`;
$('tbody').innerHTML = sorted.map(({c,i}) => {
  const hl = i === DATA.knee_index ? ' class="hl"' : '';
  return `<tr${hl}>
    <td>${c.label}</td>
    <td class="num">${fmt(c.price)}</td>
    <td class="num">${c.kcal}</td>
    <td class="num">${c.protein}</td>
    <td class="num">${c.fat}</td>
    <td class="num">${c.sodium}</td>
    <td class="num">${(c.protein/c.price).toFixed(2)}</td>
  </tr>`;
}).join('');

/* ---------- 警告 ---------- */
if (DATA.warnings && DATA.warnings.length) {
  const box = $('warnBox');
  box.style.display = 'block';
  box.innerHTML = '<b style="color:#c9a961">数据说明</b><br>' + DATA.warnings.map(w => '· ' + w).join('<br>');
}

/* ---------- 散点图 ---------- */
(function(){
  const cv = $('scatter'), tip = $('tip');
  const pad = {t: 22, r: 26, b: 46, l: 58};
  let W=0, H=0, X, Y, pts=[], hover=null;

  function resize(){
    const box = cv.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    W = box.width; H = box.height;
    cv.width = W * dpr; cv.height = H * dpr;
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr,0,0,dpr,0,0);
    draw();
  }

  function scales(){
    const ps = combos.map(c=>c.price), vs = combos.map(c=>c.protein);
    let x0 = Math.min(...ps), x1 = Math.max(...ps);
    let y0 = Math.min(...vs), y1 = Math.max(...vs);
    const px = (x1-x0)*0.05 || 1, py = (y1-y0)*0.10 || 1;
    x0 -= px; x1 += px; y0 -= py; y1 += py;
    X = p => pad.l + (p - x0)/(x1-x0) * (W - pad.l - pad.r);
    Y = v => H - pad.b - (v - y0)/(y1-y0) * (H - pad.t - pad.b);
    return {x0,x1,y0,y1};
  }

  function draw(){
    const ctx = cv.getContext('2d');
    ctx.clearRect(0,0,W,H);
    const r = scales();

    ctx.font = '11px ui-monospace,monospace';
    ctx.strokeStyle = '#21262d'; ctx.fillStyle = '#8b949e'; ctx.lineWidth = 1;

    const yTicks = 5;
    for(let i=0;i<=yTicks;i++){
      const v = r.y0 + (r.y1-r.y0)*i/yTicks;
      const y = Y(v);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W-pad.r, y); ctx.stroke();
      ctx.textAlign='right'; ctx.textBaseline='middle';
      ctx.fillText(v.toFixed(0), pad.l-9, y);
    }
    const xTicks = 5;
    for(let i=0;i<=xTicks;i++){
      const p = r.x0 + (r.x1-r.x0)*i/xTicks;
      const x = X(p);
      ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, H-pad.b); ctx.stroke();
      ctx.textAlign='center'; ctx.textBaseline='top';
      ctx.fillText(p.toFixed(0), x, H-pad.b+9);
    }

    ctx.fillStyle='#6e7681'; ctx.textAlign='center'; ctx.textBaseline='bottom';
    ctx.font='12px -apple-system,sans-serif';
    ctx.fillText('实付价格（元）', pad.l + (W-pad.l-pad.r)/2, H-10);
    ctx.save();
    ctx.translate(14, pad.t + (H-pad.t-pad.b)/2);
    ctx.rotate(-Math.PI/2);
    ctx.textBaseline='middle';
    ctx.fillText('蛋白质（g）', 0, 0);
    ctx.restore();

    pts = combos.map((c,i)=>({x:X(c.price), y:Y(c.protein), c, i}));

    pts.forEach(p=>{
      if (DATA.front_indices.includes(p.i)) return;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 2.6, 0, Math.PI*2);
      ctx.fillStyle = 'rgba(139,148,158,.26)';
      ctx.fill();
    });

    const front = DATA.front_indices.map(i=>pts[i]).sort((a,b)=>a.c.price-b.c.price);
    if (front.length > 1){
      ctx.beginPath();
      front.forEach((p,k)=> k? ctx.lineTo(p.x,p.y) : ctx.moveTo(p.x,p.y));
      ctx.strokeStyle='rgba(88,166,255,.55)'; ctx.lineWidth=1.6; ctx.stroke();
    }
    front.forEach(p=>{
      ctx.beginPath();
      ctx.arc(p.x, p.y, 4.2, 0, Math.PI*2);
      ctx.fillStyle='#58a6ff'; ctx.fill();
      ctx.strokeStyle='#0d1117'; ctx.lineWidth=1.5; ctx.stroke();
    });

    const dp = pts[DATA.default_index];
    if (dp){
      ctx.beginPath();
      ctx.arc(dp.x, dp.y, 7, 0, Math.PI*2);
      ctx.strokeStyle='#e6edf3'; ctx.lineWidth=1.8; ctx.stroke();
      ctx.fillStyle='#e6edf3'; ctx.font='11px -apple-system,sans-serif';
      ctx.textAlign='left'; ctx.textBaseline='bottom';
      ctx.fillText('默认搭配', dp.x+10, dp.y-4);
    }

    const kp = DATA.knee_index !== null ? pts[DATA.knee_index] : null;
    if (kp){
      ctx.beginPath();
      ctx.arc(kp.x, kp.y, 7, 0, Math.PI*2);
      ctx.fillStyle='#f85149'; ctx.fill();
      ctx.strokeStyle='#0d1117'; ctx.lineWidth=2; ctx.stroke();
      ctx.fillStyle='#f85149'; ctx.font='600 11px -apple-system,sans-serif';
      ctx.textAlign='left'; ctx.textBaseline='bottom';
      ctx.fillText('拐点', kp.x+10, kp.y-6);
    }

    if (hover){
      ctx.beginPath();
      ctx.arc(hover.x, hover.y, 6, 0, Math.PI*2);
      ctx.strokeStyle='#58a6ff'; ctx.lineWidth=1.6; ctx.stroke();
    }
  }

  cv.addEventListener('mousemove', e=>{
    const rect = cv.getBoundingClientRect();
    const mx = e.clientX-rect.left, my = e.clientY-rect.top;
    let near=null, nd=12*12;
    pts.forEach(p=>{
      const d=(p.x-mx)**2+(p.y-my)**2;
      if(d<nd){nd=d;near=p;}
    });
    if (near !== hover){ hover=near; draw(); }
    if (near){
      tip.style.display='block';
      tip.innerHTML = `<div class="tl">${near.c.label}</div>
        <div class="tv">${fmt(near.c.price)} 元 · ${near.c.kcal} kcal · 蛋白 ${near.c.protein} g · 钠 ${near.c.sodium} mg</div>`;
      const tx = Math.min(mx+14, W-250), ty = Math.max(my-10, 4);
      tip.style.left = tx+'px'; tip.style.top = ty+'px';
    } else {
      tip.style.display='none';
    }
  });
  cv.addEventListener('mouseleave', ()=>{ hover=null; draw(); tip.style.display='none'; });

  window.addEventListener('resize', resize);
  resize();
})();
</script>
</body>
</html>
"""


def main() -> int:
    data_path = ROOT / "docs" / "data.json"
    if not data_path.exists():
        print(f"缺少 {data_path}，先运行：python src/solve.py --meal <code> --json docs/data.json")
        return 1

    data = json.loads(data_path.read_text(encoding="utf-8"))
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # 防止 JSON 中的 </script> 提前闭合脚本块
    payload = payload.replace("</", "<\\/")

    html = TEMPLATE.replace("__DATA__", payload)
    out = ROOT / "docs" / "index.html"
    out.write_text(html, encoding="utf-8")
    print(f"已生成 {out}  ({len(html):,} 字符)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
