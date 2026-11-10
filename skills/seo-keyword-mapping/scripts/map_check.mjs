#!/usr/bin/env node
// 关键词—页面对照表查重。无依赖，Node 18+。口径见 SKILL.md 步骤 3、assets/templates.md 第 1 节。
//
// 用法（输出 JSON 到标准输出；发现问题时退出码为 1，输入有误时为 2）：
//   node map_check.mjs <对照表.csv> [--url-col 列名] [--topic-col 列名] [--reason-col 列名]
//                      [--require 列1,列2,...] [--delimiter ,|tab|;]
//
// 输入：UTF-8 的 CSV（可带 BOM），第一行是表头。表格软件里「另存为 CSV UTF-8」即可。
// 不指定列名时，按下面的名字找（不区分大小写）：
//   网址列：页面网址 / 页面 / 承接页 / 网址 / url / page
//   主题词列：主题词 / 主词 / primary keyword / topic
//   原因列：下一步 / 原因 / 备注 / next step / reason / note
//
// 查六类问题：
//   duplicateTopics     同一主题词出现在多行（大小写、全半角、空格、连字符、下划线、英文单复数视为同一个词）
//   multiTopicPages     同一网址出现在多行、主题词不同（一页多主题）
//   multiTopicCells     一个格子里写了几个主题词（用 、，,;；| 或「 / 」分隔）
//   missing             必填列为空（默认网址列和主题词列；--require 追加）
//   urlIssues           网址带空格、大写字母、下划线；同一路径既有带斜杠又有不带斜杠的写法
//   pendingWithoutReason  网址写「待建」「不做」的行，原因列为空
//
// 只比较文字，不判断意图。查出来的重复要回 SKILL.md 步骤 6 判断，不要直接删行。

import fs from 'node:fs';

function parseArgs(argv) {
  const pos = [], opt = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) opt[a.slice(2)] = argv[++i];
    else pos.push(a);
  }
  return { file: pos[0], opt };
}

function detectDelimiter(firstLine, forced) {
  if (forced) return forced === 'tab' ? '\t' : forced;
  const counts = [',', '\t', ';'].map((ch) => [ch, firstLine.split(ch).length - 1]);
  counts.sort((x, y) => y[1] - x[1]);
  return counts[0][1] > 0 ? counts[0][0] : ',';
}

// 支持引号、引号内的逗号和换行、"" 转义
function parseCsv(text, delim) {
  const rows = [];
  let row = [], field = '', inQ = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQ) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; } else inQ = false;
      } else field += ch;
      continue;
    }
    if (ch === '"' && field === '') { inQ = true; continue; }
    if (ch === delim) { row.push(field); field = ''; continue; }
    if (ch === '\r') continue;
    if (ch === '\n') { row.push(field); rows.push(row); row = []; field = ''; continue; }
    field += ch;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((c) => c.trim() !== ''));
}

function findCol(header, forced, names) {
  const norm = header.map((h) => h.trim().toLowerCase());
  if (forced) {
    const i = norm.indexOf(forced.trim().toLowerCase());
    return i;
  }
  for (const n of names) {
    const i = norm.indexOf(n);
    if (i >= 0) return i;
  }
  return -1;
}

function singular(tok) {
  if (!/^[a-z]+$/.test(tok) || tok.length <= 3) return tok;
  if (/ies$/.test(tok)) return tok.slice(0, -3) + 'y';
  if (/(s|x|z|ch|sh)es$/.test(tok)) return tok.slice(0, -2);
  if (/ss$/.test(tok)) return tok;
  if (/s$/.test(tok)) return tok.slice(0, -1);
  return tok;
}

