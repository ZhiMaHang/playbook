#!/usr/bin/env node
// TDK 批量检查：上线前把一批页面的标题、描述、H1、关键词标签过一遍。无依赖，Node 18+。
// 检查项和阈值的依据见 references/batch-workflow.md 第 5 节。
//
// 用法：
//   node tdk_check.mjs <数据.csv|数据.json> [选项]   检查一批页面，默认输出 Markdown 报告
//   node tdk_check.mjs --selftest                     反向测试：用内置的坏样本确认每项检查都拦得住
//
// 选项：
//   --banned <文件>   禁用词清单，一行一个；「词 => 改法」可附改法；# 开头是注释。从本站口径文件整理
//   --brand <写法>    品牌写法，用来查「品牌写两遍」「标题只有品牌」；多种写法用 | 分隔
//   --out <文件.csv>  把每页的长度和问题追加成新列写出（UTF-8 带 BOM，表格软件能直接打开）
//   --json            以 JSON 输出（默认 Markdown）
//   阈值（都是经验值，站点档案另有规定时改这里）：
//   --title-max-en 60   英文标题最多字符数
//   --title-min-zh 40   中文标题最少显示宽度（汉字算 2）
//   --title-max-zh 60   中文标题最多显示宽度
//   --desc-max-en 155   英文描述最多字符数
//   --desc-min-zh 50    中文描述最少字数
//   --desc-max-zh 80    中文描述最多字数
//   --front 60          主题词要落在标题前多少（英文按字符，中文按显示宽度）
//   --kw-max 3          关键词标签最多几个词
//
// 输入列（CSV 第一行是列名，大小写不限；JSON 是对象数组，键名相同）：
//   url          网址或页面标识（必填）
//   type         home | category | series | model | article | crossref | other
//   lang         en | zh；不填时按标题里有没有汉字判断
//   title        前台实际输出的标题（含系统自动追加的后缀）
//   description  前台实际输出的描述
//   h1           H1 文字；h1_count 页面上 H1 的个数（有就填）
//   keywords     关键词标签
//   primary      本页主题词（对照表里的）
//   must         其他必出词，多个用 | 分隔
//   part         型号页的料号（规格书标准写法）
//   datasheet    yes | no：有没有规格书
//
// 退出码：有「错误」级问题时为 1，否则为 0。

import fs from 'node:fs';

const DEFAULTS = {
  'title-max-en': 60, 'title-min-zh': 40, 'title-max-zh': 60,
  'desc-max-en': 155, 'desc-min-zh': 50, 'desc-max-zh': 80,
  front: 60, 'kw-max': 3,
};

const CATEGORY = {
  length: '长度', must: '必出词', banned: '禁用词', dup: '重复', empty: '空值与占位',
  residue: '模板残留', volatile: '会过期的数字', h1: 'H1 数量', format: '格式与红线',
};
const LEVELS = ['错误', '警告', '提示'];

// ---------- 参数 ----------
function parseArgs(argv) {
  const opt = { ...DEFAULTS };
  const pos = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) { pos.push(a); continue; }
    const k = a.slice(2);
    if (k === 'json' || k === 'selftest') { opt[k] = true; continue; }
    const v = argv[++i];
    if (v === undefined) throw new Error(`选项 --${k} 缺少值`);
    if (k in DEFAULTS) {
      opt[k] = Number(v);
      if (!Number.isFinite(opt[k])) throw new Error(`选项 --${k} 要填数字`);
    } else opt[k] = v;
  }
  return { file: pos[0], opt };
}

// ---------- 读入 ----------
function parseCSV(text) {
  text = text.replace(/^﻿/, '');
  const rows = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else quoted = false;
      } else field += c;
    } else if (c === '"' && field === '') quoted = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(field); rows.push(row); row = []; field = '';
    } else field += c;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  const lines = rows.filter(r => r.some(f => f.trim() !== ''));
  if (!lines.length) return { headers: [], records: [] };
  const headers = lines.shift().map(h => h.trim());
  const records = lines.map(r => Object.fromEntries(headers.map((h, j) => [h, (r[j] ?? '').trim()])));
  return { headers, records };
}

