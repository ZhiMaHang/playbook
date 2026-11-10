#!/usr/bin/env node
// 点击深度、站内入链、孤岛页、锚文本。无依赖，Node 18+。口径见 references/site-structure.md 第 3 节。
//
// 用法（输出 JSON 到标准输出）：
//   node click_depth.mjs <首页网址> [--sitemap <网址|文件>]... [--limit 500] [--max-depth 10]
//        [--concurrency 3] [--delay 300] [--exclude <正则>] [--strip-query]
//        [--ua desktop|googlebot|bingbot|baiduspider] [--ua-string <UA 全文>] [--ignore-robots]
//        [--csv <输出.csv>]
//
// 口径：
// - 从首页按广度优先抓，只跟同一主机名下、带 href 的 <a>；记下的深度就是「从首页最少点几次」。
//   跟随跳转不算一次点击。
// - 看服务器返回的源代码：脚本插入的链接、只靠点击事件的按钮不算。站点导航全靠前端渲染时结果会偏深，
//   要用浏览器抽查，并在报告里写明口径。注释、<script>、<style>、<template> 里的链接不算。
// - 默认遵守 robots.txt（按 --ua 选 User-agent 组，没有就用 *）；被禁止的网址记为「robots 禁止」，不抓。
// - 入链数 = 链向它的不同页面数（不含自己）。锚文本按文字归并计数，图片链接取 alt，都没有记为「（空）」。
// - 给了站点地图：孤岛页 = 在站点地图里、抓取中没有任何页面链向它、也不是跳转终点的网址。
//   抓取达到 --limit 或 --max-depth 时孤岛页结果不完整，warnings 里会提示。
// - 文件类链接（pdf、图片、压缩包、CAD 等）记下深度和入链，不下载。
// 只抓自己负责、得到授权的站。走代理：Node 24+ 设环境变量 NODE_USE_ENV_PROXY=1。
// Windows 的 Git Bash 会把以 / 开头的参数（如 --exclude '/search/'）改写成磁盘路径：命令前加 MSYS_NO_PATHCONV=1。

import fs from 'node:fs';

const UAS = {
  desktop: ['Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36', ''],
  googlebot: ['Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)', 'googlebot'],
  bingbot: ['Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)', 'bingbot'],
  baiduspider: ['Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)', 'baiduspider'],
};

const GENERIC = /^(click here|here|read more|learn more|more|more info|details?|view (more|details)|see more|go|›|»|→|>+|点击这里|点此|点击|这里|更多|了解更多|查看更多|查看详情|详情|进入)$/i;
const FILE_EXT = /\.(pdf|jpe?g|png|gif|webp|svg|ico|bmp|tiff?|zip|rar|7z|gz|tar|mp4|mov|avi|mp3|wav|docx?|xlsx?|pptx?|csv|step|stp|igs|iges|dwg|dxf|stl|exe|msi|dmg|apk)$/i;

