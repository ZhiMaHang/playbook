#!/bin/bash
# 数字人输入图合成:把人像放进指定场景,并做人景协调。
#
# 为什么需要这一步:OmniHuman 只接收「图 + 音频」,**没有场景提示词参数**。
# 成片的背景 100% 由输入图决定。所以「在车里口播」= 先把输入图做成车里的样子,
# 而不是在某个 prompt 里写「in a car」。
#
# 用法:
#   make_portrait.sh green <绿幕人像> <背景图> <输出图> [相似度] [羽化]
#   make_portrait.sh alpha <透明底PNG>  <背景图> <输出图>
#   make_portrait.sh check <合成图>                      # 只做人景协调自检,不改图
#
# 参数:
#   相似度 default 0.12(0.05~0.30):绿幕颜色容差,发丝残留绿边就调大,人物被啃掉就调小
#   羽化   default 0.05(0.00~0.20):边缘过渡,越大越软
set -euo pipefail
MODE="${1:?用法见文件头}"; shift

harmonize_note() {
  cat <<'EOF'
── 人景协调自检(合成后逐条看,不过关就回去改素材,别硬发)──
1. 色温:人脸偏冷、背景偏暖(或反过来)最出戏。车内暖黄、办公室冷白、户外偏蓝。
2. 光位:人像的高光在左脸,背景的窗/灯却在右边 → 假。选背景时先看主光方向。
3. 透视:车内是近距离平视或略仰视,人像若是标准证件照(正平视、无透视畸变)贴进车里会"浮"。
4. 比例:人头占画面高度约 1/4~1/3 才自然;贴太大像海报,太小看不清口型。
5. 边缘:放大到 200% 看发丝,有绿边就调大相似度参数重做。
EOF
}

case "$MODE" in
  green)
    SRC="${1:?绿幕人像}"; BG="${2:?背景图}"; OUT="${3:?输出图}"
    SIM="${4:-0.12}"; BLEND="${5:-0.05}"
    # 以人像尺寸为准,背景等比裁切填满,避免拉伸变形
    W=$(ffprobe -v error -select_streams v -show_entries stream=width  -of csv=p=0 "$SRC")
    H=$(ffprobe -v error -select_streams v -show_entries stream=height -of csv=p=0 "$SRC")
    ffmpeg -nostdin -y -loglevel error -i "$BG" -i "$SRC" -filter_complex \
      "[0:v]scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H}[bg];\
       [1:v]colorkey=0x00FF00:${SIM}:${BLEND},despill[fg];\
       [bg][fg]overlay=0:0[v]" -map "[v]" -frames:v 1 "$OUT" 2>/dev/null \
    || ffmpeg -nostdin -y -loglevel error -i "$BG" -i "$SRC" -filter_complex \
      "[0:v]scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H}[bg];\
       [1:v]colorkey=0x00FF00:${SIM}:${BLEND}[fg];\
       [bg][fg]overlay=0:0[v]" -map "[v]" -frames:v 1 "$OUT"
    echo "合成 → $OUT (${W}x${H}, 相似度=$SIM 羽化=$BLEND)"
    harmonize_note
    ;;
  alpha)
    SRC="${1:?透明底PNG}"; BG="${2:?背景图}"; OUT="${3:?输出图}"
    W=$(ffprobe -v error -select_streams v -show_entries stream=width  -of csv=p=0 "$SRC")
    H=$(ffprobe -v error -select_streams v -show_entries stream=height -of csv=p=0 "$SRC")
    ffmpeg -nostdin -y -loglevel error -i "$BG" -i "$SRC" -filter_complex \
      "[0:v]scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H}[bg];\
       [bg][1:v]overlay=0:0[v]" -map "[v]" -frames:v 1 "$OUT"
    echo "合成 → $OUT (${W}x${H})"
    harmonize_note
    ;;
  check)
    IMG="${1:?合成图}"
    python3 - "$IMG" <<'PY'
import sys
from PIL import Image, ImageStat
im = Image.open(sys.argv[1]).convert("RGB")
w, h = im.size
print(f"尺寸 {w}x{h}  宽高比 {w/h:.2f}" + ("  ✅ 接近 9:16" if 0.5 < w/h < 0.62 else "  ⚠️ 非竖版,数字人成片会是这个画幅"))
# 上/下半区色温对比(粗判人景是否同一光环境)
top, bot = im.crop((0,0,w,h//2)), im.crop((0,h//2,w,h))
for name, part in (("上半区", top), ("下半区", bot)):
    r, g, b = ImageStat.Stat(part).mean
    warm = r - b
    print(f"  {name} 均值 R{r:.0f} G{g:.0f} B{b:.0f} → {'偏暖' if warm>8 else '偏冷' if warm<-8 else '中性'} (R-B={warm:+.0f})")
r1,_,b1 = ImageStat.Stat(top).mean; r2,_,b2 = ImageStat.Stat(bot).mean
d = abs((r1-b1)-(r2-b2))
print(f"  上下色温差 {d:.0f} → " + ("✅ 一致" if d < 12 else "⚠️ 偏大,人景可能不像同一场景光"))
PY
    harmonize_note
    ;;
  *) echo "未知模式 $MODE(green|alpha|check)"; exit 1;;
esac
