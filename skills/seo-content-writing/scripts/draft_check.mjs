#!/usr/bin/env node
// draft_check.mjs：稿件发布前的字面检查（Node 18+，无依赖）
//
// 查什么：
//   FAIL  残留的「待核实」类标记和模板占位；--banned 文件里的站点禁用词
//   WARN  通用风险说法（替代打包票、最高级、服务和商务承诺、年限、整机认证结论）、
//         会过期的数字、无出处的套话、没写来源的百分比、正文里的电话邮箱和变形写法、
//         「参数见图片 / 规格书」、弱锚文字、首句没有主题词、全文没有链接
//   INFO  首句、小标题、字数（只作参考，不是指标）
// 只查字面：口径逐句对照、参数核对规格书，仍要人工做。WARN 逐条处理：改掉，或写明口径文件里的依据。
//
// 用法：node draft_check.mjs <稿件.md|.html|.txt|->  [选项]     （- 表示从标准输入读）
//   --topic "主题词"     可重复；首句含其中任意一个即通过（同义叫法一起给）
//   --banned <文件>      站点禁用词，一行一个，# 开头为注释；命中记 FAIL
//   --allow "说法"       可重复；口径文件登记过的例外（如 "best practice"），命中时不报
//   --only "标题文字"    只查这个小标题下面的内容（例如交稿包的「2. 正文」）
//   --lang zh|en         不给就自动判断
//   --json               输出 JSON
// 退出码：有 FAIL 返回 1；否则 0；参数错误返回 2。

import fs from 'node:fs';

const HELP = (() => {
  const src = fs.readFileSync(new URL(import.meta.url), 'utf8').split(/\r?\n/).slice(1);
  const end = src.findIndex(l => !l.startsWith('//'));
  return src.slice(0, end < 0 ? src.length : end).map(l => l.replace(/^\/\/ ?/, '')).join('\n');
})();

const opt = { topics: [], allow: [], banned: null, only: null, lang: null, json: false, file: null };
const argv = process.argv.slice(2);
for (let i = 0; i < argv.length; i++) {
  const a = argv[i];
  const next = () => { if (i + 1 >= argv.length) bail(`${a} 缺少参数`); return argv[++i]; };
  if (a === '--help' || a === '-h') { console.log(HELP); process.exit(0); }
  else if (a === '--topic') opt.topics.push(next());
  else if (a === '--allow') opt.allow.push(next().toLowerCase());
  else if (a === '--banned') opt.banned = next();
  else if (a === '--only') opt.only = next();
  else if (a === '--lang') opt.lang = next();
  else if (a === '--json') opt.json = true;
  else if (!opt.file) opt.file = a;
  else bail(`多余的参数：${a}`);
}
function bail(msg) { console.error(`错误：${msg}\n用 --help 看用法`); process.exit(2); }
if (!opt.file) bail('没有给稿件文件');

let raw;
try { raw = opt.file === '-' ? fs.readFileSync(0, 'utf8') : fs.readFileSync(opt.file, 'utf8'); }
catch (e) { bail(`读不了 ${opt.file}：${e.message}`); }
raw = raw.replace(/^﻿/, '');

// 去掉不上线的部分，保留行号：HTML 注释、代码块、脚本样式、开头的 YAML 头
const blank = m => m.replace(/[^\n]/g, ' ');
let text = raw.replace(/\r\n?/g, '\n')
  .replace(/<!--[\s\S]*?-->/g, blank)
  .replace(/<(script|style)\b[\s\S]*?<\/\1>/gi, blank)
  .replace(/^```[\s\S]*?^```/gm, blank)
  .replace(/^---\n[\s\S]*?\n---\n/, blank);
let lines = text.split('\n');

