#!/bin/bash
# 素材上传到 zmh 服务器静态目录,换取公网 URL(即梦 API 只收公网 URL)
# 需配置环境变量(路径按 1Panel 站点实际,首次使用时与用户确认后写死到 ~/.zshrc):
#   ZMH_ASSET_SSH     ssh 目标,默认 zmh
#   ZMH_ASSET_DIR     服务器静态目录绝对路径(必填,如 1Panel openresty 站点 www 下某目录)
#   ZMH_ASSET_URLBASE 对应公网 URL 前缀(必填,如 https://zhimahang.com/static/dhv)
# 用后记得删服务器上的临时素材(尤其形象照)。
set -euo pipefail
[ -f ~/.config/zmh-dhv/env ] && { set -a; . ~/.config/zmh-dhv/env; set +a; }
: "${ZMH_ASSET_DIR:?请设置 ZMH_ASSET_DIR(服务器静态目录)——先跑 digital-human-video-init}"
: "${ZMH_ASSET_URLBASE:?请设置 ZMH_ASSET_URLBASE(公网URL前缀)}"
SSH="${ZMH_ASSET_SSH:-zmh}"
for f in "$@"; do
  # 文件名转 ASCII:非 ASCII(中文名)会让即梦 API 直接拒收,且返回的是非 JSON 错误体
  # (2026-08-03 实测:图片 URL 带中文文件名 → HTTP 400,不是 code 层的业务错,极难定位)
  # 文件名必须是纯 ASCII:非 ASCII(如中文名)会让即梦 API 直接拒收,且返回非 JSON 错误体
  # (2026-08-03 实测:图片 URL 带中文文件名 → HTTP 400,不是 code 层业务错,崩在 json 解析上极难定位)
  ext="${f##*.}"; [ "$ext" = "$f" ] && ext="bin"
  stem="$(basename "$f" ."$ext" | LC_ALL=C tr -c 'A-Za-z0-9._-' '-' | tr -s '-' | sed 's/^-//;s/-$//')"
  [ -z "$stem" ] && stem="asset"          # 整个文件名都是中文时的兜底
  base="dhv-$(date +%s)-$stem.$ext"
  scp -q "$f" "$SSH:$ZMH_ASSET_DIR/$base"
  echo "$ZMH_ASSET_URLBASE/$base"
done
