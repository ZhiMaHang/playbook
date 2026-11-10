#!/usr/bin/env node
// 关键词表工具。无依赖，Node 18+。方法见 references/cleaning-tagging.md、references/deliverables.md。
//
// 用法：
//   node kw_table.mjs prep     <候选词.csv> [--exclude 剔除词.txt] [--brand 品牌1,品牌2] [--out 输出.csv]
//   node kw_table.mjs check    <关键词研究表.csv> [--out 补好总分的表.csv]
//   node kw_table.mjs crosstab <已打标签的查询.csv> [--impr 展示列名] [--clicks 点击列名]
//
// prep：合并去重（大小写、全半角、空格、连字符、单复数），不相加任何数字；标出疑似剔除词、
//       按修饰词给出意图初分、疑似料号、长查询。输出全部是「待复核」的建议，不替人下结论。
// check：按关键词研究表的固定选项和规则检查，算六项打分的总分，列出错误和提醒。
// crosstab：按「意图大类 × 主属性」汇总展示、点击、点击率（后台查询分类的交叉统计）。
//
// 输入：UTF-8（可带 BOM）或 UTF-16（Google 关键词规划师导出）的 CSV / TSV。输出 CSV 带 BOM，Excel 直接打开。

import fs from 'node:fs';

// ---------- 固定选项（和 assets/keyword-research-table.csv 一致） ----------
const INFO_SUB = ['概念定义', '原理机制', '参数资料', '选型方法', '设计应用', '故障排查', '标准法规', '技术对比', '行业资讯'];
const COMM_SUB = ['产品浏览', '比较挑选', '替代查询', '找厂家', '找供应渠道', '询价', '现货与下单', '样品', '定制'];
const MAJOR = ['信息', '商业', '导航', '未带意图修饰', '待定'];
const SCOPE = ['核心词', '长尾词'];
const HEAT = ['常青', '季节', '突发', '待定'];
const TREND = ['涨', '平', '跌', '未知', '待定'];
const ROLE = ['主题词', '同义叫法', '辅助词', '长尾'];
const VERDICT = ['先做', '排队', '暂缓', '剔除', '待定'];
const PAGE_TYPES = ['首页', '分类页', '系列/选型页', '型号页', '替代对照页', '博客/技术文章', '应用领域页', '关于页', '质量与认证页', '能力页', '询价/样品入口', '新闻/时效文章', '不承接', '待定'];
const SCORE_COLS = ['意图匹配', '业务相关度', '需求证据', '商业信号', '承接能力', '可赢性'];
const REQUIRED = ['关键词', '市场语言', '月搜索量', '搜索量来源', '取数日期', '数据来源', '意图大类', '意图细类', '属性', '范围', '核心词', '热度', '产品线', '主题', '主题内角色', '承接页面类型', ...SCORE_COLS, '总分', '结论'];
const KW_ALIASES = ['关键词', '搜索词', '查询', '热门查询', '搜索字词', 'keyword', 'keywords', 'query', 'queries', 'top queries', 'search term', 'search query'];
const VOL_ALIASES = ['月搜索量', '搜索量', '平均每月搜索量', 'volume', 'search volume', 'avg. monthly searches', '展示', '展现', 'impressions'];
const SRC_ALIASES = ['数据来源', '来源', 'source'];

