#!/usr/bin/env python3
"""文字渲染(PIL 出 PNG,供 ffmpeg overlay;本机 ffmpeg 无 drawtext 滤镜,勿改回 drawtext 方案)。
用法:
  render_text.py card  <out.png> <标题> <正文行1|行2|行3> [脚注]   # 1080x1920 数据图卡
  render_text.py strip <out.png> <字幕文本>                        # 透明底字幕条(自动换行)
  render_text.py badge <out.png>                                   # 「内容由 AI 生成」角标(合规必带)
"""
import sys
from PIL import Image, ImageDraw, ImageFont

FONTS = ["/System/Library/Fonts/STHeiti Medium.ttc",
         "/System/Library/Fonts/Hiragino Sans GB.ttc",
         "/System/Library/Fonts/Supplemental/Songti.ttc"]

def font(size):
    for p in FONTS:
        try: return ImageFont.truetype(p, size)
        except OSError: continue
    sys.exit("无可用中文字体")

import re

def tokenize(text):
    """CJK 逐字成词元;连续 ASCII(数字/字母/空格)整段成词元——换行不得拆断 '67'、'CSDN' 这类串。"""
    return re.findall(r"[0-9A-Za-z][0-9A-Za-z .%]*[0-9A-Za-z%]|[0-9A-Za-z]|.", text)

def wrap(draw, text, f, maxw):
    lines, cur = [], ""
    for tok in tokenize(text):
        if cur and draw.textlength(cur + tok, font=f) > maxw:
            lines.append(cur); cur = tok.lstrip()
        else:
            cur += tok
    if cur: lines.append(cur)
    return lines

def center(draw, y, text, f, fill, W=1080):
    w = draw.textlength(text, font=f)
    draw.text(((W - w) // 2, y), text, font=f, fill=fill)

def card(out, title, lines, foot):
    img = Image.new("RGB", (1080, 1920), (16, 24, 40))
    d = ImageDraw.Draw(img)
    center(d, 380, title, font(72), (255, 214, 102))
    y = 580
    for line in lines.split("|"):
        center(d, y, line.strip(), font(58), (255, 255, 255)); y += 130
    # 脚注放正文下方留白区,避开底部字幕条(字幕条区域 ≈ y1430 以下)
    center(d, max(y + 60, 1100), foot, font(32), (170, 178, 192))
    img.save(out)

def strip(out, text):
    f = font(52)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = wrap(tmp, text.strip(), f, 960)
    lh, pad = 74, 26
    H = len(lines) * lh + pad * 2
    img = Image.new("RGBA", (1080, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([30, 0, 1050, H], radius=18, fill=(0, 0, 0, 115))
    for i, line in enumerate(lines):
        center(d, pad + i * lh, line, f, (255, 255, 255, 255))
    img.save(out)

def badge(out):
    f = font(34)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    w = int(tmp.textlength("内容由 AI 生成", font=f))
    img = Image.new("RGBA", (w + 44, 60), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w + 43, 59], radius=12, fill=(0, 0, 0, 95))
    d.text((22, 9), "内容由 AI 生成", font=f, fill=(255, 255, 255, 220))
    img.save(out)

if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "card":  card(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else "单一商户题集实测 · 不代表行业均值")
    elif mode == "strip": strip(sys.argv[2], sys.argv[3])
    elif mode == "badge": badge(sys.argv[2])
    else: sys.exit(__doc__)
