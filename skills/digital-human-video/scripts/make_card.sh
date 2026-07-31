#!/bin/bash
# 数据图卡生成(PIL 渲染,1080x1920 深底白字)
# 用法: make_card.sh <out.png> <标题> <正文行1|行2|行3> [脚注]
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$DIR/render_text.py" card "$1" "$2" "$3" "${4:-单一商户题集实测 · 不代表行业均值}"
echo "图卡 → $1"