// ---------- 意图初分规则：只看词面，结果必须人工复核 ----------
const RULES = [
  { major: '商业', sub: '比较挑选', re: /\b(best|top\s*\d*|reviews?|ratings?|ranking)\b|哪家好|哪个牌子|牌子|品牌推荐|排行|排名|推荐/i },
  { major: '商业', sub: '替代查询', re: /\b(equivalents?|alternatives?|replacements?|substitutes?|cross[\s-]?reference|replace)\b|替代|代换|替换|平替/i },
  { major: '商业', sub: '找厂家', re: /\b(manufacturers?|factory|factories|makers?|producers?)\b|厂家|厂商|工厂|生产商|制造商/i },
  { major: '商业', sub: '找供应渠道', re: /\b(suppliers?|distributors?|wholesale|wholesalers?|vendors?|dealers?|exporters?)\b|供应商|代理|经销|批发|分销/i },
  { major: '商业', sub: '询价', re: /\b(price|prices|pricing|cost|quote|quotation|moq)\b|价格|报价|多少钱|起订量/i },
  { major: '商业', sub: '现货与下单', re: /\b(buy|purchase|order|in stock|stock|for sale|shop)\b|现货|库存|购买|下单|哪里买/i },
  { major: '商业', sub: '样品', re: /\b(samples?)\b|样品|送样|试样/i },
  { major: '商业', sub: '定制', re: /\b(custom|customi[sz]ed|oem|odm|bespoke|made to order)\b|定制|订制|代工|非标/i },
  { major: '信息', sub: '参数资料', re: /\b(datasheets?|data sheet|pdf|pinout|pin out|footprint|3d model|cad model|step file|drawing|dimensions?)\b|规格书|引脚|管脚|尺寸图|封装尺寸|手册/i },
  { major: '信息', sub: '概念定义', re: /\b(what is|what are|what's|meaning|definition)\b|是什么|什么是|作用|含义|定义/i },
  { major: '信息', sub: '原理机制', re: /\b(how .*works?|working principle|principle|why)\b|原理|为什么|工作方式/i },
  { major: '信息', sub: '选型方法', re: /\b(how to (choose|select|pick|size)|selection|selecting|choosing|sizing)\b|怎么选|如何选|选型/i },
  { major: '信息', sub: '设计应用', re: /\b(schematic|layout|circuit|reference design|application note)\b|电路|原理图|布局|设计/i },
  { major: '信息', sub: '故障排查', re: /\b(troubleshoot\w*|not working|problems?|issues?|faults?|failures?|leak\w*)\b|故障|不亮|掉线|失效|坏了|异常|漏/i },
  { major: '信息', sub: '标准法规', re: /\b(standards?|requirements?|compliance|regulations?)\b|标准|要求|规范|法规/i },
  { major: '信息', sub: '行业资讯', re: /\b(market|trends?|news|forecast)\b|市场|趋势|行业|新闻/i },
];
const COMPARE = /\b(vs\.?|versus|difference between|differences|compared? (to|with))\b|区别|对比|比较|差异/i;
const QUESTION_EN = /^(what|how|why|which|when|where|who|does|do|should|can|is|are)\b/i;
const QUESTION_ZH = /[吗呢？?]$|怎么|如何|哪些|哪家|哪个|为什么|是否/;
const SPEC_TOKEN = /^(\d+(\.\d+)?(g|m|k|v|kv|w|a|ma|mm|mhz|ghz|gbps|mbps|bar|psi|mpa|dn\d*|pn\d*)?|\d+g?base.*|802\.\w+|cat\d\w?|ip\d\d|iso\d+|iec\d+|dn\d+|pn\d+|\d+\/\d+.*)$/i;

// ---------- CSV ----------
function readText(file) {
  const buf = fs.readFileSync(file);
  if (buf[0] === 0xff && buf[1] === 0xfe) return buf.slice(2).toString('utf16le');
  if (buf[0] === 0xfe && buf[1] === 0xff) { const b = Buffer.from(buf.slice(2)); b.swap16(); return b.toString('utf16le'); }
  let s = buf.toString('utf8');
  if (s.charCodeAt(0) === 0xfeff) s = s.slice(1);
  return s;
}
function parseCsv(text) {
  const first = text.split(/\r?\n/, 1)[0] || '';
  const delim = (first.split('\t').length > first.split(',').length) ? '\t' : ',';
  const rows = []; let row = [], field = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; }
      else field += c;
    } else if (c === '"' && field === '') q = true;
    else if (c === delim) { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); field = '';
      if (row.some(v => v.trim() !== '')) rows.push(row);
      row = [];
    } else field += c;
  }
  row.push(field); if (row.some(v => v.trim() !== '')) rows.push(row);
  const header = (rows.shift() || []).map(h => h.trim());
  return { header, rows: rows.map(r => Object.fromEntries(header.map((h, i) => [h, (r[i] ?? '').trim()]))) };
}
function toCsv(header, rows) {
  const esc = v => { const s = String(v ?? ''); return /[",\r\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
  return '\ufeff' + [header.map(esc).join(','), ...rows.map(r => header.map(h => esc(r[h])).join(','))].join('\r\n') + '\r\n';
}
function findCol(header, aliases) {
  const low = header.map(h => h.toLowerCase());
  for (const a of aliases) { const i = low.indexOf(a.toLowerCase()); if (i >= 0) return header[i]; }
  return null;
}
function num(v) {
  const s = String(v ?? '').replace(/[,\s，]/g, '');
  return /^-?\d+(\.\d+)?$/.test(s) ? Number(s) : null;
}

// ---------- 规范写法 ----------
function display(s) { return s.normalize('NFKC').replace(/\s+/g, ' ').trim().toLowerCase(); }
function singular(w) {
  if (!/^[a-z]{4,}$/.test(w)) return w;
  if (/ies$/.test(w)) return w.slice(0, -3) + 'y';
  if (/(sses|ches|shes|xes)$/.test(w)) return w.slice(0, -2);
  if (/s$/.test(w) && !/(ss|us|is)$/.test(w)) return w.slice(0, -1);
  return w;
}
function groupKey(s) {
  return display(s).split(/[\s\-_]+/).map(singular).join('').replace(/[.·]/g, '');
}
function partNumberTokens(s) {
  return display(s).split(/\s+/).filter(t => {
    const x = t.replace(/[-_]/g, '');
    return x.length >= 5 && /[a-z]/.test(x) && (x.match(/\d/g) || []).length >= 2 && !SPEC_TOKEN.test(t);
  });
}
function wordCount(s) {
  const d = display(s);
  return /[\u4e00-\u9fff]/.test(d) ? null : d.split(' ').filter(Boolean).length;
}

// ---------- prep ----------
function pretag(kw, brands) {
  const d = display(kw);
  if (brands.some(b => b && d.includes(b))) return { major: '导航', sub: '', why: '含本站品牌（品牌词按导航词处理，核对是否同时带商业修饰）' };
  if (COMPARE.test(d)) return { major: '待定', sub: '', why: '比较类：比技术、规格归信息·技术对比；比品牌、供应商、可买产品归商业·比较挑选' };
  const hits = RULES.filter(r => r.re.test(d));
  const majors = [...new Set(hits.map(h => h.major))];
  if (hits.length === 0) {
    if (QUESTION_EN.test(d) || QUESTION_ZH.test(d)) return { major: '信息', sub: '待定', why: '问句，细类看结果页' };
    return { major: '未带意图修饰', sub: '', why: '词面没有意图修饰，看结果页定' };
  }
  if (majors.length > 1) return { major: '待定', sub: '', why: '同时命中：' + hits.map(h => h.sub).join('、') };
  return { major: majors[0], sub: hits[0].sub, why: '命中：' + hits.map(h => h.sub).join('、') };
}
function loadExclude(file) {
  if (!file) return [];
  return readText(file).split(/\r?\n/).map(l => l.trim()).filter(l => l && !l.startsWith('#')).map(display);
}
function prep(file, opt) {
  const { header, rows } = parseCsv(readText(file));
  const kwCol = findCol(header, KW_ALIASES);
  if (!kwCol) throw new Error('找不到关键词列。可用列名：' + KW_ALIASES.join(' / '));
  const volCol = findCol(header, VOL_ALIASES), srcCol = findCol(header, SRC_ALIASES);
  const excl = loadExclude(opt.exclude);
  const brands = (opt.brand || '').split(/[,，]/).map(display).filter(Boolean);
  const groups = new Map();
  for (const r of rows) {
    const raw = r[kwCol]; if (!raw) continue;
    const k = groupKey(raw);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  }
  const out = [];
  for (const members of groups.values()) {
    // 取量最大的写法；量相同时取已是单数、不带连字符的写法（例：LAN Transformers / lan-transformer → lan transformer）
    const tidy = m => { const d = display(m[kwCol]); return d === d.split(" ").map(singular).join(" ") && !/[-_]/.test(d) ? 1 : 0; };
    const rank = m => [volCol ? (num(m[volCol]) ?? -1) : 0, tidy(m), -display(m[kwCol]).length];
    const better = (a, b) => { const x = rank(a), y = rank(b); for (let j = 0; j < 3; j++) if (x[j] !== y[j]) return x[j] > y[j]; return false; };
    const best = members.reduce((a, b) => better(b, a) ? b : a);
    const kw = display(best[kwCol]);
    const variants = members.filter(m => m !== best).map(m => display(m[kwCol]) + (volCol && m[volCol] ? `（${volCol} ${m[volCol]}）` : ''));
    const sources = srcCol ? [...new Set(members.flatMap(m => (m[srcCol] || '').split(/[、,，;；/]/).map(s => s.trim()).filter(Boolean)))] : [];
    const tag = pretag(kw, brands);
    const exHits = excl.filter(f => kw.includes(f));
    const flags = [];
    const pn = partNumberTokens(kw); if (pn.length) flags.push('疑似料号：' + pn.join(' ') + '（按原厂规格书写法改）');
    const wc = wordCount(kw);
    if (wc !== null && wc >= 10 && QUESTION_EN.test(kw)) flags.push('长问句：核对是否 AI 工具检索，单列，不和真人查询混算');
    else if (wc !== null && wc >= 6) flags.push('长查询（6 词以上）：问题集候选');
    if (/[\u4e00-\u9fff]/.test(kw) && kw.length >= 12 && QUESTION_ZH.test(kw)) flags.push('中文长问句：问题集候选');
    out.push({
      ...best,
      '关键词（规范写法候选）': kw,
      '原始写法': best[kwCol],
      '变体（已合并，数字不相加）': variants.join('；'),
      '来源（合并）': sources.join('、'),
      '初分意图大类': tag.major,
      '初分意图细类': tag.sub,
      '初分依据': tag.why,
      '疑似剔除': exHits.length ? '命中：' + exHits.join('、') + '（对照产品目录确认，卖的就保留）' : '',
      '标记': flags.join('；'),
    });
  }
  const extra = ['关键词（规范写法候选）', '原始写法', '变体（已合并，数字不相加）', '来源（合并）', '初分意图大类', '初分意图细类', '初分依据', '疑似剔除', '标记'];
  const outHeader = [...extra, ...header.filter(h => h !== kwCol && h !== srcCol && !extra.includes(h))];
  const csv = toCsv(outHeader, out);
  if (opt.out) fs.writeFileSync(opt.out, csv); else process.stdout.write(csv);
  const cnt = k => out.filter(r => r[k]).length;
  console.error(`读入 ${rows.length} 行 → 合并后 ${out.length} 行；疑似剔除 ${cnt('疑似剔除')} 行；带标记 ${cnt('标记')} 行。` +
    `\n初分只看词面：「未带意图修饰」「待定」和搜索量最大的 20 个词要看结果页再定；剔除前对照产品目录。`);
}

// ---------- check ----------
function check(file, opt) {
  const { header, rows } = parseCsv(readText(file));
  const errors = [], warns = [];
  const E = (i, m) => errors.push(i ? `第 ${i} 行：${m}` : m);
  const W = (i, m) => warns.push(i ? `第 ${i} 行：${m}` : m);
  const missing = REQUIRED.filter(c => !header.includes(c));
  if (missing.length) E(0, '缺少列：' + missing.join('、') + '（表头见 assets/keyword-research-table.csv）');
  for (const h of header) if (/人群|画像|阶段/.test(h)) W(0, `有「${h}」列：不按人群、阶段给词分类，分析结论写进「承接理由」`);
  const seen = new Map(), topics = new Map(), pageTopic = new Map();
  rows.forEach((r, idx) => {
    const i = idx + 2, kw = r['关键词'] || '';
    if (!kw) { E(i, '关键词为空'); return; }
    const k = groupKey(kw);
    if (seen.has(k)) E(i, `「${kw}」和第 ${seen.get(k)} 行是同一个词的变体，应并进一行`); else seen.set(k, i);
    const major = r['意图大类'], sub = r['意图细类'];
    if (!MAJOR.includes(major)) E(i, `意图大类「${major}」不在固定选项：${MAJOR.join(' / ')}`);
    if (sub && sub !== '待定') {
      if (major === '信息' && !INFO_SUB.includes(sub)) E(i, `信息词的细类「${sub}」不在九类里`);
      else if (major === '商业' && !COMM_SUB.includes(sub)) E(i, `商业词的细类「${sub}」不在九类里`);
      else if (!INFO_SUB.includes(sub) && !COMM_SUB.includes(sub)) E(i, `意图细类「${sub}」不在固定选项里`);
    } else if (major === '信息' || major === '商业') W(i, '意图细类未定');
    if (r['范围'] && !SCOPE.includes(r['范围'])) E(i, `范围「${r['范围']}」只能填 核心词 / 长尾词`);
    if (r['范围'] === '长尾词' && !r['核心词']) E(i, '长尾词要写明相对哪个核心词');
    if (r['热度'] && !HEAT.includes(r['热度'])) E(i, `热度「${r['热度']}」只能填 ${HEAT.join(' / ')}`);
    if (r['趋势'] && !TREND.includes(r['趋势'])) E(i, `趋势「${r['趋势']}」只能填 ${TREND.join(' / ')}`);
    const vol = r['月搜索量'];
    if (!vol) E(i, '月搜索量为空：写数字、区间，或写「未知」');
    else if (vol !== '未知') {
      if (!r['搜索量来源']) E(i, '有搜索量但没写工具和市场');
      if (!/^\d{4}-\d{2}(-\d{2})?/.test(r['取数日期'] || '')) E(i, '有搜索量但取数日期缺失或不是 YYYY-MM-DD');
    }
    if ((r['后台展示'] || r['后台排名']) && !r['后台时间范围']) E(i, '有后台展示 / 排名但没写时间范围');
    if (!r['数据来源']) E(i, '数据来源为空');
    if (r['主题内角色'] && !ROLE.includes(r['主题内角色'])) E(i, `主题内角色「${r['主题内角色']}」只能填 ${ROLE.join(' / ')}`);
    if (r['承接页面类型'] && !PAGE_TYPES.includes(r['承接页面类型'])) W(i, `承接页面类型「${r['承接页面类型']}」不在常用选项里`);
    const verdict = r['结论'];
    if (!VERDICT.includes(verdict)) E(i, `结论「${verdict}」只能填 ${VERDICT.join(' / ')}`);
    if (verdict === '剔除') {
      if (!r['承接理由'] && !r['备注']) E(i, '剔除要写理由（不卖，或意图对不上）');
      if (SCORE_COLS.some(c => r[c])) W(i, '一票否决的词不用打分');
    } else if (verdict && verdict !== '待定') {
      const sc = SCORE_COLS.map(c => r[c]);
      if (sc.some(v => !['0', '1', '2'].includes(v))) E(i, '六项打分每项只能填 0 / 1 / 2');
      else {
        const total = sc.reduce((a, b) => a + Number(b), 0);
        if (r['总分'] && Number(r['总分']) !== total) E(i, `总分写的是 ${r['总分']}，六项相加是 ${total}`);
        r['总分'] = String(total);
        const band = total >= 9 ? '先做' : total >= 6 ? '排队' : '暂缓';
        r['建议结论（按分数）'] = band;
        if (band !== verdict) W(i, `总分 ${total} 按阈值是「${band}」，表里写「${verdict}」：改了阈值要在备注写原因`);
      }
      const t = r['主题'];
      if (!t) W(i, '没归到主题');
      else {
        if (!topics.has(t)) topics.set(t, []);
        topics.get(t).push({ i, kw, role: r['主题内角色'] });
        const p = r['现有可承接页面'];
        if (p && p !== '无') { if (!pageTopic.has(p)) pageTopic.set(p, new Set()); pageTopic.get(p).add(t); }
      }
    }
  });
  for (const [t, list] of topics) {
    const heads = list.filter(x => x.role === '主题词');
    if (heads.length === 0) E(0, `主题「${t}」没有主题词`);
    if (heads.length > 1) E(0, `主题「${t}」有 ${heads.length} 个主题词（第 ${heads.map(h => h.i).join('、')} 行），一个主题只留一个`);
  }
  for (const [p, ts] of pageTopic) if (ts.size > 1) W(0, `${[...ts].join('、')} 几个主题都写了现有页面 ${p}：交给 seo-keyword-mapping 核对是不是内耗`);
  if (opt.out) {
    const outHeader = header.includes('建议结论（按分数）') ? header : [...header, '建议结论（按分数）'];
    fs.writeFileSync(opt.out, toCsv(outHeader, rows));
  }
  console.log(`检查 ${rows.length} 行：错误 ${errors.length} 条，提醒 ${warns.length} 条。`);
  if (errors.length) console.log('\n错误：\n' + errors.map(s => '- ' + s).join('\n'));
  if (warns.length) console.log('\n提醒：\n' + warns.map(s => '- ' + s).join('\n'));
  const by = {};
  for (const r of rows) { const k = `${r['意图大类'] || '?'} · ${r['结论'] || '?'}`; by[k] = (by[k] || 0) + 1; }
  console.log('\n意图大类 · 结论：\n' + Object.entries(by).map(([k, v]) => `- ${k}：${v}`).join('\n'));
  process.exitCode = errors.length ? 1 : 0;
}

// ---------- crosstab ----------
function crosstab(file, opt) {
  const { header, rows } = parseCsv(readText(file));
  const majorCol = findCol(header, ['意图大类', '初分意图大类']);
  const attrCol = findCol(header, ['属性', '主属性']);
  const imprCol = opt.impr || findCol(header, ['展示', '展现', 'impressions', '后台展示']);
  const clickCol = opt.clicks || findCol(header, ['点击', '点击次数', 'clicks']);
  if (!majorCol || !attrCol || !imprCol || !clickCol) throw new Error('需要 意图大类、属性、展示、点击 四列（可用 --impr / --clicks 指定列名）');
  const cell = {}, majors = new Set(), attrs = new Set();
  let ti = 0, tc = 0;
  for (const r of rows) {
    const m = r[majorCol] || '待定', a = (r[attrCol] || '其他').split(/[、,，/;；]/)[0].trim() || '其他';
    const im = num(r[imprCol]) || 0, cl = num(r[clickCol]) || 0;
    majors.add(m); attrs.add(a); ti += im; tc += cl;
    const k = a + '|' + m; cell[k] = cell[k] || [0, 0]; cell[k][0] += im; cell[k][1] += cl;
  }
  const M = MAJOR.filter(x => majors.has(x)).concat([...majors].filter(x => !MAJOR.includes(x)));
  const lines = [`| 主属性 \\ 意图 | ${M.join(' | ')} |`, `|---|${M.map(() => '---').join('|')}|`];
  for (const a of attrs) lines.push(`| ${a} | ${M.map(m => { const c = cell[a + '|' + m]; return c ? `${c[0]} / ${c[1]}` : '—'; }).join(' | ')} |`);
  const share = M.map(m => { let im = 0, cl = 0; for (const a of attrs) { const c = cell[a + '|' + m]; if (c) { im += c[0]; cl += c[1]; } }
    return `${m}：展示 ${im}（${ti ? (im / ti * 100).toFixed(1) : 0}%），点击 ${cl}（${tc ? (cl / tc * 100).toFixed(1) : 0}%），点击率 ${im ? (cl / im * 100).toFixed(1) : 0}%`; });
  console.log('格内为「展示 / 点击」；属性多选时按第一个属性计，避免重复计数。\n\n' + lines.join('\n') + '\n\n' + share.map(s => '- ' + s).join('\n') +
    `\n\n合计：${rows.length} 个查询，展示 ${ti}，点击 ${tc}。后台导出只含可见查询（低频查询被隐去），只能看结构，不能当全量。`);
}

// ---------- 入口 ----------
const argv = process.argv.slice(2);
const pos = [], opt = {};
for (let i = 0; i < argv.length; i++) { if (argv[i].startsWith('--')) opt[argv[i].slice(2)] = argv[++i]; else pos.push(argv[i]); }
const [cmd, file] = pos;
try {
  if (!file || !fs.existsSync(file)) throw new Error('用法见文件头部注释；输入文件不存在：' + (file || '（未给）'));
  if (cmd === 'prep') prep(file, opt);
  else if (cmd === 'check') check(file, opt);
  else if (cmd === 'crosstab') crosstab(file, opt);
  else throw new Error('命令只能是 prep / check / crosstab');
} catch (e) { console.error('出错：' + e.message); process.exitCode = 2; }
