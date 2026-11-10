#!/usr/bin/env node
// GSC（或必应）查询表的整理工具。无依赖，Node 18+。方法见 references/query-analysis.md。
//
// 做什么：
//   1. 按规则给每个查询标「数据质量、品牌、意图大类 / 细类、属性、长查询」；
//   2. 算可见查询覆盖率、品牌点击、非品牌点击的两种口径；
//   3. 按意图透视（已剔除垃圾、机器查询；模板长问句单列）；
//   4. 列机会词（排名 4–20）和展示高、点击低的词；
//   5. 输出打好标签的 CSV，供人工抽查和改标签。
//
// 用法：
//   node gsc_queries.mjs <查询表.csv> --rules <规则.json>
//        [--total-clicks N] [--total-impressions N]   图表上的总数（含匿名查询），不给就不算覆盖率和总量口径
//        [--out 标签表.csv] [--top 20] [--min-impr 100]
//
// 输入：GSC 效果报告「查询」表导出的 CSV（中文或英文界面都行），必应搜索效果的关键词导出也可以，
//       只要有「查询 / 点击 / 展示 / 排名」几列。
// 规则：格式见 assets/gsc-rules.example.json。正则按 GSC 的默认行为模拟：部分匹配、不区分大小写。
//       GSC 用 RE2 语法，不支持向前看、向后看和反向引用；规则里出现这些写法时，脚本会提醒。
//
// 注意：脚本只做机械归类。规则会分错，结果要按展示从高到低抽查前 50 个，再改规则重跑。
//       输出里的数字都是「表格可见部分」，不是全站总数；总数以图表为准。

import fs from 'node:fs';

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const k = a.slice(2);
      const v = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : true;
      out[k] = v;
    } else out._.push(a);
  }
  return out;
}

function parseCsv(text) {
  text = text.replace(/^﻿/, '');
  const rows = [];
  let row = [], field = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else q = false;
      } else field += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); field = '';
      if (row.some((x) => x.trim() !== '')) rows.push(row);
      row = [];
    } else field += c;
  }
  if (field !== '' || row.length) { row.push(field); if (row.some((x) => x.trim() !== '')) rows.push(row); }
  return rows;
}

function num(s) {
  if (s == null) return NaN;
  const t = String(s).trim().replace(/[,\s]/g, '').replace(/%$/, '');
  return t === '' ? NaN : Number(t);
}

function findCol(header, patterns) {
  for (const p of patterns) {
    const i = header.findIndex((h) => p.test(h.trim()));
    if (i >= 0) return i;
  }
  return -1;
}

