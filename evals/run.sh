#!/usr/bin/env bash
# prompt-optimizer 首轮行为回归:对每个用例无头跑一次真插件,再用 haiku 按 rubric 判分。
# 用法: bash evals/run.sh [用例名过滤子串]
# 局限:只测第一轮回复(消歧/两道题/直接拼接的分流);多轮交互(回"都行"、拒答、部分作答)仍需手测。
set -uo pipefail
cd "$(dirname "$0")/.."
PLUGIN_DIR="$(pwd)"
FILTER="${1:-}"
fail=0
for dir in evals/cases/*/; do
  name=$(basename "$dir")
  [[ -n "$FILTER" && "$name" != *"$FILTER"* ]] && continue
  prompt=$(cat "$dir/prompt.txt")
  echo "=== $name ==="
  out=$(claude --plugin-dir "$PLUGIN_DIR" -p "$prompt" 2>&1)
  rc=$?
  if [[ $rc -ne 0 || -z "$out" || "$out" == *"Not logged in"* ]]; then
    echo "FAIL 运行失败(rc=$rc): $out"
    fail=1
    continue
  fi
  printf '%s\n' "$out" > "$dir/last-output.txt"
  verdict=$(claude --model haiku -p "你是评测判分员。严格按评分标准逐条核对待评回复,第一行只输出 PASS 或 FAIL,第二行用一句话给出依据(FAIL 时指明违反了第几条)。

【评分标准】
$(cat "$dir/rubric.md")

【待评回复】
$out" 2>/dev/null)
  printf '%s\n' "$verdict"
  [[ "$verdict" == PASS* ]] || fail=1
done
exit $fail