function parseArgs(argv) {
  const pos = [], opt = { sitemap: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) { pos.push(a); continue; }
    const k = a.slice(2);
    if (['strip-query', 'ignore-robots'].includes(k)) { opt[k] = true; continue; }
    const v = argv[++i];
    if (k === 'sitemap') opt.sitemap.push(v); else opt[k] = v;
  }
  return { start: pos[0], opt };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function decodeEntities(s) {
  return s
    .replace(/&#x([0-9a-f]+);/gi, (_, h) => String.fromCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(Number(d)))
    .replace(/&nbsp;/g, ' ').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&#39;|&apos;/g, "'").replace(/&amp;/g, '&');
}

function decodeBody(buf, contentType) {
  let cs = (/charset=([\w-]+)/i.exec(contentType || '') || [])[1];
  if (!cs) cs = (/<meta[^>]+charset=["']?([\w-]+)/i.exec(buf.subarray(0, 4096).toString('latin1')) || [])[1];
  try { return new TextDecoder((cs || 'utf-8').toLowerCase()).decode(buf); } catch { return buf.toString('utf8'); }
}

// 手动跟跳转，记下每一跳的状态码（301 / 302 …），最多 10 跳
async function get(url, ua) {
  let cur = url;
  const chain = [];
  for (let hop = 0; hop <= 10; hop++) {
    const res = await fetch(cur, {
      headers: { 'user-agent': ua, accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8' },
      redirect: 'manual',
      signal: AbortSignal.timeout(20000),
    });
    const loc = res.headers.get('location');
    if (res.status >= 300 && res.status < 400 && loc) {
      chain.push(res.status);
      await res.body?.cancel();
      cur = new URL(loc, cur).href;
      continue;
    }
    const ct = res.headers.get('content-type') || '';
    let body = '';
    if (/html|xml|text\/plain/i.test(ct)) body = decodeBody(Buffer.from(await res.arrayBuffer()), ct);
    else await res.body?.cancel();
    return { status: res.status, finalUrl: cur, chain, ct, body };
  }
  return { status: 'ERR', finalUrl: cur, chain, ct: '', body: '', tooMany: true };
}

// ---------- robots.txt ----------
function parseRobots(txt, token) {
  const groups = [];
  let cur = null, lastUA = false;
  for (let line of txt.split(/\r?\n/)) {
    line = line.replace(/#.*/, '').trim();
    const i = line.indexOf(':');
    if (!line || i < 0) continue;
    const k = line.slice(0, i).trim().toLowerCase(), v = line.slice(i + 1).trim();
    if (k === 'user-agent') {
      if (!cur || !lastUA) { cur = { agents: [], rules: [] }; groups.push(cur); }
      cur.agents.push(v.toLowerCase());
      lastUA = true;
    } else {
      lastUA = false;
      if (cur && (k === 'allow' || k === 'disallow') && v) cur.rules.push({ allow: k === 'allow', path: v });
    }
  }
  let chosen = token ? groups.filter((g) => g.agents.some((a) => a !== '*' && token.includes(a))) : [];
  if (!chosen.length) chosen = groups.filter((g) => g.agents.includes('*'));
  return chosen.flatMap((g) => g.rules).map((r) => {
    const anchored = r.path.endsWith('$');
    const body = (anchored ? r.path.slice(0, -1) : r.path).split('*').map((s) => s.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*');
    return { allow: r.allow, len: r.path.length, re: new RegExp('^' + body + (anchored ? '$' : '')) };
  });
}

function robotsAllows(rules, url) {
  const u = new URL(url);
  const p = u.pathname + u.search;
  let best = null;
  for (const r of rules) {
    if (!r.re.test(p)) continue;
    if (!best || r.len > best.len || (r.len === best.len && r.allow)) best = r;
  }
  return !best || best.allow;
}

// ---------- 解析 ----------
function stripNoise(html) {
  return html
    .replace(/<!--[\s\S]*?-->/g, ' ')
    .replace(/<(script|style|template)\b[^>]*>[\s\S]*?<\/\1\s*>/gi, ' ');
}

function textOf(fragment) {
  return decodeEntities(fragment.replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim();
}

function extractLinks(html, pageUrl) {
  let base = pageUrl;
  const b = /<base\b[^>]*\bhref\s*=\s*(?:"([^"]*)"|'([^']*)')/i.exec(html);
  if (b) { try { base = new URL(decodeEntities(b[1] ?? b[2]), pageUrl).href; } catch { /* 用页面网址 */ } }
  const out = [];
  for (const m of html.matchAll(/<a\b([^>]*)>([\s\S]*?)<\/a\s*>/gi)) {
    const h = /\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/i.exec(m[1]);
    if (!h) continue; // 没有 href：不可抓取
    const raw = decodeEntities((h[1] ?? h[2] ?? h[3] ?? '').trim());
    if (!raw || raw.startsWith('#') || /^(javascript|mailto|tel|data|sms):/i.test(raw)) continue;
    let u;
    try { u = new URL(raw, base); } catch { continue; }
    if (!/^https?:$/.test(u.protocol)) continue;
    u.hash = '';
    let text = textOf(m[2]);
    if (!text) {
      const alt = /<img\b[^>]*\balt\s*=\s*(?:"([^"]*)"|'([^']*)')/i.exec(m[2]);
      text = alt ? decodeEntities((alt[1] ?? alt[2]).trim()) : '';
    }
    if (!text) {
      const t = /\b(?:aria-label|title)\s*=\s*(?:"([^"]*)"|'([^']*)')/i.exec(m[1]);
      text = t ? `[${decodeEntities((t[1] ?? t[2]).trim())}]` : '';
    }
    out.push({ url: u.href, text: text || '（空）' });
  }
  return out;
}

async function loadSitemap(src, ua, warnings, level = 0) {
  let text = '';
  try {
    if (/^https?:/i.test(src)) {
      if (/\.gz$/i.test(src)) { warnings.push(`站点地图 ${src} 是 .gz 压缩包，没有读取；请先解压成文件再用 --sitemap 文件路径`); return []; }
      const r = await get(src, ua);
      if (r.status >= 400) { warnings.push(`站点地图 ${src} 返回 ${r.status}`); return []; }
      text = r.body;
    } else text = fs.readFileSync(src, 'utf8');
  } catch (e) { warnings.push(`读不到站点地图 ${src}：${e.cause?.code || e.code || e.message}`); return []; }
  const locs = [...text.matchAll(/<loc>\s*([\s\S]*?)\s*<\/loc>/gi)]
    .map((m) => decodeEntities(m[1].replace(/<!\[CDATA\[|\]\]>/g, '').trim()));
  if (!locs.length) return text.split(/\r?\n/).map((s) => s.trim()).filter((s) => /^https?:\/\//i.test(s));
  if (/<sitemapindex/i.test(text)) {
    if (level >= 2) { warnings.push(`站点地图索引嵌套过深，跳过 ${src}`); return []; }
    const all = [];
    for (const child of locs.slice(0, 200)) all.push(...await loadSitemap(child, ua, warnings, level + 1));
    return all;
  }
  return locs;
}

async function pool(items, k, fn) {
  let i = 0;
  await Promise.all(Array.from({ length: Math.min(k, items.length) }, async () => {
    while (i < items.length) { const idx = i++; await fn(items[idx]); }
  }));
}

function csvCell(v) {
  const s = v === undefined || v === null ? '' : String(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

async function main() {
  const { start, opt } = parseArgs(process.argv.slice(2));
  if (!start || !/^https?:\/\//i.test(start)) {
    console.error('用法：node click_depth.mjs <首页网址> [--sitemap <网址|文件>] [--limit 500] [--csv 输出.csv]');
    process.exit(2);
  }
  const limit = Number(opt.limit || 500);
  const maxDepth = Number(opt['max-depth'] || 10);
  const conc = Math.max(1, Number(opt.concurrency || 3));
  const delay = Number(opt.delay ?? 300);
  const exclude = opt.exclude ? new RegExp(opt.exclude) : null;
  const [uaString, uaToken] = opt['ua-string'] ? [opt['ua-string'], ''] : (UAS[opt.ua || 'desktop'] || UAS.desktop);
  const warnings = [];

  let startUrl = new URL(start);
  startUrl.hash = '';
  // 首页跳到另一个主机名（如不带 www → 带 www）时，以跳转后的主机名为准
  try {
    const pre = await get(startUrl.href, uaString);
    const fin = new URL(pre.finalUrl);
    if (fin.hostname.toLowerCase() !== startUrl.hostname.toLowerCase()) {
      warnings.push(`首页 ${startUrl.href} 跳转到 ${fin.href}，按 ${fin.hostname} 抓取`);
      startUrl = fin;
      startUrl.hash = '';
    }
  } catch (e) { warnings.push(`首页请求失败：${e.cause?.code || e.message}`); }
  const host = startUrl.hostname.toLowerCase();
  const norm = (href) => {
    const u = new URL(href);
    u.hash = '';
    if (opt['strip-query']) u.search = '';
    return u.href;
  };
  const isInternal = (href) => { try { return new URL(href).hostname.toLowerCase() === host; } catch { return false; } };

  let robots = [];
  if (!opt['ignore-robots']) {
    try {
      const r = await get(new URL('/robots.txt', startUrl).href, uaString);
      if (r.status < 400 && r.body) robots = parseRobots(r.body, uaToken);
    } catch (e) { warnings.push(`读不到 robots.txt：${e.cause?.code || e.message}；按全部允许处理`); }
  }

  const pages = new Map();
  const node = (url) => {
    if (!pages.has(url)) pages.set(url, { url, depth: undefined, status: null, note: '', from: new Set(), anchors: new Map(), firstParent: '', redirectTo: '', redirectTarget: false, fetched: false });
    return pages.get(url);
  };

  const first = norm(startUrl.href);
  node(first).depth = 0;
  let frontier = [first];
  let fetchedCount = 0, truncated = false, depthCut = false;
  const linksToRedirect = new Map(); // 目标网址 -> 链向它的页面数

  for (let depth = 0; frontier.length; depth++) {
    if (depth > maxDepth) { depthCut = true; for (const u of frontier) pages.get(u).note ||= `超过 --max-depth ${maxDepth}，未抓`; break; }
    const batch = [];
    for (const u of frontier) {
      const p = pages.get(u);
      if (p.fetched) continue;
      if (FILE_EXT.test(new URL(u).pathname)) { p.note = '文件'; continue; }
      if (exclude && exclude.test(u)) { p.note = '按 --exclude 跳过'; continue; }
      if (!robotsAllows(robots, u)) { p.note = 'robots 禁止'; continue; }
      if (fetchedCount >= limit) { truncated = true; p.note ||= `达到 --limit ${limit}，未抓`; continue; }
      fetchedCount++;
      p.fetched = true;
      batch.push(u);
    }
    const next = [];
    await pool(batch, conc, async (u) => {
      const p = pages.get(u);
      let r;
      try { r = await get(u, uaString); } catch (e) { p.status = 'ERR'; p.note = String(e.cause?.code || e.name || e.message); return; }
      p.status = r.chain.length ? r.chain[0] : r.status;
      if (r.tooMany) { p.note = '跳转超过 10 次'; return; }
      let src = u;
      if (r.chain.length) {
        const fin = norm(r.finalUrl);
        const notes = [];
        if (r.chain.length > 1) notes.push(`跳转 ${r.chain.length} 次（${r.chain.join(' → ')}）`);
        if (r.chain.some((c) => c !== 301 && c !== 308)) notes.push('有临时跳转');
        p.note = notes.join('；');
        if (fin !== u) {
          p.redirectTo = fin;
          if (!isInternal(fin)) { p.note = [p.note, '跳到站外'].filter(Boolean).join('；'); return; }
          const q = node(fin);
          q.redirectTarget = true;
          if (q.depth === undefined || q.depth > depth) { q.depth = depth; q.firstParent ||= `${u}（跳转）`; } // 跟随跳转不算一次点击
          if (q.fetched) return;
          q.fetched = true;
          q.status = r.status;
          src = fin;
        }
      }
      if (r.status >= 400 || !/html/i.test(r.ct)) { if (!/html/i.test(r.ct) && r.status < 400) p.note ||= `非 HTML（${r.ct.split(';')[0] || '未知类型'}）`; return; }
      const links = extractLinks(stripNoise(r.body), r.finalUrl || u);
      for (const l of links) {
        if (!isInternal(l.url)) continue;
        let t;
        try { t = norm(l.url); } catch { continue; }
        if (t === src || t === u) continue;
        const q = node(t);
        q.from.add(src);
        q.anchors.set(l.text, (q.anchors.get(l.text) || 0) + 1);
        if (q.depth === undefined) { q.depth = depth + 1; q.firstParent = src; next.push(t); }
      }
      if (delay) await sleep(delay);
    });
    frontier = [...new Set(next)];
  }

  // 指向跳转网址的站内链接
  for (const p of pages.values()) if (p.redirectTo && p.from.size) linksToRedirect.set(p.url, { redirectTo: p.redirectTo, linkedFrom: p.from.size });

  // 站点地图
  let sitemapSet = null, orphans = [], viaRedirectOnly = [], notInSitemap = [], slashMismatch = [];
  if (opt.sitemap.length) {
    const urls = [];
    for (const s of opt.sitemap) urls.push(...await loadSitemap(s, uaString, warnings));
    sitemapSet = new Set();
    for (const s of urls) { try { if (isInternal(s)) sitemapSet.add(norm(s)); } catch { /* 跳过坏网址 */ } }
    if (!sitemapSet.size) warnings.push('站点地图里没有读到本主机名的网址');
    const alt = (u) => { const x = new URL(u); x.pathname = x.pathname.endsWith('/') ? x.pathname.replace(/\/+$/, '') || '/' : x.pathname + '/'; return x.href; };
    for (const s of sitemapSet) {
      if (s === first) continue;
      const p = pages.get(s);
      if (p && p.from.size) continue;
      if (p && p.redirectTarget) { viaRedirectOnly.push(s); continue; }
      const a = pages.get(alt(s));
      if (a && a.from.size) { slashMismatch.push({ sitemap: s, linkedAs: a.url }); continue; }
      orphans.push(s);
    }
    for (const p of pages.values()) {
      if (p.status === 200 && !p.redirectTo && p.fetched && !sitemapSet.has(p.url) && !FILE_EXT.test(new URL(p.url).pathname)) notInSitemap.push(p.url);
    }
    if (truncated || depthCut) warnings.push('抓取没有走完全站（达到 --limit 或 --max-depth），孤岛页清单不完整：清单里的网址可能只是还没抓到');
  }
  if (truncated) warnings.push(`抓取达到 --limit ${limit}，还有页面没抓；需要全量时调大 --limit`);
  if (fetchedCount <= 1) warnings.push('只抓到首页：检查首页是不是靠脚本渲染导航、是否被防火墙拦截、robots 是否禁止');

  const list = [...pages.values()].sort((a, b) => (a.depth ?? 99) - (b.depth ?? 99) || a.url.localeCompare(b.url));
  const out = list.map((p) => {
    const anchors = [...p.anchors.entries()].sort((a, b) => b[1] - a[1]);
    return {
      url: p.url,
      depth: p.depth ?? null,
      status: p.status,
      inlinks: p.from.size,
      inSitemap: sitemapSet ? sitemapSet.has(p.url) : null,
      firstParent: p.firstParent || null,
      redirectTo: p.redirectTo || null,
      anchorVariants: anchors.length,
      topAnchors: anchors.slice(0, 5),
      note: p.note || null,
    };
  });

  const genericAnchors = [];
  for (const p of pages.values()) for (const [text, n] of p.anchors) if (GENERIC.test(text) || text === '（空）') genericAnchors.push({ target: p.url, text, count: n });
  genericAnchors.sort((a, b) => b.count - a.count);

  const hist = {};
  for (const p of out) if (p.depth !== null) hist[p.depth] = (hist[p.depth] || 0) + 1;

  const result = {
    start: first,
    measured: new Date().toISOString(),
    method: '源代码中带 href 的 <a>；广度优先；跟随跳转不计点击' + (opt['ignore-robots'] ? '；未遵守 robots.txt' : '；遵守 robots.txt'),
    summary: {
      fetched: fetchedCount,
      discovered: pages.size,
      truncated,
      depthHistogram: hist,
      deeperThan3: out.filter((p) => p.depth > 3).length,
      non200: out.filter((p) => p.status !== null && p.status !== 200).length,
      robotsBlocked: out.filter((p) => p.note === 'robots 禁止').length,
      linksToRedirect: linksToRedirect.size,
      genericAnchorLinks: genericAnchors.reduce((n, g) => n + g.count, 0),
      orphans: sitemapSet ? orphans.length : null,
    },
    warnings,
    pages: out,
    orphans,
    reachedOnlyViaRedirect: viaRedirectOnly,
    sitemapSlashMismatch: slashMismatch,
    notInSitemap: notInSitemap.slice(0, 500),
    linksToRedirect: [...linksToRedirect.entries()].map(([url, v]) => ({ url, ...v })),
    genericAnchors: genericAnchors.slice(0, 500),
  };

  if (opt.csv) {
    const head = ['url', 'depth', 'status', 'inlinks', 'in_sitemap', 'first_parent', 'redirect_to', 'anchor_variants', 'top_anchor', 'note'];
    const lines = [head.join(',')].concat(out.map((p) => [p.url, p.depth, p.status, p.inlinks, p.inSitemap, p.firstParent, p.redirectTo, p.anchorVariants, p.topAnchors[0] ? `${p.topAnchors[0][0]} ×${p.topAnchors[0][1]}` : '', p.note].map(csvCell).join(',')));
    fs.writeFileSync(opt.csv, '﻿' + lines.join('\r\n') + '\r\n');
  }
  console.log(JSON.stringify(result, null, 2));
}

main().catch((e) => { console.error(e); process.exit(1); });