function loadRows(file) {
  const text = fs.readFileSync(file, 'utf8');
  if (/\.json$/i.test(file)) {
    const data = JSON.parse(text.replace(/^﻿/, ''));
    if (!Array.isArray(data)) throw new Error('JSON 要是对象数组');
    const headers = [...new Set(data.flatMap(o => Object.keys(o)))];
    return { headers, records: data.map(o => Object.fromEntries(Object.entries(o).map(([k, v]) => [k, v == null ? '' : String(v).trim()]))) };
  }
  return parseCSV(text);
}

// 列名统一成小写，原始记录留着写回
function normalize(rec) {
  const r = {};
  for (const [k, v] of Object.entries(rec)) r[k.trim().toLowerCase()] = v;
  return r;
}

function loadBanned(file) {
  if (!file) return [];
  return fs.readFileSync(file, 'utf8').replace(/^﻿/, '').split(/\r?\n/)
    .map(l => l.trim()).filter(l => l && !l.startsWith('#'))
    .map(l => {
      const [term, fix] = l.split(/\s*=>\s*/);
      return { term: term.trim(), fix: (fix || '').trim(), re: termRegex(term.trim()) };
    }).filter(b => b.term);
}

// ---------- 度量 ----------
const WIDE = /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/;
const HAN = /[㐀-䶿一-鿿]/;
const width = s => { let w = 0; for (const ch of s) w += WIDE.test(ch) ? 2 : 1; return w; };
const chars = s => [...s].length;
const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const LATIN = 'A-Za-z0-9\\u00C0-\\u024F';

// 拉丁文字的词按整词匹配（drop-in 不会命中 drop into），中文按子串匹配
function termRegex(term) {
  const first = [...term][0] || '', last = [...term].at(-1) || '';
  const isLatin = ch => new RegExp(`[${LATIN}]`).test(ch);
  const L = isLatin(first) ? `(?<![${LATIN}])` : '';
  const R = isLatin(last) ? `(?![${LATIN}])` : '';
  return new RegExp(L + esc(term) + R, 'iu');
}
const has = (text, term) => !!term && text.toLowerCase().includes(term.toLowerCase());
const countOf = (text, term) => {
  if (!term) return 0;
  const m = text.toLowerCase().match(new RegExp(esc(term.toLowerCase()), 'g'));
  return m ? m.length : 0;
};
const split = (s, re) => (s || '').split(re).map(x => x.trim()).filter(Boolean);

// ---------- 规则 ----------
// null 只在整格就是 null 时算占位（null modem cable 这类正常词不误报）
const PLACEHOLDER = /待补充|待填写|待确认|(?<![A-Za-z])(TBD|TODO|undefined|NaN)(?![A-Za-z])|lorem ipsum|^\s*null\s*$|\{\{|\}\}|\{[a-z_]+\}|xxx/i;
const EMPTY_WORDS = /^(home|homepage|untitled|index|default|products?|page|首页|主页|产品|产品中心|未命名|无标题)$/i;
// 参数里的数字（PN16、DN50、1000BASE-T）不算：数字前面不能紧挨字母、数字或小数点
const VOLATILE_ZH = /(?<![A-Za-z\d.])\d[\d,]*\+?\s*(个|款|种|条)\s*(料号|型号|系列|产品|规格|选项)|(?<![A-Za-z\d.])\d[\d,]*\+?\s*(料号|型号)(?!书)|共\s*\d+\s*(个|款|种|条)|[一二三四五六七八九十两]\s*(个系列|大系列|种规格|个分支|款产品)|最多的|最全的/;
const VOLATILE_EN = /(?<![A-Za-z\d.])\d[\d,]*\+?\s+(part numbers?|models?|skus?|products|variants|options|series)\b/i;
const PHONE = /(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)0\d{2,3}-\d{7,8}(?!\d)|\+\d{1,3}[\s-]?\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}|电话|手机|微信|(?<![A-Za-z])QQ(?![A-Za-z])|\bTel\s*[:：]|WhatsApp/i;
const SYMBOLS = /[★☆◆◇■□●○▲△▼▽♥❤✔✓【】〖〗]|\p{Extended_Pictographic}/u;
const SEP = '[\\s|｜\\-–—:：_,，·]';

