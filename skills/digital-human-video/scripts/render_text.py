#!/usr/bin/env python3
"""文字渲染(PIL 出 PNG,供 ffmpeg overlay;本机 ffmpeg 无 drawtext 滤镜,勿改回 drawtext 方案)。
用法:
  render_text.py card  <out.png> <标题> <正文行1|行2|行3> [脚注]   # 1080x1920 数据图卡
  render_text.py strip <out.png> <字幕文本>                        # 透明底字幕条(自动换行)
  render_text.py badge <out.png> <W> <H> [tr|tl|br|bl]             # 显式 AI 角标(必带;按成片实际分辨率算字高,stdout 打印几何 JSON)
"""
import json
import os
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

# 「标签词：」整体成一个词元,不许从中间断开。
# 口播稿里大量是「原型师：抓住第一个想法」这种「短标签 + 冒号 + 说明」句式,
# 逐字换行会把标签拦腰截断成「扩展 / 者：」——2026-08-03 实测在成片里出现过。
# 只吃冒号前 ≤5 个非标点字符,长了不粘,免得一个超长词元把整行挤爆。
LABEL_RE = re.compile(r"[^\s。，、；：？！,.;:?!]{1,5}[：:]")


def tokenize(text):
    """CJK 逐字成词元;连续 ASCII(数字/字母/空格)整段成词元——换行不得拆断 '67'、'CSDN'
    这类串;「标签词：」也整体不拆(见 LABEL_RE)。"""
    out, last = [], 0
    for m in LABEL_RE.finditer(text):
        if m.start() > last:
            out += re.findall(r"[0-9A-Za-z][0-9A-Za-z .%]*[0-9A-Za-z%]|[0-9A-Za-z]|.", text[last:m.start()])
        out.append(m.group())
        last = m.end()
    if last < len(text):
        out += re.findall(r"[0-9A-Za-z][0-9A-Za-z .%]*[0-9A-Za-z%]|[0-9A-Za-z]|.", text[last:])
    return out

# 中日韩排版禁则(kinsoku):标点不能落在行首/行尾。
# 2026-08-03 实测:字幕断行把「，」「。」「、」甩到行首,一眼就看出是机器排的。
LEAD_FORBIDDEN = "。，、；：？！）〕】》」』〉”’…—·%,.;:?!)]}"   # 不能出现在行首
TAIL_FORBIDDEN = "（〔【《「『〈“‘([{"                              # 不能出现在行尾


def kinsoku(lines):
    """行首/行尾禁则处理:把违规标点挪到相邻行。宽度上允许轻微超出——
    标点比正文窄得多,挤一个进上一行几乎看不出,而标点落行首是一眼可见的排版错。"""
    out = [l for l in lines if l]
    # 行首禁则:把行首标点回吸到上一行末
    i = 1
    while i < len(out):
        while out[i] and out[i][0] in LEAD_FORBIDDEN:
            out[i - 1] += out[i][0]
            out[i] = out[i][1:]
        if not out[i]:
            out.pop(i)
        else:
            i += 1
    # 行尾禁则:把行末的开括号推到下一行首
    for i in range(len(out) - 1):
        while out[i] and out[i][-1] in TAIL_FORBIDDEN:
            out[i + 1] = out[i][-1] + out[i + 1]
            out[i] = out[i][:-1]
    return [l for l in out if l]


def wrap(draw, text, f, maxw):
    lines, cur = [], ""
    for tok in tokenize(text):
        if cur and draw.textlength(cur + tok, font=f) > maxw:
            lines.append(cur); cur = tok.lstrip()
        else:
            cur += tok
    if cur: lines.append(cur)
    return kinsoku(lines)

# ── 底部字幕安全区(唯一真源,勿在别处再写魔数)──────────────────────
# compose.sh 把字幕条贴在 overlay=0:H-h-140 → 底边恒为 y=1780,条高 h 随行数变。
# 所有 1080x1920 的图(卡片/框架图)底部内容都必须停在 sub_safe_top() 以上。
# **被字幕条盖住不是"看不见"**——条是 45% 半透明黑,盖住等于透出一层鬼影,
# 比缺了这行字更难看。2026-08-03 实测:框架图脚注画在 y=1600、第五个节点底边
# 到 y=1462,双双透在字幕里;当时 card() 把安全区写成注释里的「≈y1430」,
# 后写的 make_diagram.py 既没看到也无从复用,于是原样再踩一遍。
SUB_BOTTOM_Y = 1780
SUB_LINE_H, SUB_PAD = 74, 26


def sub_safe_top(max_lines=4):
    """字幕最多 max_lines 行时字幕条的顶边 y。图上内容不得越过这条线。
    默认 4 行:SKILL.md 规定每段文案 ≤65 字,52 号字在 960 宽下约 4 行封顶。"""
    return SUB_BOTTOM_Y - (max_lines * SUB_LINE_H + SUB_PAD * 2)


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
    # 脚注放正文下方留白区,且**必须**停在字幕安全线以上(见 sub_safe_top)
    center(d, min(max(y + 60, 1100), sub_safe_top() - 46), foot, font(32), (170, 178, 192))
    img.save(out)