// 同一个词的变体写法归成同一个键
function topicKey(s) {
  const t = s.normalize('NFKC').toLowerCase()
    .replace(/[‐‑‒–—_\-]+/g, ' ')
    .replace(/["'“”‘’()（）[\]【】]/g, ' ')
    .trim();
  return t.split(/\s+/).filter(Boolean).map(singular).join('');
}

const SPLIT = /[、，,;；|]|\s\/\s/;
const PENDING = /^(待建|不做|无|暂不|tbd|n\/a|none)/i;

function urlInfo(raw) {
  const s = raw.trim();
  if (!s || PENDING.test(s)) return null;
  let path = s, host = '';
  try {
    const u = new URL(s, 'https://placeholder.invalid');
    path = u.pathname + u.search;
    host = u.host === 'placeholder.invalid' ? '' : u.host.toLowerCase();
  } catch { /* 留原样 */ }
  const issues = [];
  if (/\s/.test(s)) issues.push('带空格');
  if (/[A-Z]/.test(path)) issues.push('路径有大写字母');
  if (/_/.test(path)) issues.push('路径有下划线');
  const bare = host + path.replace(/\/+$/, '').replace(/\/+(\?)/, '$1');
  return { exact: host + path, bare: bare.toLowerCase(), issues };
}

function main() {
  const { file, opt } = parseArgs(process.argv.slice(2));
  if (!file) {
    console.error('用法：node map_check.mjs <对照表.csv> [--url-col 列名] [--topic-col 列名] [--require 列1,列2]');
    process.exit(2);
  }
  let text;
  try { text = fs.readFileSync(file, 'utf8'); } catch (e) { console.error(`读不到文件：${file}（${e.code}）`); process.exit(2); }
  text = text.replace(/^﻿/, '');
  const delim = detectDelimiter(text.split(/\r?\n/)[0] || '', opt.delimiter);
  const rows = parseCsv(text, delim);
  if (rows.length < 2) { console.error('表里没有数据行。'); process.exit(2); }
  const header = rows[0].map((h) => h.trim());
  const urlCol = findCol(header, opt['url-col'], ['页面网址', '页面', '承接页', '网址', 'url', 'page']);
  const topicCol = findCol(header, opt['topic-col'], ['主题词', '主词', 'primary keyword', 'topic']);
  const reasonCol = findCol(header, opt['reason-col'], ['下一步', '原因', '备注', 'next step', 'reason', 'note']);
  if (urlCol < 0 || topicCol < 0) {
    console.error(`找不到${urlCol < 0 ? '网址列' : ''}${urlCol < 0 && topicCol < 0 ? '和' : ''}${topicCol < 0 ? '主题词列' : ''}。表头是：${header.join(' | ')}。用 --url-col / --topic-col 指定。`);
    process.exit(2);
  }
  const extra = (opt.require || '').split(',').map((s) => s.trim()).filter(Boolean);
  const requireCols = [urlCol, topicCol];
  const unknownRequire = [];
  for (const name of extra) {
    const i = findCol(header, name, []);
    if (i < 0) unknownRequire.push(name); else if (!requireCols.includes(i)) requireCols.push(i);
  }

  const data = rows.slice(1).map((r, i) => ({ row: i + 2, cells: r }));
  const cell = (r, c) => (c >= 0 ? (r.cells[c] ?? '').trim() : '');

  const byTopic = new Map(), byUrl = new Map(), byBare = new Map();
  const multiTopicCells = [], missing = [], urlIssues = [], pendingWithoutReason = [];

  for (const r of data) {
    const url = cell(r, urlCol), topic = cell(r, topicCol);
    for (const c of requireCols) if (!cell(r, c)) missing.push({ row: r.row, column: header[c] });

    const parts = topic ? topic.split(SPLIT).map((s) => s.trim()).filter(Boolean) : [];
    if (parts.length > 1) multiTopicCells.push({ row: r.row, url, topic });
    for (const p of parts) {
      const k = topicKey(p);
      if (!k) continue;
      if (!byTopic.has(k)) byTopic.set(k, []);
      byTopic.get(k).push({ row: r.row, url, topic: p });
    }

    if (url && PENDING.test(url)) {
      if (reasonCol < 0 || !cell(r, reasonCol)) pendingWithoutReason.push({ row: r.row, url, topic });
      continue;
    }
    const info = url ? urlInfo(url) : null;
    if (!info) continue;
    if (info.issues.length) urlIssues.push({ row: r.row, url, issues: info.issues });
    // 一页多主题按「忽略大小写和结尾斜杠」归并：多半是同一个页面的不同写法
    if (!byUrl.has(info.bare)) byUrl.set(info.bare, []);
    byUrl.get(info.bare).push({ row: r.row, url, topic });
    if (!byBare.has(info.bare)) byBare.set(info.bare, new Map());
    byBare.get(info.bare).set(url, (byBare.get(info.bare).get(url) || []).concat(r.row));
  }

  const duplicateTopics = [...byTopic.entries()]
    .filter(([, list]) => new Set(list.map((x) => x.row)).size > 1)
    .map(([key, list]) => ({ key, writings: [...new Set(list.map((x) => x.topic))], rows: list }));

  const multiTopicPages = [...byUrl.entries()]
    .filter(([, list]) => new Set(list.map((x) => topicKey(x.topic))).size > 1)
    .map(([, list]) => ({ url: [...new Set(list.map((x) => x.url))].join('  /  '), rows: list.map(({ row, topic }) => ({ row, topic })) }));

  for (const [, variants] of byBare) {
    if (variants.size > 1) {
      urlIssues.push({ rows: [...variants.values()].flat(), url: [...variants.keys()].join('  ≠  '), issues: ['同一路径有多种写法（斜杠或大小写不同）'] });
    }
  }

  const problems = { duplicateTopics, multiTopicPages, multiTopicCells, missing, urlIssues, pendingWithoutReason };
  const count = Object.values(problems).reduce((n, a) => n + a.length, 0);
  const notes = [];
  if (reasonCol < 0) notes.push('没有找到原因列（下一步 / 原因 / 备注），「待建」「不做」行的原因没有检查。');
  if (unknownRequire.length) notes.push(`--require 里这些列不存在：${unknownRequire.join('、')}`);
  notes.push('duplicateTopics 只说明文字相同或是变体写法；是不是内耗，按 SKILL.md 步骤 6 用数据判断。');

  const out = {
    file,
    rows: data.length,
    columns: { url: header[urlCol], topic: header[topicCol], reason: reasonCol >= 0 ? header[reasonCol] : null },
    summary: Object.fromEntries(Object.entries(problems).map(([k, v]) => [k, v.length])),
    problems,
    notes,
  };
  console.log(JSON.stringify(out, null, 2));
  process.exit(count ? 1 : 0);
}

main();