function checkRows(records, opt, banned) {
  const brands = split(opt.brand, /\|/);
  const rows = records.map(normalize);
  const issues = [];
  const add = (r, level, field, code, msg) => issues.push({ url: r.url || '(无网址)', level, field, code, msg });

  // 跨页面比对用的索引
  const keyOf = s => (s || '').trim().replace(/\s+/g, ' ').toLowerCase();
  const index = field => {
    const m = new Map();
    rows.forEach((r, i) => { const k = keyOf(r[field]); if (k) m.set(k, [...(m.get(k) || []), i]); });
    return m;
  };
  const titleIdx = index('title'), descIdx = index('description'), h1Idx = index('h1');
  const pnIdx = new Map();
  rows.forEach((r, i) => {
    if (r.part && r.title && has(r.title, r.part)) {
      const k = keyOf(r.title.replace(new RegExp(esc(r.part), 'ig'), '{料号}'));
      pnIdx.set(k, [...(pnIdx.get(k) || []), i]);
    }
  });
  const others = (list, i) => list.filter(j => j !== i).map(j => rows[j].url || `第 ${j + 2} 行`);
  const listOf = arr => arr.slice(0, 3).join('、') + (arr.length > 3 ? ` 等 ${arr.length} 页` : '');

  const measured = rows.map((r, i) => {
    const title = r.title || '', desc = r.description || '', h1 = r.h1 || '';
    const lang = (r.lang || '').toLowerCase() || (HAN.test(title + desc + h1) ? 'zh' : 'en');
    const zh = lang.startsWith('zh');
    const tLen = zh ? width(title) : chars(title);
    const dLen = chars(desc);
    const unitT = zh ? '宽度' : '字符', unitD = zh ? '字' : '字符';
    const hasCol = k => Object.prototype.hasOwnProperty.call(r, k);

    // 空值与占位
    if (!title) add(r, '错误', '标题', 'empty', '标题为空');
    if (hasCol('description') && !desc) add(r, '错误', '描述', 'empty', '描述为空（会用上系统兜底描述或被搜索引擎自取）');
    if (hasCol('h1') && !h1) add(r, '错误', 'H1', 'empty', 'H1 为空');
    for (const [f, v] of [['标题', title], ['描述', desc], ['H1', h1]]) {
      if (v && PLACEHOLDER.test(v)) add(r, '错误', f, 'empty', `有占位或模板变量：「${v.match(PLACEHOLDER)[0]}」`);
    }
    if (title) {
      let core = title;
      for (const b of brands) core = core.replace(new RegExp(esc(b), 'ig'), ' ');
      core = core.replace(new RegExp(SEP, 'g'), ' ').trim();
      if (brands.length && chars(core.replace(/\s/g, '')) < 4) add(r, '错误', '标题', 'empty', '半空标题：只有品牌，没有本页主题');
      else if (EMPTY_WORDS.test(core)) add(r, '错误', '标题', 'empty', `空话标题：「${core}」没有说明本页讲什么`);
      else if (chars(title) <= 5) add(r, '警告', '标题', 'length', `标题只有 ${chars(title)} 个字符，太短的标题几乎都会被改写`);
    }

    // 长度（经验值）
    if (title) {
      if (zh && tLen > opt['title-max-zh']) add(r, '警告', '标题', 'length', `显示宽度 ${tLen}，超过 ${opt['title-max-zh']}（经验值）`);
      if (zh && tLen < opt['title-min-zh'] && r.type !== 'home') add(r, '警告', '标题', 'length', `显示宽度 ${tLen}，不足 ${opt['title-min-zh']}（经验值）：看看是否缺关键参数`);
      if (!zh && tLen > opt['title-max-en']) add(r, '警告', '标题', 'length', `${tLen} 个字符，超过 ${opt['title-max-en']}（经验值）：确认被截掉的只是品牌或次要限定`);
    }
    if (desc) {
      if (zh && dLen > opt['desc-max-zh']) add(r, '警告', '描述', 'length', `${dLen} 字，超过 ${opt['desc-max-zh']}（经验值）`);
      if (zh && dLen < opt['desc-min-zh']) add(r, '警告', '描述', 'length', `${dLen} 字，不足 ${opt['desc-min-zh']}（经验值）`);
      if (!zh && dLen > opt['desc-max-en']) add(r, '警告', '描述', 'length', `${dLen} 个字符，超过 ${opt['desc-max-en']}（经验值）`);
    }

    // 必出词与位置
    if (r.primary && title) {
      if (!has(title, r.primary)) add(r, '错误', '标题', 'must', `没有主题词「${r.primary}」`);
      else {
        const at = title.toLowerCase().indexOf(r.primary.toLowerCase());
        const pre = title.slice(0, at + r.primary.length);
        const m = zh ? width(pre) : chars(pre);
        if (m > opt.front) add(r, '警告', '标题', 'must', `主题词结束在第 ${m} ${unitT}，超出前 ${opt.front}：被截断时会丢`);
        const n = countOf(title, r.primary);
        if (n >= 2) add(r, '警告', '标题', 'format', `主题词在标题里出现 ${n} 次，像在堆词`);
      }
    }
    if (r.primary && h1 && !has(h1, r.primary)) add(r, '错误', 'H1', 'must', `H1 没有主题词「${r.primary}」，和标题对不上`);
    for (const w of split(r.must, /\|/)) {
      if (title && !has(title, w)) add(r, '错误', '标题', 'must', `缺必出词「${w}」`);
    }
    if (r.part) {
      if (/\s|[()（）]|[一-鿿]/.test(r.part)) add(r, '警告', '料号', 'residue', `料号字段「${r.part}」带空格、括号或中文备注：备注放进参数，料号用规格书写法`);
      if (title && !has(title, r.part)) add(r, '错误', '标题', 'must', `没有料号「${r.part}」`);
      else if (title && r.type === 'model' && !title.trim().toLowerCase().startsWith(r.part.toLowerCase())) add(r, '警告', '标题', 'must', '料号不在最前');
      if (h1 && !has(h1, r.part)) add(r, '警告', 'H1', 'must', `H1 没有料号「${r.part}」`);
    } else if (r.type === 'model') add(r, '提示', '料号', 'must', '型号页没填 part 列，没法查料号位置');
    if (desc && !(has(desc, r.primary) || has(desc, r.part)) && (r.primary || r.part)) add(r, '提示', '描述', 'must', '描述里没有主题词或料号：「是什么」可能没写清');
    if (desc && r.primary && countOf(desc, r.primary) >= 3) add(r, '警告', '描述', 'format', `主题词在描述里出现 ${countOf(desc, r.primary)} 次，像关键词列表`);

    // 禁用词（整词匹配）
    for (const [f, v] of [['标题', title], ['描述', desc], ['H1', h1], ['关键词标签', r.keywords || '']]) {
      for (const b of banned) {
        if (v && b.re.test(v)) add(r, '错误', f, 'banned', `禁用说法「${b.term}」${b.fix ? `，改法：${b.fix}` : ''}`);
      }
    }

    // 重复
    const dupT = titleIdx.get(keyOf(title)) || [];
    if (title && dupT.length > 1) add(r, '错误', '标题', 'dup', `和 ${listOf(others(dupT, i))} 标题相同`);
    const dupD = descIdx.get(keyOf(desc)) || [];
    if (desc && dupD.length > 1) add(r, '错误', '描述', 'dup', `和 ${listOf(others(dupD, i))} 描述相同`);
    const dupH = h1Idx.get(keyOf(h1)) || [];
    if (h1 && dupH.length > 1) add(r, '警告', 'H1', 'dup', `和 ${listOf(others(dupH, i))} H1 相同`);
    if (r.part && title && has(title, r.part) && dupT.length <= 1) {
      const g = pnIdx.get(keyOf(title.replace(new RegExp(esc(r.part), 'ig'), '{料号}'))) || [];
      if (g.length > 1) add(r, '提示', '标题', 'dup', `和另外 ${g.length - 1} 页去掉料号后完全相同：确认模板短、料号打头，能加区别参数就加`);
    }

    // 模板残留
    if (title && !zh && title.includes('_')) add(r, '警告', '标题', 'residue', '有下划线拼接');
    for (const [f, v] of [['标题', title], ['描述', desc]]) {
      if (/(?<![A-Za-z])ALL(?![A-Za-z])/.test(v)) add(r, '警告', f, 'residue', '有「ALL」：改成读者看得懂的说法');
      if (/ {2,}/.test(v) || /(\s[|｜\-–—:]\s*){2,}|\|\s*\||--/.test(v)) add(r, '警告', f, 'residue', '有连续空格或重复的分隔符');
    }
    if (title && new RegExp(`^${SEP}+|${SEP}+$`).test(title.replace(/\s+$/, '').replace(/^\s+/, ''))) add(r, '警告', '标题', 'residue', '标题以分隔符开头或结尾');
    for (const b of brands) {
      const n = countOf(title, b);
      if (n >= 2) add(r, '警告', '标题', 'residue', `品牌「${b}」写了 ${n} 次：品牌只写一次，放最后`);
    }

    // 会过期的数字
    for (const [f, v] of [['标题', title], ['描述', desc], ['H1', h1]]) {
      const m = v && (v.match(VOLATILE_ZH) || v.match(VOLATILE_EN));
      if (m) add(r, '错误', f, 'volatile', `会随上下架失效的数字：「${m[0]}」`);
    }

    // H1 数量
    if (r.h1_count !== undefined && r.h1_count !== '' && Number(r.h1_count) !== 1) add(r, '错误', 'H1', 'h1', `页面上有 ${r.h1_count} 个 H1，应为 1 个`);

    // 格式与红线
    if (title && /[[\]]/.test(title)) add(r, '警告', '标题', 'format', '有方括号：更容易被改写，也可能是没填的占位');
    if (title && SYMBOLS.test(title)) add(r, '警告', '标题', 'format', `有特殊符号「${title.match(SYMBOLS)[0]}」`);
    if (title && PHONE.test(title)) add(r, '错误', '标题', 'format', `标题里有联系方式「${title.match(PHONE)[0]}」`);
    if (zh && title && /[_|｜]/.test(title)) add(r, '警告', '标题', 'format', '中文标题用了「_」或「|」：百度规范建议分隔符统一用「-」');
    if (zh && title && /官网|官方网站/.test(title) && r.type !== 'home') add(r, '警告', '标题', 'format', '非首页标题写了「官网」：核对是否确为官方网站');
    if (/^(no|否|无|0|false)$/i.test(r.datasheet || '')) {
      for (const [f, v] of [['标题', title], ['描述', desc]]) {
        if (/datasheet|规格书/i.test(v)) add(r, '错误', f, 'format', '没有规格书却写了 Datasheet / 规格书');
      }
    }
    const kws = split(r.keywords, /[,，;；、|]/);
    if (kws.length > opt['kw-max']) add(r, '警告', '关键词标签', 'format', `${kws.length} 个词：留空，或只写主题词加一两个同义词`);
    if (new Set(kws.map(k => k.toLowerCase())).size < kws.length) add(r, '警告', '关键词标签', 'format', '有重复的词');
    if (kws.length && r.primary && !kws.some(k => has(k, r.primary) || has(r.primary, k))) add(r, '提示', '关键词标签', 'format', '和本页主题词对不上：要么写准，要么留空');

    return { url: r.url || '', lang: zh ? 'zh' : 'en', title: tLen ? `${tLen} ${unitT}` : '', desc: dLen ? `${dLen} ${unitD}` : '', h1: h1 ? `${zh ? width(h1) : chars(h1)} ${unitT}` : '' };
  });
  return { rows, issues, measured };
}

