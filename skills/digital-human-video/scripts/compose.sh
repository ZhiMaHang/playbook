#!/bin/bash
# 竖版成片合成(纯 overlay,不依赖 drawtext——本机 ffmpeg 无该滤镜):
# 数字人段/图卡段 → 1080x1920 拼接 + PIL 字幕条 + 「内容由 AI 生成」角标(合规必带,不许去掉)
# 用法: compose.sh <workdir> <out.mp4>
# 约定:<workdir>/segments.txt 每行一个片段:
#   dh <数字人mp4> <配音wav|-> <字幕txt>    (- 表示用 mp4 自带音轨)
#   card <底图png> <配音wav> <字幕txt>
set -euo pipefail
[ -f ~/.config/zmh-dhv/env ] && { set -a; . ~/.config/zmh-dhv/env; set +a; }
WORK="$1"; OUT="$2"; TMP="$WORK/_tmp"; mkdir -p "$TMP"
DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$DIR/render_text.py" badge "$TMP/badge.png"
i=0; LIST="$TMP/concat.txt"; : > "$LIST"
while read -r kind a b c; do
  [ -z "${kind:-}" ] && continue
  i=$((i+1)); SEG="$TMP/norm_$i.mp4"
  python3 "$DIR/render_text.py" strip "$TMP/sub_$i.png" "$(cat "$WORK/$c")"
  if [ "$kind" = "dh" ]; then
    # 480P 数字人置于画面上部,下留字幕区
    if [ "$b" = "-" ]; then AUD=(-map 0:a); EXTRA=(); else AUD=(-map 3:a); EXTRA=(-i "$WORK/$b"); fi
    ffmpeg -nostdin -y -loglevel error -i "$WORK/$a" -i "$TMP/sub_$i.png" -i "$TMP/badge.png" "${EXTRA[@]}" \
      -filter_complex "[0:v]scale=1080:-2,pad=1080:1920:0:200:color=0x101828[bg];[bg][1:v]overlay=0:H-h-140[s];[s][2:v]overlay=W-w-40:60[v]" \
      -map "[v]" "${AUD[@]}" -r 25 -c:v libx264 -pix_fmt yuv420p -c:a aac -ar 44100 -ac 2 "$SEG"
  else
    # 静态图卡 + 配音,时长=音频(用 -t 精确收口;-loop 1 -shortest 会溢出数秒)
    DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$WORK/$b")
    ffmpeg -nostdin -y -loglevel error -loop 1 -i "$WORK/$a" -i "$WORK/$b" -i "$TMP/sub_$i.png" -i "$TMP/badge.png" \
      -filter_complex "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x101828[bg];[bg][2:v]overlay=0:H-h-140[s];[s][3:v]overlay=W-w-40:60[v]" \
      -map "[v]" -map 1:a -t "$DUR" -r 25 -c:v libx264 -pix_fmt yuv420p -c:a aac -ar 44100 -ac 2 "$SEG"
  fi
  echo "file 'norm_$i.mp4'" >> "$LIST"
done < "$WORK/segments.txt"
ffmpeg -nostdin -y -loglevel error -f concat -safe 0 -i "$LIST" -c copy "$OUT"
echo "成片 → $OUT ($(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT")s)"
