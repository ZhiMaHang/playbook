#!/usr/bin/env node
// 比例的误差范围：给 AI 搜索监测（提及率、引用率、推荐率）和小样本点击率用。无依赖，Node 18+。
// 方法见 references/ai-monitoring.md「样本量与误差」。
//
// 用法：
//   node ratio_ci.mjs --k 6 --n 90              比例 + 95% 区间（Wilson）+ 正态近似的 ± 值
//   node ratio_ci.mjs --k 0 --n 7               0 次：真实比例的 95% 单侧上限（二项分布精确值）
//   node ratio_ci.mjs --p 0.1 --n 7             真实比例为 p 时，n 次里至少出现 1 次的概率（看「≥1/n」这类 KPI 有多靠运气）
//   node ratio_ci.mjs --compare 6/90 9/90       两期比较：差值和 95% 区间；区间跨 0 就写「在误差范围内，不判断为变化」
//   node ratio_ci.mjs --n-for 0.1               要让 ± 不超过 10 个百分点，大约要多少条有效回答（按最坏的 50% 算）
//
// 假设每次回答相互独立。实际不完全独立，真实误差可能更大，报告里写一句。

function parseArgs(argv) {
  const o = {};
  for (let i = 0; i < argv.length; i++) {
    if (argv[i].startsWith('--')) {
      const k = argv[i].slice(2);
      const v = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : true;
      o[k] = v;
    }
  }
  return o;
}
const Z = 1.959964;
const p100 = (x) => `${(x * 100).toFixed(1)}%`;

function wilson(k, n) {
  const p = k / n;
  const d = 1 + (Z * Z) / n;
  const c = (p + (Z * Z) / (2 * n)) / d;
  const h = (Z * Math.sqrt((p * (1 - p)) / n + (Z * Z) / (4 * n * n))) / d;
  return [Math.max(0, c - h), Math.min(1, c + h)];
}

function parseFrac(s) {
  const m = String(s).match(/^(\d+)\/(\d+)$/);
  if (!m) { console.error(`写成 k/n，例如 6/90：${s}`); process.exit(1); }
  return [Number(m[1]), Number(m[2])];
}

const a = parseArgs(process.argv.slice(2));
const out = [];

if (a.compare) {
  const parts = [a.compare, ...process.argv.slice(process.argv.indexOf(a.compare) + 1).filter((x) => !x.startsWith('--'))].slice(0, 2);
  if (parts.length < 2) { console.error('用法：--compare 上期k/n 本期k/n'); process.exit(1); }
  const [k1, n1] = parseFrac(parts[0]);
  const [k2, n2] = parseFrac(parts[1]);
  const p1 = k1 / n1, p2 = k2 / n2;
  const se = Math.sqrt((p1 * (1 - p1)) / n1 + (p2 * (1 - p2)) / n2);
  const d = p2 - p1;
  const lo = d - Z * se, hi = d + Z * se;
  out.push(`上期 ${k1}/${n1} = ${p100(p1)}；本期 ${k2}/${n2} = ${p100(p2)}`);
  out.push(`差值 ${d >= 0 ? '+' : ''}${(d * 100).toFixed(1)} 个百分点，95% 区间 ${(lo * 100).toFixed(1)} 到 ${(hi * 100).toFixed(1)} 个百分点（正态近似）`);
  out.push(lo <= 0 && hi >= 0 ? '结论：在误差范围内，不判断为变化。' : '结论：超出误差范围，可以写「有变化」，原因仍只写「可能」并列证据。');
  if (Math.min(n1, n2) < 30) out.push('提醒：有一期少于 30 条有效回答，正态近似不可靠，只看方向。');
} else if (a['n-for']) {
  const e = Number(a['n-for']);
  const n = Math.ceil((Z * Z * 0.25) / (e * e));
  out.push(`要让 95% 误差不超过 ±${(e * 100).toFixed(0)} 个百分点（比例在 50% 附近的最坏情况），大约需要 ${n} 条有效回答。`);
} else if (a.p && a.n) {
  const p = Number(a.p), n = Number(a.n);
  const atLeast1 = 1 - Math.pow(1 - p, n);
  out.push(`真实比例 ${p100(p)}，问 ${n} 次，至少出现 1 次的概率 = ${p100(atLeast1)}`);
  out.push('如果把「至少出现 1 次」定成 KPI，什么都不变时达标的概率也是这个数。');
} else if (a.k != null && a.n) {
  const k = Number(a.k), n = Number(a.n);
  if (!(n > 0) || k < 0 || k > n) { console.error('要求 0 ≤ k ≤ n，n > 0'); process.exit(1); }
  const [lo, hi] = wilson(k, n);
  out.push(`比例 ${k}/${n} = ${p100(k / n)}（分母写进报告）`);
  out.push(`95% 区间（Wilson）：${p100(lo)} 到 ${p100(hi)}`);
  out.push(`正态近似（按 50% 最坏情况）：± ${(Z * Math.sqrt(0.25 / n) * 100).toFixed(0)} 个百分点`);
  if (k === 0) {
    const up = 1 - Math.pow(0.05, 1 / n);
    out.push(`0 次：只能说真实比例大概率（95%）低于 ${p100(up)}，不能说「从不提到」。`);
  }
  if (k === n) {
    const low = Math.pow(0.05, 1 / n);
    out.push(`${n} 次全中：只能说真实比例大概率（95%）高于 ${p100(low)}。`);
  }
  if (n < 30) out.push('提醒：有效回答少于 30 条，只当方向，不当结论。');
} else {
  console.error('用法见文件头部注释：--k --n | --p --n | --compare a/b c/d | --n-for 0.1');
  process.exit(1);
}
out.push('假设每次回答相互独立；实际不完全独立，误差可能更大。');
console.log(out.join('\n'));
