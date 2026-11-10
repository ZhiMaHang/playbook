#!/usr/bin/env node
// 站点 SEO 测量工具。无依赖，Node 18+。口径见 references/measurement.md。
//
// 用法（输出 JSON 到标准输出）：
//   node seo_measure.mjs facts   <网址|HTML文件> [--ua 名称] [--render] [--tables]
//   node seo_measure.mjs text    <网址|HTML文件> [--render] [--tables]          输出正文纯文本
//   node seo_measure.mjs density <网址|HTML文件> --terms 词表.json [--render] [--tables]
//   node seo_measure.mjs overlap <网址|HTML文件> --against <网址|文件> [--against ...] [--n 6]
//   node seo_measure.mjs uacheck <网址>                                         多 UA 对比
//   node seo_measure.mjs links   <网址|HTML文件> [--limit 300] [--concurrency 4]
//
// 选项：
//   --ua        desktop（默认）| mobile | googlebot | googlebot-desktop | baiduspider | bingbot | gptbot | claudebot | perplexitybot
//   --ua-string 自定义 UA 全文
//   --render    用本机 Chrome 渲染后再分析（CHROME_PATH 环境变量可指定路径）
//   --tables    正文计入表格文字（默认不计，表格词数单独给出）
//   --base      分析本地 HTML 文件时，用来补全相对链接的网址
//
// 词表 JSON 格式：{"core":[],"variant":[],"derived":[],"longtail":[],"volume":{"词":月搜索量}}
// 走代理：Node 24+ 设环境变量 NODE_USE_ENV_PROXY=1。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';

const UAS = {
  desktop: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
  mobile: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
  googlebot: 'Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
  'googlebot-desktop': 'Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)',
  baiduspider: 'Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)',
  bingbot: 'Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)',
  gptbot: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)',
  claudebot: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)',
  perplexitybot: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)',
};

// ---------- 参数 ----------
function parseArgs(argv) {
  const pos = [], opt = { against: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) { pos.push(a); continue; }
    const k = a.slice(2);
    if (['render', 'tables'].includes(k)) { opt[k] = true; continue; }
    const v = argv[++i];
    if (k === 'against') opt.against.push(v); else opt[k] = v;
  }
  return { cmd: pos[0], target: pos[1], opt };
}