// ---------- 输出 ----------
function summarize(issues) {
  const by = {};
  for (const c of Object.values(CATEGORY)) by[c] = { 错误: 0, 警告: 0, 提示: 0 };
  for (const it of issues) by[CATEGORY[it.code]][it.level]++;
  return by;
}

const cell = s => String(s ?? '').replace(/\|/g, '\\|').replace(/\r?\n/g, ' ');

function toMarkdown(file, result, opt, banned) {
  const { issues, measured } = result;
  const n = l => issues.filter(i => i.level === l).length;
  const out = [];
  out.push('# TDK 检查结果', '');
  out.push(`- 文件：${file}；共 ${measured.length} 页；错误 ${n('错误')} 条，警告 ${n('警告')} 条，提示 ${n('提示')} 条`);
  out.push(`- 阈值（经验值）：英文标题 ≤ ${opt['title-max-en']} 字符；中文标题显示宽度 ${opt['title-min-zh']}–${opt['title-max-zh']}；英文描述 ≤ ${opt['desc-max-en']} 字符；中文描述 ${opt['desc-min-zh']}–${opt['desc-max-zh']} 字；主题词在前 ${opt.front}；关键词标签 ≤ ${opt['kw-max']} 个词`);
  out.push(`- 禁用词：${opt.banned ? `${opt.banned}（${banned.length} 个）` : '未提供，没有查口径禁用说法'}`);
  out.push(`- 品牌写法：${opt.brand || '未提供，没有查品牌重复和半空标题'}`, '');
  out.push('## 按检查项汇总', '', '| 检查项 | 错误 | 警告 | 提示 |', '|---|---|---|---|');
  for (const [c, v] of Object.entries(summarize(issues))) out.push(`| ${c} | ${v.错误} | ${v.警告} | ${v.提示} |`);
  out.push('', '## 逐页问题', '');
  if (!issues.length) out.push('没有发现问题。');
  else {
    out.push('| 网址 | 级别 | 位置 | 检查项 | 问题 |', '|---|---|---|---|---|');
    const sorted = [...issues].sort((a, b) => LEVELS.indexOf(a.level) - LEVELS.indexOf(b.level));
    for (const it of sorted) out.push(`| ${cell(it.url)} | ${it.level} | ${it.field} | ${CATEGORY[it.code]} | ${cell(it.msg)} |`);
  }
  out.push('', '## 长度', '', '| 网址 | 语言 | 标题 | 描述 | H1 |', '|---|---|---|---|---|');
  for (const m of measured) out.push(`| ${cell(m.url)} | ${m.lang} | ${m.title} | ${m.desc} | ${m.h1} |`);
  out.push('', '说明：长度英文按字符、中文标题按显示宽度（汉字算 2）、中文描述按字数；阈值都是经验值，不是搜索引擎的规定。');
  return out.join('\n');
}

