#!/bin/bash
# 竖版成片合成(纯 overlay,不依赖 drawtext——本机 ffmpeg 无该滤镜):
# 数字人段/图卡段 → 1080x1920 拼接 + PIL 字幕条 + 「视频由 AI 制作」角标(合规必带,不许去掉)
# 用法: compose.sh <workdir> <out.mp4>
# 注意: segments.txt 里的路径一律**相对 workdir**(脚本自己拼 $WORK/),写绝对路径会拼成两截。
# 约定:<workdir>/segments.txt 每行一个片段:
#   dh   <数字人mp4>   <配音wav|-> <字幕txt>   (- 表示用 mp4 自带音轨;数字人置于上部,下留字幕区)
#   card <底图png>     <配音wav>   <字幕txt>   (静态图卡)
#   clip <生成视频mp4> <旁白wav>   <字幕txt>   (整幅铺满的生成视频 + 外挂旁白,见下)
#
# clip 与 dh 的区别:dh 是对口型的口播,画面缩在上部给字幕留位;clip 出自 jimeng_video.py,
# **不对口型**,是配画外音的氛围画面,所以铺满整幅、字幕直接压在画面上。
# clip 的时长以**旁白音频**为准(视频多出来的部分裁掉),因为没有口型要对,画面长度可以随意裁。
# 注意即梦视频 3.0 出的是 **1088x1920**(比 1080 宽 8 像素),必须 crop 回 1080,
# 否则 scale 会把画面压窄、字幕条与角标的坐标整体偏移。
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
    if [ "$b" = "-" ]; then AUD=(-map 0:a); EXTRA=(); else AUD=(-map 3:a); EXTRA=(-i "$WORK/$b"); fi
    # 数字人的输出画幅**不是固定的**:同一个 omni_v15,有时回 480P 小画幅、
    # 有时回 1088x1920 整幅(2026-08-04 实测)。写死「缩到上部+留白」会把整幅素材
    # 往下推 200px 再裁掉底部,所以这里按实际高度分流,不靠假设。
    IH=$(ffprobe -v error -select_streams v -show_entries stream=height -of csv=p=0 "$WORK/$a")
    if [ "${IH:-0}" -ge 1600 ]; then
      VF="[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg]"   # 整幅:铺满并裁回 1080
    else
      VF="[0:v]scale=1080:-2,pad=1080:1920:0:200:color=0x101828[bg]"                      # 小画幅:置于上部,下留字幕区
    fi
    ffmpeg -nostdin -y -loglevel error -i "$WORK/$a" -i "$TMP/sub_$i.png" -i "$TMP/badge.png" ${EXTRA[@]+"${EXTRA[@]}"} \
      -filter_complex "$VF;[bg][1:v]overlay=0:H-h-140[s];[s][2:v]overlay=W-w-40:60[v]" \
      -map "[v]" "${AUD[@]}" -r 25 -c:v libx264 -pix_fmt yuv420p -c:a aac -ar 44100 -ac 2 "$SEG"
  elif [ "$kind" = "clip" ]; then
    # 生成视频铺满整幅 + 外挂旁白。时长以旁白为准,视频多余部分裁掉。
    DUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$WORK/$b")
    VDUR=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$WORK/$a")
    # 视频短于旁白会在末尾定格,不是想要的效果——宁可报出来让人换素材
    awk -v v="$VDUR" -v d="$DUR" 'BEGIN{if (v+0.05 < d+0) exit 1}' \
      || echo "  ⚠️ 片段 $i:视频 ${VDUR}s 短于旁白 ${DUR}s,尾部会定格,建议换更长的视频段"
    ffmpeg -nostdin -y -loglevel error -i "$WORK/$a" -i "$WORK/$b" -i "$TMP/sub_$i.png" -i "$TMP/badge.png" \
      -filter_complex "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];[bg][2:v]overlay=0:H-h-140[s];[s][3:v]overlay=W-w-40:60[v]" \
      -map "[v]" -map 1:a -t "$DUR" -r 25 -c:v libx264 -pix_fmt yuv420p -c:a aac -ar 44100 -ac 2 "$SEG"
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