def strip(out, text):
    f = font(52)
    tmp = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = wrap(tmp, text.strip(), f, 960)
    lh, pad = SUB_LINE_H, SUB_PAD
    H = len(lines) * lh + pad * 2
    img = Image.new("RGBA", (1080, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([30, 0, 1050, H], radius=18, fill=(0, 0, 0, 115))
    for i, line in enumerate(lines):
        center(d, pad + i * lh, line, f, (255, 255, 255, 255))
    img.save(out)

# ── 显式 AI 角标 ────────────────────────────────────────────────────
# 措辞(BADGE_TEXT)与字高下限的唯一真源在同目录 aigc_meta.py。各期脚本用
# spec_from_file_location 加载本文件,这时本目录不在 sys.path 上,`import aigc_meta`
# 会抛 ModuleNotFoundError,所以按路径加载;放到 badge() 里懒加载,strip/card 不受牵连。
BADGE_ANCHORS = ("tr", "tl", "br", "bl")
BADGE_MARGIN_X, BADGE_MARGIN_Y = 0.037, 0.056   # 边距占最短边的比例:1080 时正好是 40、60
_AIGC_META = None


def _aigc_meta():
    global _AIGC_META
    if _AIGC_META is None:
        import importlib.util
        from pathlib import Path
        path = Path(__file__).resolve().with_name("aigc_meta.py")
        spec = importlib.util.spec_from_file_location("aigc_meta", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _AIGC_META = module
    return _AIGC_META


def _font_and_path(size):
    for p in FONTS:
        try: return ImageFont.truetype(p, size), p
        except OSError: continue
    sys.exit("无可用中文字体")


def _ink_height(text, f):
    """整行墨迹高:L 模式白字,阈值 >128 的外接框高度。实测而非「字号×系数」,换回退字体也成立。"""
    l, t, r, b = f.getbbox(text)
    img = Image.new("L", (r - l + 8, b - t + 8), 0)
    ImageDraw.Draw(img).text((4 - l, 4 - t), text, font=f, fill=255)
    bb = img.point(lambda v: 255 if v > 128 else 0).getbbox()
    return bb[3] - bb[1] if bb else 0


def _even(v):
    """yuv420 叠加按 2 像素对齐:边距、框宽高取偶数,贴边的角才落在偶数坐标上不被挪位。"""
    return 2 * round(v / 2)


def badge(out, *, width, height, anchor="tr"):
    """按成片实际分辨率出显式 AI 角标 PNG,并写出 <out>.json 几何说明(同时作为返回值)。
    width/height 必填、没有默认值:分辨率一变,按 1080x1920 悄悄兜底就是错的尺寸。
    字号从 ceil(5%·最短边) 起逐 1 增大,直到整行墨迹高 ≥ 下限 + 2 px;
    超出画面就报错,不静默裁切。"""
    m = _aigc_meta()
    if anchor not in BADGE_ANCHORS:
        raise ValueError(f"anchor 只能是 {'/'.join(BADGE_ANCHORS)},收到 {anchor!r}")
    W, H = int(width), int(height)
    if W <= 0 or H <= 0:
        raise ValueError(f"画幅必须为正: {W}x{H}")
    S = min(W, H)
    need = m.min_glyph_px(W, H)
    size = need
    f, fpath = _font_and_path(size)
    ink = _ink_height(m.BADGE_TEXT, f)
    while ink < need + m.INK_SAFETY_PX:
        size += 1
        if size > 4 * need:
            raise ValueError(f"字体 {fpath} 量不出合格墨迹高(字号已到 {size})")
        f, fpath = _font_and_path(size)
        ink = _ink_height(m.BADGE_TEXT, f)
    # 样式沿用旧角标:圆角黑底 alpha 95、白字 alpha 220;内边距和圆角按字号比例缩放
    pad_x, pad_y = round(0.65 * size), round(0.3 * size)
    l, t, r, b = f.getbbox(m.BADGE_TEXT)
    w, h = r - l + 2 * pad_x, b - t + 2 * pad_y
    w, h = w + w % 2, h + h % 2   # 宽高补成偶数(理由见 _even)
    mx, my = _even(BADGE_MARGIN_X * S), _even(BADGE_MARGIN_Y * S)
    if w + 2 * mx > W or h + 2 * my > H:
        raise ValueError(f"角标 {w}x{h}(含边距 {mx},{my})超出画面 {W}x{H}")
    x = mx if anchor[1] == "l" else W - w - mx
    y = my if anchor[0] == "t" else H - h - my
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=round(0.35 * size), fill=(0, 0, 0, 95))
    d.text(((w - (r - l)) // 2 - l, (h - (b - t)) // 2 - t), m.BADGE_TEXT, font=f, fill=(255, 255, 255, 220))
    img.save(out)
    spec = {
        "text": m.BADGE_TEXT, "font_path": fpath, "font_size": size,
        "ink_height_px": ink, "min_required_px": need, "shortest_side": S,
        "canvas": [W, H], "x": x, "y": y, "w": w, "h": h, "anchor": anchor,
        "margin": [mx, my], "png": os.path.basename(str(out)),
        "rule": "GB 45438-2025 图解数值自设基线：两要素＋字高≥最短边 5%",
    }
    with open(str(out) + ".json", "w", encoding="utf-8") as fh:
        json.dump(spec, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return spec


BADGE_USAGE = "用法: render_text.py badge <out.png> <W> <H> [tr|tl|br|bl]   (宽高必填,按成片实际分辨率)"


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "card":  card(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5] if len(sys.argv) > 5 else "单一商户题集实测 · 不代表行业均值")
    elif mode == "strip": strip(sys.argv[2], sys.argv[3])
    elif mode == "badge":
        if len(sys.argv) not in (5, 6): sys.exit(BADGE_USAGE)
        try:
            spec = badge(sys.argv[2], width=int(sys.argv[3]), height=int(sys.argv[4]),
                         anchor=sys.argv[5] if len(sys.argv) > 5 else "tr")
        except ValueError as e:
            sys.exit(f"{e}\n{BADGE_USAGE}")
        print(json.dumps(spec, ensure_ascii=False))
    else: sys.exit(__doc__)