const csvField = s => { s = String(s ?? ''); return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };

function writeCSV(file, headers, records, result) {
  const extra = ['标题长度', '描述长度', '错误', '警告', '提示'];
  const lines = [[...headers, ...extra].map(csvField).join(',')];
  records.forEach((rec, i) => {
    const url = result.rows[i].url || '(无网址)';
    const pick = l => result.issues.filter(it => it.url === url && it.level === l).map(it => `${it.field}：${it.msg}`).join('；');
    const m = result.measured[i];
    lines.push([...headers.map(h => rec[h]), m.title, m.desc, pick('错误'), pick('警告'), pick('提示')].map(csvField).join(','));
  });
  fs.writeFileSync(file, '﻿' + lines.join('\r\n') + '\r\n', 'utf8');
}

// ---------- 反向测试 ----------
// 每个坏样本只埋一种错，检查必须报出对应的检查项；好样本不能报错误和警告。
function selftest() {
  const opt = { ...DEFAULTS, brand: 'Brand X|品牌X' };
  const banned = ['best => 删掉，写可核对的事实', 'drop-in => replacement candidate', '厂家直供'].map(l => {
    const [term, fix] = l.split(/\s*=>\s*/);
    return { term, fix: fix || '', re: termRegex(term) };
  });
  const base = { type: 'category', lang: 'en', h1_count: '1', keywords: '', datasheet: 'yes' };
  const good = [
    { ...base, url: '/good-en', title: 'Ball Valves | 2-Piece, Flanged - Brand X', description: 'Ball valves with 2-piece and 3-piece bodies, flanged and threaded ends. Filter by size and pressure class and download datasheets.', h1: 'Ball Valves', primary: 'ball valve' },
    { ...base, url: '/good-zh', type: 'model', lang: 'zh', part: 'BV2040', title: 'BV2040 两片式法兰球阀 DN50 PN16 规格书 - 品牌X', description: 'BV2040 是一款两片式法兰球阀，通径 DN50，压力等级 PN16，阀体为不锈钢，用于化工和水处理管线。可下载规格书和外形图，在线询价。', h1: 'BV2040 两片式法兰球阀', primary: '法兰球阀' },
    { ...base, url: '/good-wholeword', title: 'How to Drop Into a Gate Valve Retrofit | Brand X', description: 'Gate valve retrofit steps for existing pipelines: how to check face-to-face dimensions, flange ratings and actuator mounting before you order.', h1: 'How to Drop Into a Gate Valve Retrofit', primary: 'gate valve', type: 'article' },
    { ...base, url: '/good-long-part', type: 'model', part: 'GV-2040-1000', title: 'GV-2040-1000 Gate Valve, Class 150 Datasheet | Brand X', description: 'GV-2040-1000 is a Class 150 flanged gate valve, 4 inch, WCB body. Datasheet and drawing available; request a quote.', h1: 'GV-2040-1000 Gate Valve', primary: 'gate valve' },
  ];
  const bad = [
    ['empty', { ...base, url: '/bad-empty', title: '', description: 'Gate valves for water lines, flanged ends, cast iron bodies, with datasheets.', h1: 'Gate Valves', primary: 'gate valve' }],
    ['empty', { ...base, url: '/bad-halfempty', title: '| Brand X', description: 'Check valves in swing and lift types for water and oil lines. Filter by size and download datasheets.', h1: 'Check Valves', primary: 'check valve' }],
    ['dup', { ...base, url: '/bad-dup-1', title: 'Butterfly Valves | Wafer, Lug - Brand X', description: 'Wafer-type butterfly valves for HVAC lines. Filter by size.', h1: 'Butterfly Valves', primary: 'butterfly valve' }],
    ['dup', { ...base, url: '/bad-dup-2', title: 'Butterfly Valves | Wafer, Lug - Brand X', description: 'Lug-type butterfly valves for dead-end service. Filter by size.', h1: 'Butterfly Valves for Dead-End Service', primary: 'butterfly valve' }],
    ['banned', { ...base, url: '/bad-banned', title: 'Best Globe Valves | Flanged - Brand X', description: 'Globe valves for throttling service with flanged ends. Download datasheets.', h1: 'Globe Valves', primary: 'globe valve' }],
    ['banned', { ...base, url: '/bad-banned-zh', lang: 'zh', title: '法兰闸阀 DN50-DN300 厂家直供 - 品牌X', description: '法兰闸阀，通径 DN50 到 DN300，压力等级 PN16、PN25，阀体为球墨铸铁，用于给排水管线，可按通径筛选，下载规格书。', h1: '法兰闸阀', primary: '法兰闸阀' }],
    ['length', { ...base, url: '/bad-length', title: 'Pneumatic Actuators for Quarter-Turn Valves | Double Acting, Spring Return - Brand X', description: 'Pneumatic actuators for ball and butterfly valves. Download datasheets.', h1: 'Pneumatic Actuators', primary: 'pneumatic actuator' }],
    ['must', { ...base, url: '/bad-must', title: 'Valve Accessories | Limit Switches - Brand X', description: 'Strainers in Y and basket types for pipelines, with datasheets for each size.', h1: 'Strainers', primary: 'strainer' }],
    ['must', { ...base, url: '/bad-pn-order', type: 'model', part: 'GV300', title: 'Flanged Gate Valve GV300 Datasheet | Brand X', description: 'GV300 is a flanged gate valve, DN80, PN16. Datasheet available; request a quote.', h1: 'GV300 Flanged Gate Valve', primary: 'gate valve' }],
    ['h1', { ...base, url: '/bad-h1', h1_count: '2', title: 'Plug Valves | Lubricated, Sleeved - Brand X', description: 'Plug valves in lubricated and sleeved designs for slurry lines. Download datasheets.', h1: 'Plug Valves', primary: 'plug valve' }],
    ['residue', { ...base, url: '/bad-residue', type: 'model', part: 'NV10', title: 'NV10 Needle Valve_ALL - Brand X', description: 'NV10 is a needle valve for instrument lines, 1/4 NPT. Datasheet available.', h1: 'NV10 Needle Valve', primary: 'needle valve' }],
    ['residue', { ...base, url: '/bad-brand-twice', title: 'Brand X Diaphragm Valves | Weir Type - Brand X', description: 'Weir-type diaphragm valves for corrosive media. Download datasheets.', h1: 'Diaphragm Valves', primary: 'diaphragm valve' }],
    ['residue', { ...base, url: '/bad-part-field', type: 'model', part: 'SV 20 (短体)', title: 'SV 20 (短体) Solenoid Valve | Brand X', description: 'SV 20 is a 2-way solenoid valve, 24 VDC. Datasheet available.', h1: 'SV 20 (短体) Solenoid Valve', primary: 'solenoid valve' }],
    ['volatile', { ...base, url: '/bad-volatile-en', title: 'Check Valves | Swing, Lift - Brand X', description: 'Choose from over 300 models of swing and lift check valves. Download datasheets.', h1: 'Check Valves', primary: 'check valve' }],
    ['volatile', { ...base, url: '/bad-volatile', lang: 'zh', title: '不锈钢球阀 共 600 个型号 DN15-DN100 - 品牌X', description: '不锈钢球阀，通径 DN15 到 DN100，压力等级 PN16 到 PN64，丝扣和法兰连接，可按通径和材质筛选，下载规格书。', h1: '不锈钢球阀', primary: '不锈钢球阀' }],
    ['format', { ...base, url: '/bad-phone', lang: 'zh', title: '安全阀 弹簧全启式 电话13812345678 - 品牌X', description: '弹簧全启式安全阀，整定压力范围宽，用于锅炉和压力容器，可按口径和压力等级筛选，下载规格书，在线询价。', h1: '安全阀', primary: '安全阀' }],
    ['format', { ...base, url: '/bad-datasheet', type: 'model', part: 'RV5', datasheet: 'no', title: 'RV5 Pressure Reducing Valve Datasheet | Brand X', description: 'RV5 is a pressure reducing valve for steam lines, DN25.', h1: 'RV5 Pressure Reducing Valve', primary: 'pressure reducing valve' }],
    ['format', { ...base, url: '/bad-keywords', keywords: 'control valve, control valves, valve, valves, flow control', title: 'Control Valves | Globe, Rotary - Brand X', description: 'Control valves in globe and rotary designs with pneumatic actuators. Download datasheets.', h1: 'Control Valves', primary: 'control valve' }],
    ['format', { ...base, url: '/bad-zh-sep', lang: 'zh', title: '蝶阀_对夹式_手柄驱动 DN50-DN200_品牌X', description: '对夹式蝶阀，手柄驱动，通径 DN50 到 DN200，用于暖通和给排水管线，可按通径和阀体材质筛选，下载规格书。', h1: '对夹式蝶阀', primary: '蝶阀' }],
  ];
  const all = [...good, ...bad.map(b => b[1])];
  const { issues } = checkRows(all, opt, banned);
  const lines = [];
  let fail = 0;
  for (const g of good) {
    const hit = issues.filter(it => it.url === g.url && it.level !== '提示');
    if (hit.length) { fail++; lines.push(`✗ 好样本 ${g.url} 被误报：${hit.map(h => h.msg).join('；')}`); }
    else lines.push(`✓ 好样本 ${g.url} 没有误报`);
  }
  for (const [code, row] of bad) {
    const hit = issues.filter(it => it.url === row.url && it.code === code && it.level !== '提示');
    if (!hit.length) { fail++; lines.push(`✗ ${row.url}：没拦住「${CATEGORY[code]}」`); }
    else lines.push(`✓ ${row.url}：拦住「${CATEGORY[code]}」—— ${hit[0].msg}`);
  }
  lines.push('', fail ? `反向测试未通过：${fail} 项` : `反向测试通过：${good.length} 个好样本没有误报，${bad.length} 个坏样本全部拦住`);
  console.log(lines.join('\n'));
  process.exit(fail ? 1 : 0);
}

// ---------- 入口 ----------
function main() {
  let args;
  try { args = parseArgs(process.argv.slice(2)); } catch (e) { console.error(e.message); process.exit(2); }
  const { file, opt } = args;
  if (opt.selftest) return selftest();
  if (!file) { console.error('用法：node tdk_check.mjs <数据.csv|数据.json> [--banned 禁用词.txt] [--brand 品牌] [--out 结果.csv] [--json]；node tdk_check.mjs --selftest'); process.exit(2); }
  const { headers, records } = loadRows(file);
  if (!records.length) { console.error('没有读到数据行'); process.exit(2); }
  if (!headers.some(h => h.trim().toLowerCase() === 'url')) { console.error('缺少 url 列'); process.exit(2); }
  const banned = loadBanned(opt.banned);
  const result = checkRows(records, opt, banned);
  if (opt.out) writeCSV(opt.out, headers, records, result);
  if (opt.json) console.log(JSON.stringify({ file, options: opt, summary: summarize(result.issues), issues: result.issues, lengths: result.measured }, null, 2));
  else console.log(toMarkdown(file, result, opt, banned));
  process.exit(result.issues.some(i => i.level === '错误') ? 1 : 0);
}

main();