// ---------- 抓取 ----------
function decodeBody(buf, contentType) {
  let cs = (/charset=([\w-]+)/i.exec(contentType || '') || [])[1];
  if (!cs) cs = (/<meta[^>]+charset=["']?([\w-]+)/i.exec(buf.subarray(0, 4096).toString('latin1')) || [])[1];
  try { return new TextDecoder((cs || 'utf-8').toLowerCase()).decode(buf); } catch { return buf.toString('utf8'); }
}

async function fetchChain(url, ua) {
  const chain = [];
  let cur = url;
  for (let i = 0; i < 10; i++) {
    const res = await fetch(cur, { redirect: 'manual', headers: { 'user-agent': ua, accept: 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8' } });
    const loc = res.headers.get('location');
    chain.push(loc ? { url: cur, status: res.status, location: loc } : { url: cur, status: res.status });
    if (res.status >= 300 && res.status < 400 && loc) {
      await res.arrayBuffer().catch(() => {});
      cur = new URL(loc, cur).href;
      continue;
    }
    const buf = Buffer.from(await res.arrayBuffer());
    return { chain, finalUrl: cur, status: res.status, headers: Object.fromEntries(res.headers), html: decodeBody(buf, res.headers.get('content-type')), bytes: buf.length };
  }
  throw new Error('跳转超过 10 次：' + url);
}

function findChrome() {
  const c = [process.env.CHROME_PATH,
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'];
  return c.find(p => p && fs.existsSync(p));
}

function renderDom(url, ua) {
  const chrome = findChrome();
  if (!chrome) throw new Error('找不到 Chrome，设 CHROME_PATH 或去掉 --render');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'seo-measure-'));
  const r = spawnSync(chrome, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--user-data-dir=' + dir, '--virtual-time-budget=8000', '--user-agent=' + ua, '--dump-dom', url],
  { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024, timeout: 60000 });
  try { fs.rmSync(dir, { recursive: true, force: true }); } catch {}
  if (r.status !== 0 && !r.stdout) throw new Error('Chrome 渲染失败：' + (r.stderr || '').slice(0, 300));
  return r.stdout;
}

async function load(target, opt, uaName) {
  const ua = opt['ua-string'] || UAS[uaName || opt.ua || 'desktop'] || UAS.desktop;
  if (!/^https?:\/\//i.test(target)) {
    const html = fs.readFileSync(target, 'utf8');
    return { url: opt.base || null, finalUrl: opt.base || null, status: null, chain: [], headers: {}, html, bytes: Buffer.byteLength(html), rendered: false, ua };
  }
  const r = await fetchChain(target, ua);
  let rendered = false;
  if (opt.render) { r.html = renderDom(r.finalUrl, ua); rendered = true; }
  return { url: target, ...r, rendered, ua };
}

// ---------- HTML 解析（按规则，不建 DOM） ----------
// 标签内部写成 §：允许引号里出现 < 和 >（有的站把 HTML 片段放进属性值）
const AT = `(?:[^>"']|"[^"]*"|'[^']*')*`;
const rx = (s, f) => new RegExp(s.split('§').join(AT), f);
const ENT = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', copy: '©', reg: '®', trade: '™', hellip: '…', mdash: '—', ndash: '–', rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“', deg: '°', times: '×', middot: '·', minus: '−', plusmn: '±', micro: 'µ', ohm: 'Ω' };
function decodeEntities(s) {
  return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => {
    if (e[0] === '#') { const n = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10); try { return String.fromCodePoint(n); } catch { return m; } }
    return ENT[e.toLowerCase()] ?? m;
  });
}
function attrs(tag) {
  const o = {}, re = /([^\s=\/>"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
  const inner = tag.replace(/^<\s*[\w:-]+/, '').replace(/\/?>$/, '');
  let m;
  while ((m = re.exec(inner))) o[m[1].toLowerCase()] = decodeEntities(m[2] ?? m[3] ?? m[4] ?? '');
  return o;
}
const tagsOf = (html, name) => (html.match(rx('<' + name + '\\b§>', 'gi')) || []).map(attrs);
function toText(fragment) {
  return decodeEntities(fragment
    .replace(rx('<(br|hr)\\b§>', 'gi'), '\n')
    .replace(/<\/(p|div|li|tr|h[1-6]|section|article|table|ul|ol|dd|dt|blockquote|summary|details|figcaption)>/gi, '\n')
    .replace(/<\/t[dh]>/gi, ' ')
    .replace(rx('<[!/?a-zA-Z]§>', 'g'), ' '))
    .split('\n').map(l => l.replace(/\s+/g, ' ').trim()).filter(Boolean).join('\n');
}
const clean = html => html.replace(/<!--[\s\S]*?-->/g, ' ').replace(/<(script|style|noscript|template|svg|iframe|textarea)\b[\s\S]*?<\/\1>/gi, ' ');

function mainOf(html, withTables) {
  const h = clean(html);
  let body = (rx('<body\\b§>([\\s\\S]*)<\\/body>', 'i').exec(h) || [null, h])[1];
  let scope = 'body';
  const m = rx('<main\\b§>([\\s\\S]*)<\\/main>', 'i').exec(body);
  const a = !m && rx('<article\\b§>([\\s\\S]*)<\\/article>', 'i').exec(body);
  if (m) { body = m[1]; scope = 'main'; } else if (a) { body = a[1]; scope = 'article'; }
  const chrome = scope === 'body' ? 'header|nav|footer|aside|form|dialog' : 'nav|aside|form|dialog';
  body = body.replace(new RegExp('<(' + chrome + ')\\b[\\s\\S]*?<\\/\\1>', 'gi'), ' ');
  body = body.replace(rx('<(ol|ul|nav|div)\\b[^>]*?(?:class|id)=["\'][^"\']*(?:breadcrumb|crumb|cookie|modal|popup)[^"\']*["\']§>[\\s\\S]*?<\\/\\1>', 'gi'), ' ');
  const tables = (body.match(/<table\b[\s\S]*?<\/table>/gi) || []).map(toText).join('\n');
  const text = toText(withTables ? body : body.replace(/<table\b[\s\S]*?<\/table>/gi, ' '));
  return { scope, text, tableText: tables };
}

// ---------- 分词与宽度 ----------
const CJK_CLASS = '\\u3040-\\u30ff\\u3400-\\u4dbf\\u4e00-\\u9fff\\uf900-\\ufaff\\uac00-\\ud7af';
const TOKEN_RE = new RegExp('[' + CJK_CLASS + ']|(?:(?![' + CJK_CLASS + '])[\\p{L}\\p{N}])+', 'gu');
const IS_CJK = new RegExp('^[' + CJK_CLASS + ']$', 'u');
const tokenize = s => (s.match(TOKEN_RE) || []).map(t => t.toLowerCase());
function width(s) {
  let w = 0;
  for (const ch of s) w += /[\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6]/.test(ch) ? 2 : 1;
  return w;
}
const measureStr = s => (s == null ? null : { text: s, chars: [...s].length, width: width(s) });

// ---------- 命令 ----------
function headFacts(page, opt) {
  const html = page.html;
  const head = (/<head\b[\s\S]*?<\/head>/i.exec(html) || [html])[0];
  const metas = tagsOf(head, 'meta');
  const meta = n => { const t = metas.find(x => (x.name || x.property || '').toLowerCase() === n); return t ? t.content ?? '' : null; };
  const links = tagsOf(head, 'link');
  const title = (rx('<title\\b§>([\\s\\S]*?)<\\/title>', 'i').exec(head) || [])[1];
  const hs = lvl => [...clean(html).matchAll(rx('<h' + lvl + '\\b§>([\\s\\S]*?)<\\/h' + lvl + '>', 'gi'))].map(x => toText(x[1]).replace(/\n/g, ' '));
  const jsonld = [...html.matchAll(/<script[^>]+type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)].map(x => {
    try {
      const data = JSON.parse(x[1].trim());
      const types = new Set();
      (function walk(o) { if (Array.isArray(o)) return o.forEach(walk); if (o && typeof o === 'object') { if (o['@type']) [].concat(o['@type']).forEach(t => types.add(t)); Object.values(o).forEach(walk); } })(data);
      return { ok: true, types: [...types] };
    } catch (e) { return { ok: false, error: String(e.message).slice(0, 120) }; }
  });
  const base = page.finalUrl || opt.base;
  const host = base ? new URL(base).host : null;
  const anchors = [...clean(html).matchAll(rx('<a\\b(§)>([\\s\\S]*?)<\\/a>', 'gi'))].map(x => ({ href: attrs('<a ' + x[1] + '>').href, text: toText(x[2]).replace(/\n/g, ' ') }));
  const resolved = anchors.filter(a => a.href && !/^(javascript:|mailto:|tel:|#)/i.test(a.href)).map(a => { try { return { ...a, abs: base ? new URL(a.href, base).href : a.href }; } catch { return { ...a, abs: a.href }; } });
  const internal = resolved.filter(a => !host || (/^https?:/i.test(a.abs) && new URL(a.abs).host === host));
  const generic = anchors.filter(a => /^(read more|learn more|more|click here|details|view more|see more|view details|了解更多|查看详情|更多|点击这里|详情|查看更多|[→>»›]+)$/i.test(a.text.trim()));
  const imgs = tagsOf(clean(html), 'img');
  const body = mainOf(html, opt.tables);
  return {
    url: page.url, finalUrl: page.finalUrl, status: page.status, chain: page.chain, ua: page.ua, rendered: page.rendered, htmlBytes: page.bytes,
    lang: (/<html\b[^>]*\blang=["']?([\w-]+)/i.exec(html) || [])[1] || null,
    title: measureStr(title != null ? decodeEntities(title).replace(/\s+/g, ' ').trim() : null),
    description: measureStr(meta('description')),
    metaKeywords: meta('keywords'),
    robots: meta('robots'), xRobotsTag: page.headers['x-robots-tag'] || null,
    canonical: (links.find(l => (l.rel || '').toLowerCase() === 'canonical') || {}).href || null,
    hreflang: links.filter(l => (l.rel || '').toLowerCase() === 'alternate' && l.hreflang).map(l => ({ lang: l.hreflang, href: l.href })),
    og: { title: meta('og:title'), description: meta('og:description'), image: meta('og:image') },
    twitter: { title: meta('twitter:title'), description: meta('twitter:description') },
    h1: hs(1), h2: hs(2), h3Count: hs(3).length,
    jsonld,
    links: { total: anchors.length, internal: internal.length, uniqueInternal: new Set(internal.map(a => a.abs.split('#')[0])).size, external: resolved.length - internal.length, genericAnchorCount: generic.length, genericAnchors: generic.slice(0, 20) },
    // 空 alt（alt=""）是装饰图的正确写法，单独计数；缺 alt 属性才算问题，内容图是否该有文字要人工看
    images: { total: imgs.length, missingAlt: imgs.filter(i => !('alt' in i)).length, emptyAlt: imgs.filter(i => 'alt' in i && !i.alt.trim()).length },
    body: { scope: body.scope, words: tokenize(body.text).length, tableWords: tokenize(body.tableText).length, tablesCounted: !!opt.tables, first300: body.text.slice(0, 300) },
    _internalLinks: internal,
  };
}

function density(text, terms) {
  const tokens = tokenize(text);
  const groups = ['core', 'variant', 'derived', 'longtail'];
  const list = [];
  for (const g of groups) for (const t of terms[g] || []) list.push({ term: t, group: g, toks: tokenize(t) });
  const occ = [];
  const raw = new Map();
  for (const t of list) {
    if (!t.toks.length) continue;
    let n = 0;
    for (let i = 0; i + t.toks.length <= tokens.length; i++) {
      let ok = true;
      for (let j = 0; j < t.toks.length; j++) if (tokens[i + j] !== t.toks[j]) { ok = false; break; }
      if (ok) { occ.push({ i, len: t.toks.length, t }); n++; }
    }
    raw.set(t.term, n);
  }
  occ.sort((a, b) => b.len - a.len || a.i - b.i);
  const used = new Uint8Array(tokens.length);
  let matched = 0;
  for (const o of occ) {
    let free = true;
    for (let k = o.i; k < o.i + o.len; k++) if (used[k]) { free = false; break; }
    if (!free) continue;
    for (let k = o.i; k < o.i + o.len; k++) used[k] = 1;
    matched += o.len;
  }
  const total = tokens.length || 1;
  const pct = x => Math.round(x * 10000) / 100;
  const coverage = {};
  for (const g of groups) {
    const items = list.filter(t => t.group === g);
    if (items.length) coverage[g] = { found: items.filter(t => raw.get(t.term) > 0).length, total: items.length, pct: pct(items.filter(t => raw.get(t.term) > 0).length / items.length) };
  }
  let volumeWeighted = null;
  if (terms.volume) {
    const vs = Object.entries(terms.volume).map(([t, v]) => ({ w: Math.log10((+v || 0) + 1), hit: (raw.get(t) ?? countRaw(tokens, tokenize(t))) > 0 }));
    const sum = vs.reduce((s, x) => s + x.w, 0);
    volumeWeighted = sum ? pct(vs.filter(x => x.hit).reduce((s, x) => s + x.w, 0) / sum) : null;
  }
  const first100 = new Set();
  const coreTerms = list.filter(t => t.group === 'core');
  for (const o of occ) if (o.t.group === 'core' && o.i < 100) first100.add(o.t.term);
  return {
    words: tokens.length,
    themeDensityPct: pct(matched / total),
    coreDensityPct: Object.fromEntries(coreTerms.map(t => [t.term, pct((raw.get(t.term) || 0) / total)])),
    maxSingleTermDensityPct: Math.max(0, ...list.map(t => pct((raw.get(t.term) || 0) / total))),
    coreInFirst100Words: [...first100],
    coverage, volumeWeightedCoveragePct: volumeWeighted,
    counts: Object.fromEntries(list.map(t => [t.term, raw.get(t.term) || 0])),
    missing: list.filter(t => !raw.get(t.term)).map(t => t.term),
  };
}
function countRaw(tokens, toks) {
  let n = 0;
  for (let i = 0; i + toks.length <= tokens.length; i++) { let ok = toks.length > 0; for (let j = 0; j < toks.length; j++) if (tokens[i + j] !== toks[j]) { ok = false; break; } if (ok) n++; }
  return n;
}

function overlap(textA, others, nOpt) {
  const A = tokenize(textA);
  const cjkShare = A.filter(t => IS_CJK.test(t)).length / (A.length || 1);
  const n = +nOpt || (cjkShare > 0.5 ? 10 : 6);
  const sh = toks => { const s = new Set(); for (let i = 0; i + n <= toks.length; i++) s.add(toks.slice(i, i + n).join(' ')); return s; };
  const aList = []; for (let i = 0; i + n <= A.length; i++) aList.push(A.slice(i, i + n).join(' '));
  const per = [];
  const union = new Set();
  for (const o of others) {
    const s = sh(tokenize(o.text));
    s.forEach(x => union.add(x));
    const hit = aList.filter(x => s.has(x)).length;
    per.push({ against: o.label, overlapPct: aList.length ? Math.round(hit / aList.length * 10000) / 100 : 0 });
  }
  const covered = new Uint8Array(A.length);
  aList.forEach((x, i) => { if (union.has(x)) for (let k = i; k < i + n; k++) covered[k] = 1; });
  const hitAll = aList.filter(x => union.has(x)).length;
  return { shingleSize: n, words: A.length, uniqueWords: A.length - covered.reduce((s, x) => s + x, 0), unionOverlapPct: aList.length ? Math.round(hitAll / aList.length * 10000) / 100 : 0, maxOverlapPct: Math.max(0, ...per.map(p => p.overlapPct)), perPage: per };
}

async function pool(items, k, fn) {
  const out = new Array(items.length); let next = 0;
  await Promise.all(Array.from({ length: Math.min(k, items.length) }, async () => { while (next < items.length) { const i = next++; out[i] = await fn(items[i]); } }));
  return out;
}

// ---------- 主程序 ----------
const { cmd, target, opt } = parseArgs(process.argv.slice(2));
const out = o => process.stdout.write(JSON.stringify(o, null, 2) + '\n');
try {
  if (!cmd || !target) throw new Error('用法见文件头注释');
  if (cmd === 'facts') {
    const f = headFacts(await load(target, opt), opt); delete f._internalLinks; out(f);
  } else if (cmd === 'text') {
    const p = await load(target, opt); const m = mainOf(p.html, opt.tables);
    process.stdout.write(`# scope=${m.scope} words=${tokenize(m.text).length} tableWords=${tokenize(m.tableText).length}\n${m.text}\n`);
  } else if (cmd === 'density') {
    if (!opt.terms) throw new Error('缺 --terms 词表.json');
    const p = await load(target, opt); const m = mainOf(p.html, opt.tables);
    out({ target, scope: m.scope, tablesCounted: !!opt.tables, ...density(m.text, JSON.parse(fs.readFileSync(opt.terms, 'utf8'))) });
  } else if (cmd === 'overlap') {
    if (!opt.against.length) throw new Error('缺 --against');
    const pa = await load(target, opt);
    const a = mainOf(pa.html, opt.tables).text;
    const others = [], warnings = [];
    if (pa.status && pa.status !== 200) warnings.push(`目标页返回 ${pa.status}`);
    for (const t of opt.against) {
      const p = await load(t, opt);
      if (p.status && p.status !== 200) { warnings.push(`对照页返回 ${p.status}，已跳过：${t}`); continue; }
      others.push({ label: t, text: mainOf(p.html, opt.tables).text });
    }
    out({ target, warnings, ...overlap(a, others, opt.n) });
  } else if (cmd === 'uacheck') {
    const rows = [];
    for (const name of Object.keys(UAS)) {
      try {
        const p = await load(target, { ...opt, render: false }, name);
        const f = headFacts(p, opt);
        rows.push({ ua: name, status: p.status, chain: p.chain.map(c => c.status + (c.location ? ' → ' + c.location : '')).join(' | '), finalUrl: p.finalUrl, title: f.title && f.title.text, robots: f.robots, canonical: f.canonical, h1: f.h1[0] || null, bodyWords: f.body.words, htmlBytes: p.bytes });
      } catch (e) { rows.push({ ua: name, error: String(e.message) }); }
    }
    const ref = rows.find(r => r.ua === 'desktop') || {};
    for (const r of rows) {
      if (r.error || r === ref) continue;
      const d = [];
      if (r.finalUrl !== ref.finalUrl) d.push('最终网址不同');
      if (r.status !== ref.status) d.push('状态码不同');
      if (r.title !== ref.title) d.push('Title 不同');
      if ((r.robots || '') !== (ref.robots || '')) d.push('robots 不同');
      if (ref.bodyWords && Math.abs(r.bodyWords - ref.bodyWords) / ref.bodyWords > 0.3) d.push('正文词数相差超过 30%');
      r.diffFromDesktop = d;
    }
    out({ target, rows });
  } else if (cmd === 'links') {
    const p = await load(target, opt); const f = headFacts(p, opt);
    const urls = [...new Set(f._internalLinks.map(a => a.abs.split('#')[0]))].slice(0, +opt.limit || 300);
    const res = await pool(urls, +opt.concurrency || 4, async u => {
      try {
        let r = await fetch(u, { method: 'HEAD', redirect: 'manual', headers: { 'user-agent': UAS.desktop } });
        if ([403, 405, 501].includes(r.status)) r = await fetch(u, { method: 'GET', redirect: 'manual', headers: { 'user-agent': UAS.desktop } });
        return { url: u, status: r.status, location: r.headers.get('location') || undefined };
      } catch (e) { return { url: u, error: String(e.message) }; }
    });
    const bad = res.filter(r => r.error || r.status >= 400);
    const redirects = res.filter(r => r.status >= 300 && r.status < 400);
    out({ target, checked: res.length, ok: res.filter(r => r.status === 200).length, redirects, bad });
  } else throw new Error('未知命令：' + cmd);
} catch (e) {
  process.stderr.write('错误：' + e.message + '\n');
  process.exit(1);
}
