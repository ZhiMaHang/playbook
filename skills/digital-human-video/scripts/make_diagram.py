#!/usr/bin/env python3
"""框架图(1080x1920 竖版,PIL 纯代码绘制,零成本)。

为什么用代码画而不是 AI 生成图:**AI 生成图里的中文几乎必错**(缺笔画、造字、串行),
而框架图的信息全在文字上,错一个字整张图就废了。代码画的字 100% 可控、可复算、可复现,
改一个词重画一次的成本是零。AI 生成图适合做底纹与氛围,不适合承载文字信息。

链路 + 旁挂的结构:主链竖排带箭头(表示先后/演进),旁挂项列在下方(表示贯穿全程的角色)。
支持 --highlight 高亮其中若干节点——**同一张图分段高亮,观众能一步步建立心智模型**,
比每段换一张不相干的图卡好得多。

用法:
  make_diagram.py --out d.png --title "五种角色" \\
    --chain "原型师|抓住第一个想法，飞快试错" "构建者|做成能推向市场的产品" "扩展者|放大十倍一百倍" \\
    --side  "维护者|规模之后守住它" "收尾人|打磨产品与代码" \\
    --highlight 0 1 --stage "想法" "产品" "规模" --footnote "出处 · …"
"""
import argparse, sys
from PIL import Image, ImageDraw

import importlib.util, os
_spec = importlib.util.spec_from_file_location(
    "render_text", os.path.join(os.path.dirname(os.path.abspath(__file__)), "render_text.py"))
_rt = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_rt)
font = _rt.font

W, H = 1080, 1920
BG      = (16, 24, 40)
GOLD    = (255, 209, 102)
WHITE   = (245, 247, 250)
DIM     = (120, 132, 152)     # 未高亮:压暗但仍可读
BOXDIM  = (30, 41, 61)
BOXHI   = (37, 56, 88)
EDGEHI  = (255, 209, 102)
EDGEDIM = (52, 66, 92)


def rounded(d, box, r, fill, outline, width=2):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


def arrow(d, x, y0, y1, color):
    d.line([(x, y0), (x, y1 - 14)], fill=color, width=3)
    d.polygon([(x - 9, y1 - 16), (x + 9, y1 - 16), (x, y1)], fill=color)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--title", required=True)
    p.add_argument("--chain", nargs="+", required=True, help='主链节点,每项 "名称|说明"')
    p.add_argument("--side", nargs="*", default=[], help='旁挂节点,每项 "名称|说明"')
    p.add_argument("--stage", nargs="*", default=[], help="主链各节点左侧的阶段标签(可选,数量需与 chain 一致)")
    p.add_argument("--highlight", nargs="*", type=int, default=None,
                   help="高亮的节点序号(主链 0..n-1,旁挂接着往后编);不给则全部高亮")
    p.add_argument("--footnote", default="")
    a = p.parse_args()

    nodes = [("chain", s) for s in a.chain] + [("side", s) for s in a.side]
    hi = set(range(len(nodes))) if a.highlight is None else set(a.highlight)
    if a.stage and len(a.stage) != len(a.chain):
        sys.exit("--stage 数量必须与 --chain 一致")

    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    f_title, f_name, f_desc, f_stage, f_foot = font(72), font(48), font(34), font(28), font(32)

    _rt.center(d, 210, a.title, f_title, GOLD)

    BOX_W, BOX_H, GAP = 720, 150, 78
    x0 = (W - BOX_W) // 2
    y = 400

    for i, (kind, spec) in enumerate(nodes):
        name, _, desc = spec.partition("|")
        on = i in hi
        if kind == "side" and i == len(a.chain):
            # 旁挂区之前留一段额外空档:分隔线与标签要落在两块之间的空白里,
            # 直接用 GAP//2 会压在上一个方框的下边缘上(2026-08-03 实测踩到)。
            y += 56
            _rt.center(d, y - 46, "贯穿全程", f_stage, DIM)
            d.line([(x0, y + 6), (x0 + BOX_W, y + 6)], fill=EDGEDIM, width=2)
            y += 40
        rounded(d, (x0, y, x0 + BOX_W, y + BOX_H), 18,
                BOXHI if on else BOXDIM, EDGEHI if on else EDGEDIM, 3 if on else 2)
        d.text((x0 + 34, y + 26), name, font=f_name, fill=GOLD if on else DIM)
        if desc:
            for k, ln in enumerate(_rt.wrap(d, desc, f_desc, BOX_W - 68)[:2]):
                d.text((x0 + 34, y + 88 + k * 42), ln, font=f_desc, fill=WHITE if on else DIM)
        if kind == "chain" and a.stage:
            st = a.stage[i]
            sw = d.textlength(st, font=f_stage)
            d.text((x0 - sw - 26, y + BOX_H // 2 - 18), st, font=f_stage, fill=GOLD if on else DIM)
        nxt = y + BOX_H + GAP
        if kind == "chain" and i + 1 < len(a.chain):
            arrow(d, W // 2, y + BOX_H + 12, nxt - 8, EDGEHI if (on and (i + 1) in hi) else EDGEDIM)
        y = nxt

    if a.footnote:
        _rt.center(d, H - 320, a.footnote, f_foot, DIM)
    im.save(a.out)
    print(f"框架图 → {a.out} ({W}x{H}, 高亮 {sorted(hi)})")


if __name__ == "__main__":
    main()
