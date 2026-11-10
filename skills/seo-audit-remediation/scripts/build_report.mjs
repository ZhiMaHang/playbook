#!/usr/bin/env node
// 把报告数据 JSON 填进 assets/report-template.html，生成内容已写好的静态 HTML。
// 不开脚本、在预览面板或邮件里打开也能看到全部内容；脚本只负责 P0 / P1 / P2 筛选按钮。
//
// 用法：node build_report.mjs <数据.json> <输出.html> [模板路径]
// 数据字段见模板头部注释。

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const [dataPath, outPath, tplArg] = process.argv.slice(2);
if (!dataPath || !outPath) { process.stderr.write('用法：node build_report.mjs <数据.json> <输出.html> [模板路径]\n'); process.exit(1); }
const here = path.dirname(fileURLToPath(import.meta.url));
const tplPath = tplArg || path.join(here, '..', 'assets', 'report-template.html');

const tpl = fs.readFileSync(tplPath, 'utf8');
const data = JSON.parse(fs.readFileSync(dataPath, 'utf8'));

const code = tpl.slice(tpl.indexOf('/*RENDER-START*/'), tpl.indexOf('/*RENDER-END*/'));
if (!code) throw new Error('模板里找不到 RENDER-START / RENDER-END');
const renderReport = new Function(code + '\nreturn renderReport;')();

// 写进 <script type="application/json"> 的数据要防止提前闭合
const json = JSON.stringify(data, null, 2).replace(/</g, '\\u003c');
let html = tpl.replace(/(<script type="application\/json" id="report-data">)[\s\S]*?(<\/script>)/, (m, a, b) => a + '\n' + json + '\n' + b);
html = html.replace('<!--STATIC-->', () => renderReport(data));
if (data.site) html = html.replace(/<title>[^<]*<\/title>/, () => '<title>' + String(data.site).replace(/[<&]/g, '') + ' SEO 诊断</title>');

fs.writeFileSync(outPath, html);
process.stdout.write('已生成 ' + outPath + '\n');
