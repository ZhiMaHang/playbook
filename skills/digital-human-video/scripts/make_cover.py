#!/usr/bin/env python3
"""视频封面生成(从成片抽帧或指定图 + 大字钩子)。

**封面是在缩略图尺寸下被看到的**,这决定了一切设计取舍:
  · 字要极大。信息流里封面只有几厘米宽,正文字号的标题等于没有。
  · 字数要极少。一行 ≤8 字、最多两行;写满一句话在缩略图上就是一团灰。
  · 对比要极强。深底 + 白/金字,并在文字区压一层渐变暗罩,否则背景一花字就糊。
  · 留住脸。人脸天然吸引注意力,裁切时别把脸裁掉。

**文字放在画面中部偏上,不要贴边**:各平台封面裁切比例不一(视频号约 3:4,
其他平台可能按 1:1 或 16:9 裁),贴边的字会被裁掉。中部 60% 是各种裁法都保得住的区域。

用法:
  make_cover.py --image frame.png --out cover.png \\
      --line "九成代码" --line "已经是 AI 写的" --tag "Anthropic 内部"
  make_cover.py --video 成片.mp4 --at 4.0 --out cover.png --line "..." --size 1080x1440
"""
import argparse, os, subprocess, sys, tempfile
from PIL import Image, ImageDraw, ImageFilter

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "render_text", os.path.join(os.path.dirname(os.path.abspath(__file__)), "render_text.py"))
_rt = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_rt)
font = _rt.font

GOLD = (255, 209, 102)
WHITE = (250, 251, 253)


def grab(video, at, out):
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
                    "-ss", str(at), "-i", video, "-frames:v", "1", out], check=True)


def cover(src, size, lines, tag, out, anchor):
    W, H = size
    im = Image.open(src).convert("RGB")
    # 等比放大后居中裁切,绝不拉伸变形
    sc = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * sc + 0.5), int(im.height * sc + 0.5)), Image.LANCZOS)
    im = im.crop(((im.width - W) // 2, (im.height - H) // 2,
                  (im.width - W) // 2 + W, (im.height - H) // 2 + H))

    f_main = font(int(W * 0.135))          # 缩略图下能认出的最小字号,别再调小
    f_tag = font(int(W * 0.045))
    lh = int(f_main.size * 1.22)
    block_h = len(lines) * lh + (int(f_tag.size * 1.9) if tag else 0)
    top = int(H * anchor) - block_h // 2

    # 文字区压一层渐变暗罩:背景再花也保证字读得出,同时不把整张图压死
    band = Image.new("L", (1, H), 0)
    bd = ImageDraw.Draw(band)
    pad = int(H * 0.10)
    for y in range(H):
        d = min(abs(y - (top - pad)), abs(y - (top + block_h + pad)))
        inside = (top - pad) <= y <= (top + block_h + pad)
        bd.point((0, y), fill=205 if inside else max(0, 205 - int(d * 1.6)))
    mask = band.resize((W, H)).filter(ImageFilter.GaussianBlur(2))
    im = Image.composite(Image.new("RGB", (W, H), (8, 12, 22)), im, mask)

    d = ImageDraw.Draw(im)
    y = top
    if tag:
        tw = d.textlength(tag, font=f_tag)
        x0 = (W - tw) // 2
        d.rounded_rectangle([x0 - 22, y - 8, x0 + tw + 22, y + f_tag.size + 16], radius=10,
                            fill=(255, 209, 102))
        d.text((x0, y), tag, font=f_tag, fill=(12, 18, 30))
        y += int(f_tag.size * 1.9)
    for i, ln in enumerate(lines):
        w = d.textlength(ln, font=f_main)
        x = (W - w) // 2
        # 描边:缩略图缩放后边缘会糊,一圈深色描边能把字从背景里拔出来
        for dx, dy in ((-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2), (-2, 2), (2, -2)):
            d.text((x + dx, y + dy), ln, font=f_main, fill=(6, 10, 18))
        d.text((x, y), ln, font=f_main, fill=GOLD if i == 0 else WHITE)
        y += lh
    im.save(out)
    print(f"封面 → {out} ({W}x{H})")


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--image"); g.add_argument("--video")
    p.add_argument("--at", type=float, default=1.0, help="从视频第几秒抽帧")
    p.add_argument("--out", required=True)
    p.add_argument("--line", action="append", required=True, help="标题行,可给多次;一行 ≤8 字")
    p.add_argument("--tag", default="", help="顶部小标签(可选)")
    p.add_argument("--size", default="1080x1440", help="默认 3:4,视频号封面比例")
    p.add_argument("--anchor", type=float, default=0.40,
                   help="文字块中心在画面高度的占比,0.40 偏上但仍在安全区内")
    a = p.parse_args()

    W, H = (int(x) for x in a.size.lower().split("x"))
    for ln in a.line:
        if len(ln) > 10:
            print(f"⚠️ 「{ln}」有 {len(ln)} 字,缩略图下会看不清,建议 ≤8 字", file=sys.stderr)

    src = a.image
    tmp = None
    if a.video:
        tmp = tempfile.mktemp(suffix=".png")
        grab(a.video, a.at, tmp)
        src = tmp
    cover(src, (W, H), a.line, a.tag, a.out, a.anchor)
    if tmp and os.path.exists(tmp):
        os.remove(tmp)


if __name__ == "__main__":
    main()