const RE2_UNSUPPORTED = /\(\?<?[=!]|\\[1-9]/;
function compile(src, where) {
  if (!src) return null;
  if (RE2_UNSUPPORTED.test(src)) {
    console.error(`提醒：${where} 用了 RE2 不支持的写法（向前 / 向后看或反向引用），在 GSC 界面里会报错：${src}`);
  }
  try { return new RegExp(src, 'i'); } catch (e) {
    console.error(`规则写错了（${where}）：${src}\n${e.message}`); process.exit(2);
  }
}
const list = (x) => (Array.isArray(x) ? x : x ? [x] : []);

function fmt(n, d = 0) {
  if (!Number.isFinite(n)) return '—';
  return n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
const pct = (a, b) => (b > 0 ? `${fmt((a / b) * 100, 1)}%` : '—');
const csvCell = (v) => {
  const s = v == null ? '' : String(v);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

function main() {
  const args = parseArgs(process.argv.slice(2));
  const file = args._[0];
  if (!file || !args.rules) {
    console.error('用法：node gsc_queries.mjs <查询表.csv> --rules <规则.json> [--total-clicks N] [--total-impressions N] [--out 标签表.csv] [--top 20] [--min-impr 100]');
    process.exit(1);
  }
  const rules = JSON.parse(fs.readFileSync(args.rules, 'utf8'));
  const R = {
    brand: compile(rules.brand, 'brand'),
    spam: list(rules.spam).map((s, i) => compile(s, `spam[${i}]`)),
    machine: list(rules.machine).map((s, i) => compile(s, `machine[${i}]`)),
    template: list(rules.templateQuestion).map((s, i) => compile(s, `templateQuestion[${i}]`)),
    long: compile(rules.longQuery || '^(\\S+\\s+){5}\\S+', 'longQuery'),
    intent: list(rules.intent).map((r, i) => ({ ...r, rx: compile(r.re, `intent[${i}] ${r.major}/${r.minor}`) })),
    attr: list(rules.attributes).map((r, i) => ({ ...r, rx: compile(r.re, `attributes[${i}] ${r.label}`) })),
  };

  const rows = parseCsv(fs.readFileSync(file, 'utf8'));
  if (rows.length < 2) { console.error('文件里没有数据行'); process.exit(1); }
  const header = rows[0];
  const ci = {
    q: findCol(header, [/^(top queries|queries|query|keyword|keywords)$/i, /热门查询|查询|关键字|关键词/]),
    clicks: findCol(header, [/^clicks$/i, /点击次数|^点击$/]),
    impr: findCol(header, [/^impressions$/i, /展示|曝光|展现/]),
    pos: findCol(header, [/^(position|avg\.? position|average position)$/i, /排名/]),
  };
  if (ci.q < 0) ci.q = 0;
  if (ci.clicks < 0 || ci.impr < 0) {
    console.error(`认不出点击或展示列。表头是：${header.join(' | ')}`); process.exit(1);
  }

  const data = rows.slice(1).map((r) => {
    const q = (r[ci.q] || '').trim();
    const clicks = num(r[ci.clicks]) || 0;
    const impr = num(r[ci.impr]) || 0;
    const pos = ci.pos >= 0 ? num(r[ci.pos]) : NaN;
    let quality = '正常';
    if (R.spam.some((x) => x.test(q))) quality = '垃圾';
    else if (R.machine.some((x) => x.test(q))) quality = '机器';
    else if (R.template.some((x) => x.test(q))) quality = '模板长问句';
    const brand = !!(R.brand && R.brand.test(q));
    let major = '', minor = '';
    if (quality === '正常' || quality === '模板长问句') {
      if (brand) { major = '导航'; minor = '品牌'; }
      else {
        const hit = R.intent.find((x) => x.rx.test(q));
        if (hit) { major = hit.major; minor = hit.minor || ''; }
        else { major = '未带意图修饰'; minor = '待看结果页'; }
      }
    }
    const attrs = R.attr.filter((x) => x.rx.test(q)).map((x) => x.label);
    if (brand && !attrs.includes('品牌')) attrs.unshift('品牌');
    const isLong = R.long.test(q);
    return { q, clicks, impr, pos, quality, brand, major, minor, attrs, isLong };
  }).filter((d) => d.q);

  const sum = (arr, k) => arr.reduce((s, d) => s + d[k], 0);
  const vis = { clicks: sum(data, 'clicks'), impr: sum(data, 'impr') };
  const spam = data.filter((d) => d.quality === '垃圾');
  const machine = data.filter((d) => d.quality === '机器');
  const tmpl = data.filter((d) => d.quality === '模板长问句');
  const brandRows = data.filter((d) => d.brand && (d.quality === '正常' || d.quality === '模板长问句'));
  const noise = { clicks: sum(spam, 'clicks') + sum(machine, 'clicks'), impr: sum(spam, 'impr') + sum(machine, 'impr') };
  const brandC = sum(brandRows, 'clicks');
  const totalC = args['total-clicks'] != null ? num(args['total-clicks']) : NaN;
  const totalI = args['total-impressions'] != null ? num(args['total-impressions']) : NaN;
  const top = Number(args.top) || 20;
  const minImpr = Number(args['min-impr']) || 100;

  const L = [];
  L.push('## 查询表整理结果');
  L.push('');
  L.push(`- 文件：${file}；数据行：${data.length}`);
  L.push(`- 表格可见合计：点击 ${fmt(vis.clicks)}，展示 ${fmt(vis.impr)}（这是可见部分，不是总数）`);
  if (Number.isFinite(totalC)) {
    L.push(`- 图表总点击 ${fmt(totalC)} → 可见查询覆盖 ${pct(vis.clicks, totalC)} 的点击`);
  } else L.push('- 没给 --total-clicks：无法算可见查询覆盖率和非品牌总量口径，报告里写「未计算」');
  if (Number.isFinite(totalI)) L.push(`- 图表总展示 ${fmt(totalI)} → 可见查询覆盖 ${pct(vis.impr, totalI)} 的展示；剔除垃圾、机器查询后的总展示 = ${fmt(totalI - noise.impr)}`);
  L.push(`- 品牌词：${brandRows.length} 个查询，点击 ${fmt(brandC)}，展示 ${fmt(sum(brandRows, 'impr'))}（规则：${rules.brand || '未设置'}）`);
  L.push(`- 垃圾查询：${spam.length} 个，点击 ${fmt(sum(spam, 'clicks'))}，展示 ${fmt(sum(spam, 'impr'))}`);
  L.push(`- 机器查询：${machine.length} 个，点击 ${fmt(sum(machine, 'clicks'))}，展示 ${fmt(sum(machine, 'impr'))}`);
  L.push(`- 模板长问句（单列，不和真人查询混算，也不算 AI 引用）：${tmpl.length} 个，点击 ${fmt(sum(tmpl, 'clicks'))}，展示 ${fmt(sum(tmpl, 'impr'))}`);
  const nbVisible = vis.clicks - brandC - noise.clicks;
  if (Number.isFinite(totalC)) {
    L.push(`- 非品牌点击 · 总量口径（上限）= ${fmt(totalC)} − 品牌 ${fmt(brandC)} − 垃圾和机器 ${fmt(noise.clicks)} = ${fmt(totalC - brandC - noise.clicks)}`);
  }
  L.push(`- 非品牌点击 · 可识别口径（下限）= 可见 ${fmt(vis.clicks)} − 品牌 ${fmt(brandC)} − 垃圾和机器 ${fmt(noise.clicks)} = ${fmt(nbVisible)}`);
  L.push('');

  // 透视
  const clean = data.filter((d) => d.quality === '正常');
  const groups = new Map();
  for (const d of clean) {
    const k = `${d.major}|${d.minor}`;
    const g = groups.get(k) || { major: d.major, minor: d.minor, n: 0, clicks: 0, impr: 0 };
    g.n++; g.clicks += d.clicks; g.impr += d.impr; groups.set(k, g);
  }
  L.push('### 按意图透视（已剔除垃圾、机器查询；模板长问句不在内）');
  L.push('');
  L.push('| 意图大类 | 细类 | 查询数 | 展示 | 点击 | 点击率 |');
  L.push('|---|---|---:|---:|---:|---:|');
  for (const g of [...groups.values()].sort((a, b) => b.impr - a.impr)) {
    L.push(`| ${g.major} | ${g.minor} | ${g.n} | ${fmt(g.impr)} | ${fmt(g.clicks)} | ${pct(g.clicks, g.impr)} |`);
  }
  L.push('');

  const opp = clean.filter((d) => !d.brand && d.pos >= 4 && d.pos <= 20).sort((a, b) => b.impr - a.impr).slice(0, top);
  L.push(`### 机会词：排名 4–20（非品牌，按展示排序，前 ${top}）`);
  L.push('');
  L.push('| 查询 | 意图 | 展示 | 点击 | 排名 |');
  L.push('|---|---|---:|---:|---:|');
  for (const d of opp) L.push(`| ${d.q} | ${d.major}${d.minor ? ' · ' + d.minor : ''} | ${fmt(d.impr)} | ${fmt(d.clicks)} | ${fmt(d.pos, 1)} |`);
  L.push('');

  const cleanCtr = sum(clean, 'impr') > 0 ? sum(clean, 'clicks') / sum(clean, 'impr') : 0;
  const lowCtr = clean.filter((d) => !d.brand && d.impr >= minImpr && d.clicks / d.impr < cleanCtr / 2).sort((a, b) => b.impr - a.impr).slice(0, top);
  L.push(`### 展示高、点击低（展示 ≥ ${minImpr}，点击率低于正常查询平均点击率 ${fmt(cleanCtr * 100, 2)}% 的一半）`);
  L.push('');
  L.push('| 查询 | 意图 | 展示 | 点击 | 排名 |');
  L.push('|---|---|---:|---:|---:|');
  for (const d of lowCtr) L.push(`| ${d.q} | ${d.major}${d.minor ? ' · ' + d.minor : ''} | ${fmt(d.impr)} | ${fmt(d.clicks)} | ${fmt(d.pos, 1)} |`);
  L.push('');

  const longUnreviewed = data.filter((d) => d.isLong && d.quality === '正常');
  if (longUnreviewed.length) {
    L.push(`### 6 个词以上、还没归类的长查询：${longUnreviewed.length} 个，展示 ${fmt(sum(longUnreviewed, 'impr'))}`);
    L.push('逐个看：句式模板化、只换产品名的，归「模板长问句」（补进 templateQuestion 规则）；真人长尾词保留。');
    L.push('');
  }
  L.push('提醒：样本小的格子（展示几十次以内）只看大概；排名看 8–12 周（经验值），单周只记录、不下结论。');

  console.log(L.join('\n'));

  if (args.out) {
    const head = ['查询', '点击', '展示', '点击率', '排名', '数据质量', '品牌', '意图大类', '意图细类', '属性', '长查询', '承接页', '备注'];
    const lines = [head.join(',')];
    for (const d of [...data].sort((a, b) => b.impr - a.impr)) {
      lines.push([
        d.q, d.clicks, d.impr, d.impr ? ((d.clicks / d.impr) * 100).toFixed(2) + '%' : '',
        Number.isFinite(d.pos) ? d.pos : '', d.quality, d.brand ? '是' : '', d.major, d.minor,
        d.attrs.join('、'), d.isLong ? '是' : '', '', '',
      ].map(csvCell).join(','));
    }
    fs.writeFileSync(args.out, '﻿' + lines.join('\r\n'), 'utf8');
    console.error(`已写出标签表：${args.out}`);
  }
}

main();