const headingOf = l => {
  let m = l.match(/^(#{1,6})\s+(.*)$/); if (m) return { level: m[1].length, text: m[2].trim() };
  m = l.match(/<h([1-6])[^>]*>([\s\S]*?)<\/h\1>/i); if (m) return { level: +m[1], text: strip(m[2]).trim() };
  return null;
};
function strip(s) {
  return s.replace(/<[^>]+>/g, ' ').replace(/!\[[^\]]*\]\([^)]*\)/g, ' ').replace(/\]\([^)]*\)/g, ']')
    .replace(/\*\*|__|`/g, '')
    .replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
}

if (opt.only) {
  const start = lines.findIndex(l => { const h = headingOf(l); return h && h.text.includes(opt.only); });
  if (start < 0) bail(`找不到小标题「${opt.only}」`);
  const lv = headingOf(lines[start]).level;
  lines = lines.map((l, i) => {
    if (i <= start) return '';
    const before = lines.slice(start + 1, i + 1).some(x => { const h = headingOf(x); return h && h.level <= lv; });
    return before ? '' : l;
  });
}

const vis = lines.map(strip);
const body = vis.join('\n');
const cjk = (body.match(/[一-鿿]/g) || []).length;
const words = (body.match(/[A-Za-z][A-Za-z'-]*/g) || []).length;
const lang = opt.lang || (cjk > words ? 'zh' : 'en');

const out = { file: opt.file, lang, fail: [], warn: [], info: [] };
const ctx = (s, idx, len) => s.slice(Math.max(0, idx - 18), idx + len + 18).trim();
const allowed = (line, hit) => opt.allow.some(a => a.includes(hit.toLowerCase()) && line.toLowerCase().includes(a));
function scan(level, re, why) {
  vis.forEach((s, i) => {
    for (const m of s.matchAll(new RegExp(re.source, re.flags.includes('g') ? re.flags : re.flags + 'g'))) {
      if (allowed(s, m[0])) continue;
      out[level].push({ line: i + 1, hit: m[0], why, context: ctx(s, m.index, m[0].length) });
    }
  });
}

// FAIL：残留标记和占位
scan('fail', /待核实|待确认|待补充?|待技术审核|待口径确认|待定|TODO|TBD|\[TO VERIFY\]|to be (?:verified|confirmed)/i, '残留标记：发布前清零，核实不了的句子删掉');
scan('fail', /〔[^〕]*〕|\b\d{4}-xx-xx\b/i, '模板占位没填');

// FAIL：站点禁用词
if (opt.banned) {
  let list;
  try { list = fs.readFileSync(opt.banned, 'utf8').replace(/^﻿/, '').split(/\r?\n/).map(s => s.trim()).filter(s => s && !s.startsWith('#')); }
  catch (e) { bail(`读不了禁用词文件 ${opt.banned}：${e.message}`); }
  for (const w of list) {
    const esc = w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '[\\s-]+');
    const re = /^[\x00-\x7f]+$/.test(w) ? new RegExp(`(?<![A-Za-z])${esc}(?![A-Za-z])`, 'i') : new RegExp(esc, 'i');
    scan('fail', re, '站点禁用词（口径文件）');
  }
}

// WARN：通用风险说法
scan('warn', /pin[\s-]?to[\s-]?pin|drop[\s-]?in(?: replacement)?|direct(?:ly)? replace(?:ment|s|d)?|fully compatible|100\s?% compatible|直接替换|直接替代|完全兼容|完全替代|无需改板/i,
  '替代打包票：改成「替代候选 + 量产前用样品验证」；口径登记的例外用 --allow');
scan('warn', /\bbest\b|\bleading\b|world[\s-]class|industry[\s-]leading|\bNo\.\s?1\b|number one|#1\b|行业领先|全球领先|国内领先|领先的|顶级|最好的|最佳|最优|行业第一|全国第一|全球第一|排名第一|第一品牌/i,
  '最高级或无法核实的比较：改写成参数、证书、能力等事实');
scan('warn', /ultimate guide|终极指南|史上最全|全网最全/i, '标题或正文夸张：Google 自查问题要求不夸张');
scan('warn', /free samples?|免费样品|免费寄样|免费送样|\bMOQ\b|minimum order|起订量|最小起订|ship(?:s|ped)? within|\d+\s?(?:hours?|小时)(?:内)?发货|当天发货|现货秒发|in stock|库存充足|lead[\s-]time|交期|\bprice[sd]?\b|\bpricing\b|价格|报价|最低价|cheapest/i,
  '服务或商务承诺：只用口径文件登记的原句，没登记的不写');
scan('warn', /\d+\+?\s*years? of experience|\d+\s*年(?:以上)?(?:的)?(?:行业|生产|研发)?经验|多年经验/i, '年限说法：口径文件没登记就不写');
scan('warn', /guaranteed? to pass|ensures? (?:full )?compliance|保证通过|确保通过|确保整机/i, '替客户下整机认证结论：只写器件自身参数和测试报告');
scan('warn', /官网/, '「官网」：百度细雨算法打击标题滥用「官网」，正文也少用');

// WARN：无出处的套话（同一行没有年份才报）
vis.forEach((s, i) => {
  const m = s.match(/研究表明|数据显示|据统计|业内公认|众所周知|专家认为|studies show|research shows|it is well known|widely recognized|experts agree/i);
  if (m && !/(19|20)\d{2}/.test(s) && !allowed(s, m[0])) out.warn.push({ line: i + 1, hit: m[0], why: '无出处的套话：写出发布方和日期，或删掉', context: ctx(s, m.index, m[0].length) });
});

// WARN：会过期的数字
scan('warn', /\d[\d,]*\s*(?:多|余)?\s*(?:个|款|种|只)\s*(?:料号|型号|系列|产品|规格|SKU)|共\s*\d[\d,]*\s*(?:个|款|种)|[一二三四五六七八九十两百千]+\s*(?:个|款|种|大)\s*(?:系列|规格|型号)|最全|最多的/,
  '会过期的数字：改成描述结构（含哪些系列、覆盖什么范围）');
scan('warn', /\b(?:over |more than )?\d[\d,]*\+?\s+(?:part numbers|models|SKUs|products|series|variants|configurations)\b|\b(?:widest|largest) (?:range|selection)\b/i,
  '会过期的数字：改成描述结构');

// WARN：没写来源的百分比（同一行没有年份或来源词才报；表格行不查）
vis.forEach((s, i) => {
  const m = s.match(/\d+(?:\.\d+)?\s?%/);
  if (!m || s.trim().startsWith('|') || /compatible/i.test(s)) return;
  if (/(19|20)\d{2}|来源|出处|据|根据|source|according to|规格书|datasheet|测试|tested|survey|调研/i.test(s)) return;
  out.warn.push({ line: i + 1, hit: m[0], why: '百分比：写来源和日期；是规格参数的，确认能指回规格书', context: ctx(s, m.index, m[0].length) });
});

// WARN：电话、邮箱、变形写法
scan('warn', /[\w.+-]+@[\w-]+\.[\w.-]+/, '正文里的邮箱：确认是站点公共邮箱，不放个人邮箱');
scan('warn', /\+\d{1,3}[\s-]?\d[\d\s-]{6,}\d|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)0\d{2,3}-\d{7,8}(?!\d)|(?<!\d)400[-\s]?\d{3}[-\s]?\d{4}(?!\d)|\(\d{3}\)\s?\d{3}-\d{4}/,
  '正文里的电话：确认是口径登记的对外号码，文章里优先放表单或样品页链接');
scan('warn', /(?:\d[\s.·_]){6,}\d|[零〇一二三四五六七八九壹贰叁肆伍陆柒捌玖]{7,}/, '疑似变形写的联系方式：百度细雨算法打击这种写法');

// WARN：参数只给图片或规格书
scan('warn', /参数(?:见|详见)(?:下图|图片|附件|规格书)|详细参数请(?:下载|见|参考)|see (?:the )?(?:image|picture|attached file|datasheet) (?:below )?for (?:details|specifications|specs)|refer to the datasheet for (?:all )?(?:details|specifications|specs)/i,
  '关键参数要写成网页上的文字表格，规格书保留下载');

// WARN：弱锚文字
const weak = /^(read more|click here|learn more|more|here|details|this page|了解更多|点击这里|点击此处|更多|详情|查看更多|这里|→|>>|»)$/i;
let linkCount = 0;
lines.forEach((l, i) => {
  const anchors = [...l.matchAll(/(?<!!)\[([^\]]+)\]\([^)]+\)/g)].map(m => m[1])
    .concat([...l.matchAll(/<a\b[^>]*>([\s\S]*?)<\/a>/gi)].map(m => strip(m[1])));
  for (const t of anchors) {
    linkCount++;
    if (weak.test(t.trim())) out.warn.push({ line: i + 1, hit: t.trim(), why: '弱锚文字：写目标页的主题', context: l.trim().slice(0, 60) });
  }
});
if (linkCount === 0) out.warn.push({ line: 0, hit: '', why: '全文没有链接：至少链一个相关产品页或分类页，并给真实的下一步入口', context: '' });

// 首段与首句
const heads = [];
lines.forEach((l, i) => { const h = headingOf(l); if (h) heads.push({ line: i + 1, ...h }); });
let firstPara = '';
for (let i = 0; i < vis.length; i++) {
  const t = vis[i].trim();
  if (!firstPara && /^\*\*[^*]{1,30}\*\*[:：]?$/.test(lines[i].trim())) continue; // 「**要点速览**」这类标签行
  if (!t || headingOf(lines[i]) || /^!\[|^<img/i.test(lines[i].trim()) || /^[-*_]{3,}$/.test(t)) { if (firstPara) break; continue; }
  firstPara += (firstPara ? ' ' : '') + t.replace(/^[-*+>]\s+|^\d+\.\s+/, '');
}
const firstSentence = (firstPara.match(lang === 'zh' ? /^[^。！？!?]*[。！？!?]?/ : /^.*?(?:[.!?](?=\s|$)|$)/) || [''])[0].trim();
out.info.push({ what: '首句', value: firstSentence || '（没找到正文段落）' });
if (opt.topics.length) {
  const hit = opt.topics.some(tp => {
    const esc = tp.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '[\\s-]+');
    return new RegExp(/^[\x00-\x7f]+$/.test(tp) ? `(?<![A-Za-z])${esc}(?:s|es)?(?![A-Za-z])` : esc, 'i').test(firstSentence);
  });
  if (!hit) out.warn.push({ line: 0, hit: opt.topics.join(' / '), why: '首句没有主题词：第一句写出本页主题词，用买家的叫法', context: firstSentence.slice(0, 80) });
} else out.info.push({ what: '主题词', value: '没给 --topic，跳过首句检查' });

out.info.push({ what: '小标题', value: heads.map(h => `${'#'.repeat(h.level)} ${h.text}`).join(' | ') || '（没有）' });
if (heads.filter(h => h.level === 1).length > 1) out.info.push({ what: 'H1', value: `有 ${heads.filter(h => h.level === 1).length} 个 H1；H1 写法归 seo-tdk-page-elements` });
out.info.push({ what: '字数', value: lang === 'zh' ? `${cjk} 个汉字（只作参考，不是指标）` : `${words} 个英文词（只作参考，不是指标）` });

// 输出（按行号排，全文级的放最后）
for (const k of ['fail', 'warn']) out[k].sort((a, b) => (a.line || 1e9) - (b.line || 1e9));
if (opt.json) { console.log(JSON.stringify(out, null, 2)); }
else {
  const row = f => `  ${f.line ? 'L' + f.line : '全文'}  ${f.why}${f.hit ? `「${f.hit}」` : ''}${f.context ? `\n        …${f.context}…` : ''}`;
  console.log(`稿件检查：${opt.file}　语言：${lang}${opt.topics.length ? '　主题词：' + opt.topics.join(' / ') : ''}`);
  console.log(`\nFAIL ${out.fail.length}`); out.fail.forEach(f => console.log(row(f)));
  console.log(`\nWARN ${out.warn.length}`); out.warn.forEach(f => console.log(row(f)));
  console.log('\nINFO'); out.info.forEach(f => console.log(`  ${f.what}：${f.value}`));
  console.log(`\n结论：${out.fail.length ? '有 FAIL，先处理再交。' : out.warn.length ? '没有 FAIL；WARN 逐条处理，改掉或写明口径依据。' : '字面检查通过；口径和参数仍要人工核对。'}`);
}
process.exit(out.fail.length ? 1 : 0);
