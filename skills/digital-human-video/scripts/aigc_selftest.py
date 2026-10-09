#!/usr/bin/env python3
"""AI 角标与成片元数据的自测:全部样本在临时目录里现造,不读写任何项目文件。
用法: python3 aigc_selftest.py        # 全过打印 AIGC_SELFTEST_PASS,否则非零退出

改 render_text.badge()、aigc_meta.py、compose.sh 的角标/元数据部分,或 verify_package.py 的
角标/元数据检查之后,都跑这一条。覆盖:
  1. 角标:1080x1920、1920x1080、1440x2560(抓写死字号)、720x1280 四种画幅解码第 0 帧、
     1 s、2 s、末帧实测字形高;位置贴角;深浅两种底色过 verify_package 的像素判据;
     另有 432x768／360x640／540x960／610x1084 小画幅的 verify 正反例;
  2. 元数据:源片键逐字保留(含非标准键)、说明字段、brand 三键无重复值、源片在第 0/1 路、
     description 冲突护栏、creation_time(普通源片、mdta 源片「X;X」与「A;B」都不让 mvhd 归零、真被改仍判变)、
     封装器键不同的源片(含 Matroska 大写键名);compose.sh 端到端(多段不同元数据、首段是 clip、全是图卡(且清掉旧 sidecar)、成片再混音、/bin/bash 3.2 下的冲突用例);
     变帧率、头部或头尾 -c copy 剪过、音轨比画面长的正例不误判截断;变帧率慢段掉角标、一帧停留跨过整秒掉角标都要抓到;
  3. verify_package.py:正例 exit 0;不叠角标、角标晚出/只在开头/中段掉了/末尾掉了、淡角标、旧角标、
     几何 JSON 墨迹或字号不足、JSON 自报够但 PNG 实测不够、措辞缺一要素、终版被截断(恰好少 2 帧、只少最后 1 个字节等)、结构完整但解不出帧;
     mdat 长度未声明时只报 WARN、ffmpeg 彩色日志照常解析、画幅不符、
     插入面板盖住部分角标、白闪盖过角标、缺说明字段、源键丢失/被改/brand 重复值、--source 传错/不存在/读不了、缺 --badge-spec、缺 --source、
     脱离插件目录找不到 aigc_meta.py 都必须 FAIL 并点名检查项;--legacy-audit 降为 WARN;
     aigc_meta.py check 命令行同样拦得住传错的 --source 和缺说明字段。
样本里的 AIGC 键只用假值(example-producer / test-000N),只为验证「原样保留」。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
VERIFY = HERE.parents[1] / "zmh-short-video-pipeline/scripts/verify_package.py"

# 期望值独立写死,不从被测代码里算:改坏 aigc_meta 的比例常量时这里才会变红
CANVASES = [(1080, 1920), (1920, 1080), (1440, 2560), (720, 1280)]
EXPECTED_MIN_PX = {(1080, 1920): 54, (1920, 1080): 54, (1440, 2560): 72, (720, 1280): 36}
EXPECTED_MARGIN = {1080: (40, 60), 1440: (54, 80), 720: (26, 40)}
DARK, LIGHT, WHITE = "0x202020", "0xF0F0F0", "0xFFFFFF"
DURATION = 3

STRIP_ALL = ["-map_metadata", "-1"]  # NEGATIVE_SAMPLE_ONLY:只用来造「源键丢失」反例


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


meta = load("aigc_meta")
rt = load("render_text")

FAILURES: list[str] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)
    return ok


def ff(*args: str) -> None:
    result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg {' '.join(args[-1:])} 失败: {result.stderr.strip()}")


def aigc_value(produce_id: str) -> str:
    return json.dumps({"Label": "AIGC", "ContentProducer": "example-producer", "ProduceID": produce_id,
                       "ContentPropagator": "example-producer", "PropagateID": produce_id,
                       "ReservedCode1": "", "ReservedCode2": ""}, separators=(",", ":"))


SRC_TAGS = {
    "title": "selftest title", "artist": "selftest artist", "comment": "selftest comment",
    "AIGC": aigc_value("test-0001"),
    "workflow": json.dumps({"nodes": [{"id": i, "type": "ExampleNode"} for i in range(60)]}),
}


def tag_args(tags: dict[str, str]) -> list[str]:
    return [a for k, v in tags.items() for a in ("-metadata", f"{k}={v}")]


def make_src(path: Path, w: int, h: int, color: str, tags: dict[str, str] = SRC_TAGS,
             dur: float = DURATION, mdta: bool = True) -> Path:
    """mdta=False 造「普通」源片(手机、剪辑软件导出那种):标准键走 iTunes/mvhd,不带非标准键。"""
    ff("-f", "lavfi", "-i", f"color=c={color}:s={w}x{h}:d={dur}:r=25",
       "-f", "lavfi", "-i", f"sine=f=440:d={dur}",
       "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac",
       *tag_args(tags), *(["-movflags", "+use_metadata_tags"] if mdta else []), str(path))
    return path


def overlay(src: Path, png: Path | None, x: int, y: int, out: Path, extra: list[str],
            enable: str | None = None) -> Path:
    if png is None:
        ff("-i", str(src), "-map", "0:v", "-map", "0:a", "-c:v", "libx264", "-crf", "18",
           "-pix_fmt", "yuv420p", "-c:a", "aac", *extra, str(out))
    else:
        ff("-i", str(src), "-loop", "1", "-i", str(png),
           "-filter_complex", f"[0:v][1:v]overlay={x}:{y}:shortest=1" + (f":enable='{enable}'" if enable else "") + "[v]",
           "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
           "-c:a", "aac", *extra, str(out))
    return out


def ink_heights(video: Path, x: int, y: int, w: int, h: int) -> dict[str, int]:
    """框内亮度 > 128 的像素外接框高度,取第 0 帧、1 s、2 s、末帧(不用 -ss,整段解码后按序号取)。"""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"format=gray,crop={w}:{h}:{x}:{y}",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    frames = [raw[i:i + w * h] for i in range(0, len(raw) - w * h + 1, w * h)]
    out = {}
    for label, index in (("0", 0), ("1s", 25), ("2s", 50), ("末帧", len(frames) - 1)):
        frame = frames[index]
        rows = [r for r in range(h) if max(frame[r * w:(r + 1) * w]) > 128]
        out[label] = rows[-1] - rows[0] + 1 if rows else 0
    return out


def decoded_frames(path: Path) -> int:
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough",
                          "-f", "framemd5", "-"], capture_output=True, text=True, check=False).stdout
    return sum(1 for line in out.splitlines() if line and not line.startswith("#"))


def mvhd_creation_time(path: Path) -> int:
    """mvhd 盒里的创建时间(1904 年起的秒数);0 表示被写成了空值。"""
    data = path.read_bytes()
    i = data.find(b"mvhd")
    return struct.unpack(">Q", data[i + 8:i + 16])[0] if data[i + 4] == 1 else struct.unpack(">I", data[i + 8:i + 12])[0]


def meta_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HERE / "aigc_meta.py"), *args], capture_output=True, text=True, check=False)


def old_badge(out: Path) -> tuple[int, int]:
    """改版前的角标复刻(只作反例):字号 34、框高 60、措辞取退役登记第一条。"""
    f, text = rt.font(34), meta.RETIRED_BADGE_TEXTS[0]
    w = int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=f))
    img = Image.new("RGBA", (w + 44, 60), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w + 43, 59], radius=12, fill=(0, 0, 0, 95))
    d.text((22, 9), text, font=f, fill=(255, 255, 255, 220))
    img.save(out)
    return img.size


def verify(video: Path, *extra: str, env: dict[str, str] | None = None) -> tuple[int, str]:
    result = subprocess.run([sys.executable, str(VERIFY), "--video", str(video), *extra],
                            capture_output=True, text=True, check=False, env=dict(os.environ, **(env or {})))
    return result.returncode, result.stdout + result.stderr


def dims(w: int, h: int) -> list[str]:
    return ["--expected-width", str(w), "--expected-height", str(h)]


def expect_fail(name: str, video: Path, item: str, *extra: str, detail: str = "") -> None:
    """非零退出,且报告里有以 [FAIL] <item>: 开头、含 detail 的一行。"""
    code, out = verify(video, *extra)
    hit = any(line.startswith(f"[FAIL] {item}:") and detail in line for line in out.splitlines())
    check(code != 0 and hit, f"verify 反例·{name}",
          f"exit={code},点名「{item}」{('含「' + detail + '」') if detail else ''}" if code else "竟然 exit 0")


def section_badge(tmp: Path) -> dict:
    print("\n== 1. 角标 ==")
    check(meta.BADGE_TEXT not in meta.RETIRED_BADGE_TEXTS and meta.has_two_elements(meta.BADGE_TEXT),
          "措辞常量:不在退役登记里且含两要素", meta.BADGE_TEXT)
    old_cli = subprocess.run([sys.executable, str(HERE / "render_text.py"), "badge", str(tmp / "x.png")],
                             capture_output=True, text=True, check=False)
    check(old_cli.returncode != 0 and "<W> <H>" in old_cli.stderr, "旧 CLI 用法(不带宽高)报错并提示新用法")
    try:
        rt.badge(str(tmp / "x.png"))
        check(False, "旧函数调用 badge(out) 报错")
    except TypeError as exc:
        check("width" in str(exc) and "height" in str(exc), "旧函数调用 badge(out) 报错", str(exc))

    samples = {}
    for (w, h) in CANVASES:
        png = tmp / f"badge_{w}x{h}.png"
        spec = rt.badge(str(png), width=w, height=h)
        need = EXPECTED_MIN_PX[(w, h)]
        mx, my = EXPECTED_MARGIN[min(w, h)]
        on_disk = json.loads(Path(str(png) + ".json").read_text(encoding="utf-8"))
        check(on_disk == spec and spec["canvas"] == [w, h], f"{w}x{h} 写出几何 JSON")
        check(spec["font_size"] >= need, f"{w}x{h} 字号 ≥ {need}", str(spec["font_size"]))
        check(spec["ink_height_px"] >= need + 2, f"{w}x{h} 墨迹高 ≥ 下限＋2(给编码留余量)", str(spec["ink_height_px"]))
        check(spec["x"] >= 0 and spec["y"] >= 0 and spec["x"] + spec["w"] <= w and spec["y"] + spec["h"] <= h
              and w - spec["x"] - spec["w"] == mx and spec["y"] == my,
              f"{w}x{h} 框在画面内且贴右上角(边距 {mx},{my})",
              f"({spec['x']},{spec['y']}) {spec['w']}x{spec['h']}")
        for bg in (DARK, LIGHT):
            src = make_src(tmp / f"src_{w}x{h}_{bg}.mp4", w, h, bg)
            fin = overlay(src, png, spec["x"], spec["y"], tmp / f"fin_{w}x{h}_{bg}.mp4", meta.ffmpeg_args(src, 0))
            samples[(w, h, bg)] = (src, fin, png)
            if bg == DARK:
                inks = ink_heights(fin, spec["x"], spec["y"], spec["w"], spec["h"])
                check(all(v >= need for v in inks.values()), f"{w}x{h} 实测字形高 ≥ {need}", json.dumps(inks))
            code, out = verify(fin, "--badge-spec", str(png) + ".json", "--source", str(src), *dims(w, h))
            check(code == 0 and "[WARN] AI 角标像素" not in out, f"verify 正例·{w}x{h} 底色 {bg}(且角标像素无 WARN)",
                  "exit 0" if code == 0 else out[-600:])
    try:
        rt.badge(str(tmp / "bad_anchor.png"), width=1080, height=1920, anchor="TR")
        check(False, "anchor 只认小写 tr/tl/br/bl")
    except ValueError:
        check(True, "anchor 只认小写 tr/tl/br/bl")
    for (w, h) in ((432, 768), (360, 640)):   # 字号小到笔画撑不过腐蚀(ComfyUI 原生 432x768 一类)
        png_s = tmp / f"badge_{w}x{h}.png"
        spec_s = rt.badge(str(png_s), width=w, height=h)
        src_s = make_src(tmp / f"src_{w}x{h}.mp4", w, h, LIGHT)
        fin_s = overlay(src_s, png_s, spec_s["x"], spec_s["y"], tmp / f"fin_{w}x{h}.mp4", meta.ffmpeg_args(src_s, 0))
        code, out = verify(fin_s, "--badge-spec", str(png_s) + ".json", "--source", str(src_s), *dims(w, h))
        check(code == 0, f"verify 正例·小画幅 {w}x{h}(字号 {spec_s['font_size']})", "exit 0" if code == 0 else out[-300:])
        bare = overlay(src_s, None, 0, 0, tmp / f"neg_{w}x{h}_no_badge.mp4", meta.ffmpeg_args(src_s, 0))
        code, out = verify(bare, "--badge-spec", str(png_s) + ".json", "--source", str(src_s), *dims(w, h))
        check(code != 0 and "[FAIL] AI 角标像素:" in out, f"verify 反例·小画幅 {w}x{h} 不叠角标", f"exit={code}")
    # 腐蚀后掩码偏在少数字上的字号(STHeiti 下短边 ≤ 700 一带):盖住其余的字也要抓到
    for (w, h), cover_frac, what in (((540, 960), 3, "「视频」"), ((610, 1084), 5, "左五分之一")):
        png_m = tmp / f"badge_{w}x{h}.png"
        spec_m = rt.badge(str(png_m), width=w, height=h)
        src_m = make_src(tmp / f"src_{w}x{h}.mp4", w, h, DARK)
        fin_m = overlay(src_m, png_m, spec_m["x"], spec_m["y"], tmp / f"fin_{w}x{h}.mp4", meta.ffmpeg_args(src_m, 0))
        code, out = verify(fin_m, "--badge-spec", str(png_m) + ".json", "--source", str(src_m), *dims(w, h))
        check(code == 0, f"verify 正例·{w}x{h}(字号 {spec_m['font_size']})", "exit 0" if code == 0 else out[-300:])
        covered = tmp / f"neg_{w}x{h}_cover.mp4"
        cover = (f"drawbox=x={spec_m['x']}:y={spec_m['y']}:w={spec_m['w'] // cover_frac}:h={spec_m['h']}"
                 ":color=black:t=fill:enable='between(t,1,2.5)'")
        ff("-i", str(src_m), "-loop", "1", "-i", str(png_m), "-filter_complex",
           f"[0:v][1:v]overlay={spec_m['x']}:{spec_m['y']}:shortest=1,{cover}[v]", "-map", "[v]", "-map", "0:a",
           "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", *meta.ffmpeg_args(src_m, 0), str(covered))
        code, out = verify(covered, "--badge-spec", str(png_m) + ".json", "--source", str(src_m), *dims(w, h))
        check(code != 0 and "[FAIL] AI 角标像素:" in out, f"verify 反例·{w}x{h} 盖住{what} 1.5 s", f"exit={code}")
    for anchor in ("tl", "br", "bl"):
        s = rt.badge(str(tmp / f"a_{anchor}.png"), width=1080, height=1920, anchor=anchor)
        left, top = s["x"], s["y"]
        right, bottom = 1080 - s["x"] - s["w"], 1920 - s["y"] - s["h"]
        ok = (left if anchor[1] == "l" else right) == 40 and (top if anchor[0] == "t" else bottom) == 60
        check(ok, f"anchor={anchor} 贴角(边距 40,60)", f"({left},{top}) 右 {right} 下 {bottom}")

    # 反例:旧角标的字形实测确实过不了线,量法本身能抓出来
    old_png = tmp / "old_badge.png"
    ow, oh = old_badge(old_png)
    src = samples[(1080, 1920, DARK)][0]
    old_fin = overlay(src, old_png, 1080 - ow - 40, 60, tmp / "fin_old_badge.mp4", meta.ffmpeg_args(src, 0))
    inks = ink_heights(old_fin, 1080 - ow - 40, 60, ow, oh)
    check(all(v < 54 for v in inks.values()), "反例·旧角标实测字形高 < 54", json.dumps(inks))
    old_spec = dict(rt.badge(str(tmp / "unused.png"), width=1080, height=1920))
    old_spec.update(text=meta.RETIRED_BADGE_TEXTS[0], font_size=34, ink_height_px=min(inks.values()),
                    x=1080 - ow - 40, y=60, w=ow, h=oh, png=old_png.name)
    (tmp / "old_badge.png.json").write_text(json.dumps(old_spec, ensure_ascii=False), encoding="utf-8")
    samples["old"] = (src, old_fin, old_png)
    return samples


def section_verify_negatives(tmp: Path, samples: dict) -> None:
    print("\n== 3. verify_package.py 反例 ==")
    src, fin, png = samples[(1080, 1920, DARK)]
    spec = str(png) + ".json"
    no_badge = overlay(src, None, 0, 0, tmp / "neg_no_badge.mp4", meta.ffmpeg_args(src, 0))
    expect_fail("不叠角标(深底)", no_badge, "AI 角标像素", "--badge-spec", spec, "--source", str(src))
    white = make_src(tmp / "src_white.mp4", 1080, 1920, WHITE)
    white_fin = overlay(white, None, 0, 0, tmp / "neg_white_no_badge.mp4", meta.ffmpeg_args(white, 0))
    expect_fail("纯白底不叠角标", white_fin, "AI 角标像素", "--badge-spec", spec, "--source", str(white))
    _, old_fin, _ = samples["old"]
    expect_fail("旧角标配新几何", old_fin, "AI 角标像素", "--badge-spec", spec, "--source", str(src))
    old_spec = str(tmp / "old_badge.png.json")
    expect_fail("旧角标·字高", old_fin, "AI 角标字高", "--badge-spec", old_spec, "--source", str(src))
    expect_fail("旧角标·措辞", old_fin, "AI 角标措辞", "--badge-spec", old_spec, "--source", str(src))
    big_src, big_fin, _ = samples[(1440, 2560, DARK)]
    expect_fail("画幅不符", big_fin, "AI 角标画幅", "--badge-spec", spec, "--source", str(big_src), *dims(1440, 2560))
    no_desc = overlay(src, png, *_xy(spec), tmp / "neg_no_desc.mp4",
                      ["-map_metadata", "0", "-movflags", "+use_metadata_tags"])
    expect_fail("缺说明字段", no_desc, "说明字段", "--badge-spec", spec, "--source", str(src))
    stripped = overlay(src, png, *_xy(spec), tmp / "neg_stripped.mp4",
                       STRIP_ALL + ["-metadata", f"description={meta.AI_DESCRIPTION}"])
    expect_fail("源键丢失", stripped, "源元数据保留", "--badge-spec", spec, "--source", str(src))
    late_start = overlay(src, png, *_xy(spec), tmp / "neg_badge_late_start.mp4", meta.ffmpeg_args(src, 0), "gte(t,0.2)")
    expect_fail("角标 0.2 s 才出现(首帧没有)", late_start, "AI 角标像素", "--badge-spec", spec, "--source", str(src))
    early = overlay(src, png, *_xy(spec), tmp / "neg_badge_early_only.mp4", meta.ffmpeg_args(src, 0), "lt(t,0.5)")
    expect_fail("角标只在开头 0.5 s", early, "AI 角标像素", "--badge-spec", spec, "--source", str(src))
    middle = overlay(src, png, *_xy(spec), tmp / "neg_badge_gap_middle.mp4", meta.ffmpeg_args(src, 0),
                     "not(between(t,1.9,2.1))")
    expect_fail("中段 1.9–2.1 s 掉角标(只有 2 s 那一帧抽得到)", middle, "AI 角标像素", "--badge-spec", spec, "--source", str(src))
    late = overlay(src, png, *_xy(spec), tmp / "neg_badge_drops_at_end.mp4", meta.ffmpeg_args(src, 0), "lt(t,2.8)")
    expect_fail("末尾 0.2 s 掉角标", late, "AI 角标像素", "--badge-spec", spec, "--source", str(src))
    faint_png = tmp / "faint_badge.png"
    img = Image.open(png).convert("RGBA")
    img.putalpha(img.getchannel("A").point(lambda a: 110 if a >= 200 else a))
    img.save(faint_png)
    faint = overlay(src, faint_png, *_xy(spec), tmp / "neg_faint_badge.mp4", meta.ffmpeg_args(src, 0))
    expect_fail("淡角标(字 alpha 110)", faint, "AI 角标像素", "--badge-spec", spec, "--source", str(src))
    changed = overlay(src, png, *_xy(spec), tmp / "neg_value_changed.mp4",
                      meta.ffmpeg_args(src, 0) + ["-metadata", "comment=changed by script"])
    expect_fail("源键被改", changed, "源元数据保留", "--badge-spec", spec, "--source", str(src))
    brand_dup = overlay(src, png, *_xy(spec), tmp / "neg_brand_dup.mp4",
                        ["-map_metadata", "0", "-metadata", f"description={meta.AI_DESCRIPTION}",
                         "-movflags", "+faststart+use_metadata_tags"])
    expect_fail("brand 三键重复值", brand_dup, "源元数据保留", "--badge-spec", spec, "--source", str(src))
    expect_fail("--source 传了终版自己", fin, "源元数据保留", "--badge-spec", spec, "--source", str(fin),
                detail="同一个文件")
    fin_copy = tmp / "fin_copy.mp4"
    shutil.copyfile(fin, fin_copy)
    expect_fail("--source 传了终版的副本", fin, "源元数据保留", "--badge-spec", spec, "--source", str(fin_copy),
                detail="带 description")
    for label, source, needle in (("终版自己", fin, "同一个文件"), ("终版副本", fin_copy, "带 description")):
        cli = meta_cli("check", str(fin), "--source", str(source))
        check(cli.returncode == 1 and needle in cli.stdout, f"CLI check·--source 传了{label}", f"exit={cli.returncode}")
    cli = meta_cli("check", str(no_desc), "--source", str(src))
    check(cli.returncode == 1 and "description" in cli.stdout, "CLI check·终版缺说明字段", f"exit={cli.returncode}")
    expect_fail("--source 文件不存在", fin, "源元数据保留", "--badge-spec", spec, "--source", str(tmp / "nope.mp4"),
                detail="源片不存在")
    garbage = tmp / "garbage.mp4"
    garbage.write_bytes(b"not a video" * 100)
    expect_fail("--source 读不了", fin, "源元数据保留", "--badge-spec", spec, "--source", str(garbage),
                detail="读不了")
    alone = tmp / "alone"
    alone.mkdir()
    shutil.copy(VERIFY, alone / VERIFY.name)
    run = subprocess.run([sys.executable, str(alone / VERIFY.name), "--video", str(fin), "--badge-spec", spec,
                          "--source", str(src)], capture_output=True, text=True, check=False)
    check(run.returncode != 0 and "[FAIL] aigc_meta.py:" in run.stdout, "verify 脱离插件目录、找不到 aigc_meta.py 时 FAIL",
          f"exit={run.returncode}")
    clash = tmp / "src_desc.mp4"
    expect_fail("--source 带 description", fin, "源元数据保留", "--badge-spec", spec, "--source", str(clash),
                detail="带 description")
    base_spec = json.loads(Path(spec).read_text(encoding="utf-8"))
    for n, (label, change, item, needle) in enumerate((
        ("几何 JSON 墨迹不足", {"ink_height_px": 40}, "AI 角标字高", "墨迹"),
        ("几何 JSON 字号不足", {"font_size": 40}, "AI 角标字高", "字号"),
        ("措辞只有 AI 没有生成", {"text": "视频由 AI 打造"}, "AI 角标措辞", "缺两要素"),
        ("措辞只有生成没有 AI", {"text": "画面由算法生成"}, "AI 角标措辞", "缺两要素"),
    )):
        bad_spec = tmp / f"spec_bad_{n}.png.json"
        bad_spec.write_text(json.dumps(dict(base_spec, **change), ensure_ascii=False), encoding="utf-8")
        expect_fail(label, fin, item, "--badge-spec", str(bad_spec), "--source", str(src), detail=needle)
    _, old_fin, old_png = samples["old"]
    ow, oh = Image.open(old_png).size
    liar = tmp / "liar_old_badge.png.json"
    liar.write_text(json.dumps(dict(base_spec, x=1080 - ow - 40, y=60, w=ow, h=oh, png=old_png.name)), encoding="utf-8")
    expect_fail("JSON 自报够、PNG 实测不够", old_fin, "AI 角标字高", "--badge-spec", str(liar), "--source", str(src),
                detail="实测字高")
    one_elem = overlay(src, png, *_xy(spec), tmp / "neg_desc_one_element.mp4",
                       ["-map_metadata", "0", "-metadata", "major_brand=", "-metadata", "minor_version=",
                        "-metadata", "compatible_brands=", "-metadata", "description=本视频由 AI 辅助完成",
                        "-movflags", "+faststart+use_metadata_tags"])
    expect_fail("说明字段只有 AI 没有生成", one_elem, "说明字段", "--badge-spec", spec, "--source", str(src))
    cli = meta_cli("check", str(one_elem), "--source", str(src))
    check(cli.returncode == 1 and "description" in cli.stdout, "CLI check·说明字段缺一要素", f"exit={cli.returncode}")
    tex = tmp / "src_texture.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=s=1080x1920:d=3:r=25", "-f", "lavfi", "-i", f"sine=f=440:d={DURATION}",
       "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", *tag_args(SRC_TAGS),
       "-movflags", "+use_metadata_tags", str(tex))
    tex_fin = overlay(tex, png, *_xy(spec), tmp / "fin_texture.mp4", meta.ffmpeg_args(tex, 0))
    code, out = verify(tex_fin, "--badge-spec", spec, "--source", str(tex))
    check(code == 0, "verify 正例·有纹理的底片", "exit 0" if code == 0 else out[-400:])
    tdata, total = tex_fin.read_bytes(), decoded_frames(tex_fin)
    cut2 = tmp / "neg_truncated_2frames.mp4"
    lo, hi = len(tdata) // 2, len(tdata)   # 二分出「恰好少 2 帧」的截断样本:B 帧终版少几帧也得判截断
    while lo < hi:
        mid = (lo + hi) // 2
        cut2.write_bytes(tdata[:mid])
        lo, hi = (mid + 1, hi) if decoded_frames(cut2) < total - 2 else (lo, mid)
    cut2.write_bytes(tdata[:lo])
    got = decoded_frames(cut2)
    if check(got == total - 2, "构造「恰好少 2 帧」的截断样本", f"{got}/{total}"):
        expect_fail("终版被截断(只少 2 帧)", cut2, "AI 角标像素", "--badge-spec", spec, "--source", str(tex), detail="截断")
    trimmed = tmp / "fin_head_trimmed.mp4"
    ff("-ss", "1", "-i", str(fin), "-c", "copy", *meta.ffmpeg_args(fin, 0), str(trimmed))
    code, out = verify(trimmed, "--badge-spec", spec, "--source", str(src))
    check(code == 0, "verify 正例·头部 -c copy 剪掉 1 s(编辑表藏了预滚帧，不误判截断)", "exit 0" if code == 0 else out[-400:])
    between = tmp / "fin_head_trimmed_between_frames.mp4"
    ff("-ss", "1.01", "-i", str(fin), "-c", "copy", *meta.ffmpeg_args(fin, 0), str(between))
    code, out = verify(between, "--badge-spec", spec, "--source", str(src))
    check(code == 0, "verify 正例·头部剪在两帧之间(编辑表带小数帧，不误判截断)", "exit 0" if code == 0 else out[-400:])
    both = tmp / "fin_head_tail_trimmed.mp4"
    ff("-ss", "1.02", "-i", str(fin), "-t", "1.3", "-c", "copy", *meta.ffmpeg_args(fin, 0), str(both))
    code, out = verify(both, "--badge-spec", spec, "--source", str(src))
    check(code == 0, "verify 正例·头尾都 -c copy 剪过(中间缺帧，不误判截断)", "exit 0" if code == 0 else out[-400:])
    longer = tmp / "fin_audio_longer.mp4"
    ff("-i", str(src), "-loop", "1", "-i", str(png), "-f", "lavfi", "-i", f"sine=f=330:d={DURATION + 0.4}",
       "-filter_complex", f"[0:v][1:v]overlay={_xy(spec)[0]}:{_xy(spec)[1]}:shortest=1[v]",
       "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac",
       *meta.ffmpeg_args(src, 0), str(longer))
    code, out = verify(longer, "--badge-spec", spec, "--source", str(src), "--max-duration", "10")
    check(code == 0, "verify 正例·音轨比画面长 0.4 s(不误判截断)", "exit 0" if code == 0 else out[-400:])
    w7, h7 = 720, 1280
    png7 = tmp / "badge_720x1280.png"
    spec7 = json.loads(Path(str(png7) + ".json").read_text(encoding="utf-8"))
    vfr_src = tmp / "src_vfr.mp4"
    ff("-f", "lavfi", "-i", f"color=c={DARK}:s={w7}x{h7}:d=5:r=30", "-f", "lavfi", "-i", "sine=f=440:d=4",
       "-vf", "setpts='if(lt(N,30),N/30,if(lt(N,90),(30+(N-30)/2)/30,(N-30)/30))/TB'", "-fps_mode", "passthrough",
       "-frames:v", "150", "-video_track_timescale", "600", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
       *tag_args(SRC_TAGS), "-movflags", "+use_metadata_tags", "-shortest", str(vfr_src))
    vfr_fin = overlay(vfr_src, png7, spec7["x"], spec7["y"], tmp / "fin_vfr.mp4",
                      meta.ffmpeg_args(vfr_src, 0) + ["-fps_mode", "passthrough", "-video_track_timescale", "600"])
    code, out = verify(vfr_fin, "--badge-spec", str(png7) + ".json", "--source", str(vfr_src), *dims(w7, h7))
    check(code == 0, "verify 正例·变帧率终版(不因补帧／丢帧误判截断)", "exit 0" if code == 0 else out[-400:])
    gap_src = tmp / "src_vfr_gap.mp4"
    ff("-f", "lavfi", "-i", f"color=c={DARK}:s={w7}x{h7}:d=10:r=25", "-f", "lavfi", "-i", "sine=f=440:d=7",
       "-vf", "setpts='if(lt(N,50),N/25,if(lt(N,53),2+(N-50),5+(N-53)/25))/TB'", "-frames:v", "103",
       "-fps_mode", "passthrough", "-video_track_timescale", "600", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-c:a", "aac", *tag_args(SRC_TAGS), "-movflags", "+use_metadata_tags", "-shortest", str(gap_src))
    gap_fin = overlay(gap_src, png7, spec7["x"], spec7["y"], tmp / "neg_vfr_gap.mp4",
                      meta.ffmpeg_args(gap_src, 0) + ["-fps_mode", "passthrough", "-video_track_timescale", "600"],
                      "not(between(t,2,4.99))")
    expect_fail("变帧率终版在慢段(每秒 1 帧的 2–5 s)掉角标", gap_fin, "AI 角标像素", "--badge-spec", str(png7) + ".json",
                "--source", str(gap_src), *dims(w7, h7))
    held_src = tmp / "src_vfr_held.mp4"
    ff("-f", "lavfi", "-i", f"color=c={DARK}:s={w7}x{h7}:d=10:r=25", "-f", "lavfi", "-i", "sine=f=440:d=5",
       "-vf", "setpts='if(lt(N,28),N/25,3+(N-28)/25)/TB'", "-frames:v", "78",
       "-fps_mode", "passthrough", "-video_track_timescale", "600", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-c:a", "aac", *tag_args(SRC_TAGS), "-movflags", "+use_metadata_tags", "-shortest", str(held_src))
    held_fin = overlay(held_src, png7, spec7["x"], spec7["y"], tmp / "neg_vfr_held.mp4",
                       meta.ffmpeg_args(held_src, 0) + ["-fps_mode", "passthrough", "-video_track_timescale", "600"],
                       "not(between(t,1.07,1.09))")
    expect_fail("变帧率终版一帧从 1.08 s 停到 3 s、只这一帧掉角标", held_fin, "AI 角标像素",
                "--badge-spec", str(png7) + ".json", "--source", str(held_src), *dims(w7, h7))
    zeroed = bytearray(fin.read_bytes())
    at = zeroed.find(b"mdat") - 4
    end = at + struct.unpack(">I", bytes(zeroed[at:at + 4]))[0]
    zeroed[at + 8:end] = bytes(end - at - 8)
    zero_fin = tmp / "neg_payload_zeroed.mp4"
    zero_fin.write_bytes(bytes(zeroed))
    expect_fail("结构完整但画面一帧也解不出(mdat 内容清零)", zero_fin, "AI 角标像素", "--badge-spec", spec,
                "--source", str(src), detail="为零")
    data = fin.read_bytes()
    for label, cut in (("只少最后 1 个字节", len(data) - 1), ("截掉后 60%", len(data) * 4 // 10),
                       ("只剩 moov", data.find(b"mdat") + 4)):
        cut_file = tmp / f"neg_truncated_{cut}.mp4"
        cut_file.write_bytes(data[:cut])
        expect_fail(f"终版被截断({label})", cut_file, "AI 角标像素", "--badge-spec", spec, "--source", str(src),
                    detail="截断")
    code, out = verify(fin, "--badge-spec", spec, "--source", str(src), env={"AV_LOG_FORCE_COLOR": "1"})
    check(code == 0 and "[PASS] AI 角标像素" in out, "verify 正例·环境强制 ffmpeg 彩色日志也照常解析", f"exit={code}")
    open_end = bytearray(fin.read_bytes())
    at = open_end.find(b"mdat") - 4
    open_end[at:at + 4] = bytes(4)   # mdat 长度写 0 =「一直到文件尾」
    open_fin = tmp / "fin_mdat_size0.mp4"
    open_fin.write_bytes(bytes(open_end))
    code, out = verify(open_fin, "--badge-spec", spec, "--source", str(src))
    check(code == 0 and "[WARN] AI 角标像素" in out and "无从核对" in out,
          "mdat 长度写 0(到文件尾)时报「截断无从核对」WARN，不冒充核过", f"exit={code}")
    bx, by = _xy(spec)
    bw, bh = json.loads(Path(spec).read_text(encoding="utf-8"))["w"], json.loads(Path(spec).read_text(encoding="utf-8"))["h"]
    cover_x = bx + bw * 4 // 5
    for label, extra_filter, needle in (
        ("插入面板盖住角标右边五分之一(「成」字)", f"drawbox=x={cover_x}:y={by}:w={bx + bw - cover_x}:h={bh}:color=black:t=fill:enable='between(t,1,2.5)'", "亮字占比"),
        ("白闪 85% 盖过角标", "drawbox=x=0:y=0:w=iw:h=ih:color=white@0.85:t=fill:enable='between(t,0.9,1.1)'", "字形−底框"),
    ):
        partial = tmp / f"neg_partial_{len(extra_filter)}.mp4"
        ff("-i", str(src), "-loop", "1", "-i", str(png),
           "-filter_complex", f"[0:v][1:v]overlay={bx}:{by}:shortest=1,{extra_filter}[v]",
           "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac",
           *meta.ffmpeg_args(src, 0), str(partial))
        expect_fail(label, partial, "AI 角标像素", "--badge-spec", spec, "--source", str(src), detail=needle)
    dual = tmp / "src_dual_brand.mp4"
    ff("-i", str(src), "-c", "copy", "-map_metadata", "0", "-movflags", "+use_metadata_tags", str(dual))
    dual_bytes = dual.read_bytes()
    stamp = meta.format_tags(dual).get("encoder", "").encode()
    older = dual_bytes.replace(stamp, (b"Lavf59.27.10" + b"0" * len(stamp))[:len(stamp)]) if stamp else dual_bytes
    dual.write_bytes(older)
    dual_fin = overlay(dual, png, bx, by, tmp / "fin_dual_brand.mp4", meta.ffmpeg_args(dual, 0))
    dtags = meta.format_tags(dual)
    code, out = verify(dual_fin, "--badge-spec", spec, "--source", str(dual))
    check(";" in dtags.get("minor_version", "") and dtags.get("encoder") != meta.format_tags(dual_fin).get("encoder")
          and code == 0 and not meta.compare(dual_fin, dual),
          "verify 正例·源片 brand 重复值、encoder 是旧版本(即梦段那种)不误报",
          f"源 encoder={dtags.get('encoder')} minor={dtags.get('minor_version')} exit={code}")
    covers = []
    for name, size in (("cover_v.png", (1080, 1440)), ("cover_b.png", (1440, 1080)), ("cover_y.png", (1080, 1920))):
        Image.new("RGB", size, (20, 20, 20)).save(tmp / name)
        covers.append(str(tmp / name))
    code, out = verify(fin, "--badge-spec", spec, "--source", str(src), "--cover-vertical", covers[0],
                       "--cover-bilibili", covers[1], "--cover-youtube-shorts", covers[2])
    lines = [line for line in out.splitlines() if line.startswith("- ")]
    first = next((i for i, line in enumerate(lines) if "确认第一句实际口播" in line), -1)
    cover = next((i for i, line in enumerate(lines) if "三版平台封面" in line), -1)
    check(code == 0 and first >= 0 and cover == first + 1, "人工清单:封面条目紧跟在「第一句」条目之后",
          f"exit={code} 位置 {first}/{cover}")
    expect_fail("缺 --badge-spec", fin, "AI 角标未机检", "--source", str(src))
    expect_fail("缺 --source", fin, "源元数据保留未核", "--badge-spec", spec)

    legacy = overlay(src, None, 0, 0, tmp / "legacy.mp4", STRIP_ALL)
    expect_fail("旧片不带 --legacy-audit", legacy, "AI 角标未机检")
    code, out = verify(legacy, "--legacy-audit")
    check(code == 0 and "[WARN] AI 角标未机检" in out and "[WARN] 说明字段" in out
          and "[WARN] 源元数据保留未核" in out and "[FAIL]" not in out,
          "旧片带 --legacy-audit:新增项降为 WARN、exit 0", f"exit={code}")
    code, out = verify(legacy, "--legacy-audit", "--source", str(src))
    check(code == 0 and "[WARN] 源元数据保留:" in out and "[FAIL]" not in out,
          "旧片带 --legacy-audit 和 --source:源键丢失也只报 WARN", f"exit={code}")


def _xy(spec_path: str) -> tuple[int, int]:
    s = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    return s["x"], s["y"]


def section_metadata(tmp: Path, samples: dict) -> None:
    print("\n== 2. 元数据 ==")
    src = samples[(1080, 1920, DARK)][0]
    wav = tmp / "ext.wav"
    ff("-f", "lavfi", "-i", f"sine=f=220:d={DURATION}", str(wav))
    src_tags = meta.format_tags(src)
    for index in (0, 1):
        out = tmp / f"meta_src_at_{index}.mp4"
        inputs = ["-i", str(src), "-i", str(wav)] if index == 0 else ["-i", str(wav), "-i", str(src)]
        ff(*inputs, "-map", f"{index}:v", "-map", f"{1 - index}:a", "-c:v", "libx264", "-crf", "18",
           "-pix_fmt", "yuv420p", "-c:a", "aac", *meta.ffmpeg_args(src, index), str(out))
        tags = meta.format_tags(out)
        for key in ("AIGC", "workflow", "title", "artist", "comment"):
            check(tags.get(key) == src_tags.get(key), f"源片在第 {index} 路·{key} 逐字保留")
        check(tags.get("description") == meta.AI_DESCRIPTION and meta.has_two_elements(tags.get("description")),
              f"源片在第 {index} 路·description 为说明字段且含两要素")
        check(all(";" not in tags.get(k, "") for k in meta.BRAND_KEYS), f"源片在第 {index} 路·brand 三键无重复值",
              json.dumps({k: tags.get(k) for k in meta.BRAND_KEYS}))
        problems = meta.compare(out, src)
        check(not problems, f"源片在第 {index} 路·compare 无问题", "; ".join(problems))
        check(any("workflow" in f for f in meta.privacy_flags(out)), f"源片在第 {index} 路·privacy_flags 提示 workflow")

    plain = make_src(tmp / "src_plain.mp4", 1080, 1920, DARK,
                     {"title": "plain", "creation_time": "2026-08-04T12:34:56.000000Z"}, mdta=False)
    plain_out = tmp / "meta_plain.mp4"
    ff("-i", str(plain), "-c", "copy", *meta.ffmpeg_args(plain, 0), str(plain_out))
    problems = meta.compare(plain_out, plain)
    check(not problems, "普通源片的 creation_time(终版读成 X;X)compare 不误报", "; ".join(problems))
    ct_changed = tmp / "meta_ct_changed.mp4"
    ff("-i", str(plain), "-c", "copy", *meta.ffmpeg_args(plain, 0),
       "-metadata", "creation_time=2030-01-01T00:00:00.000000Z", str(ct_changed))
    problems = meta.compare(ct_changed, plain)
    check(any("creation_time" in x for x in problems), "creation_time 真被改了仍判「值变了」", "; ".join(problems))
    mdta_ct = make_src(tmp / "src_mdta_ct.mp4", 1080, 1920, DARK,
                       {"title": "mdta", "creation_time": "2026-08-04T12:34:56.000000Z"}, dur=1)
    mdta_out = tmp / "meta_mdta_ct.mp4"
    ff("-i", str(mdta_ct), "-c", "copy", *meta.ffmpeg_args(mdta_ct, 0), str(mdta_out))
    src_ct, out_ct = mvhd_creation_time(mdta_ct), mvhd_creation_time(mdta_out)
    check(src_ct != 0 and out_ct == src_ct and not meta.compare(mdta_out, mdta_ct),
          "mdta 源片的 creation_time:mvhd 创建时间不被写成 0", f"源 {src_ct} 终 {out_ct}")
    for label, value in (("不带微秒", "2026-08-04T12:34:56Z"), ("带小数秒", "2026-08-04T12:34:56.5Z")):
        ab = make_src(tmp / f"src_ab_{len(value)}.mp4", 320, 240, DARK, {"creation_time": value}, dur=1)
        ab_out = tmp / f"meta_ab_{len(value)}.mp4"
        ff("-i", str(ab), "-c", "copy", *meta.ffmpeg_args(ab, 0), str(ab_out))
        same = meta.format_tags(ab_out).get("creation_time") == meta.format_tags(ab).get("creation_time")
        check(";" in meta.format_tags(ab)["creation_time"] and mvhd_creation_time(ab_out) == mvhd_creation_time(ab) != 0
              and same and not meta.compare(ab_out, ab),
              f"mdta 源片 creation_time 读成「A;B」({label}):mvhd 不归零、读回逐字相同",
              f"{meta.format_tags(ab)['creation_time']} → mvhd {mvhd_creation_time(ab_out)}")
    vhs = make_src(tmp / "src_vhs_now.mp4", 320, 240, DARK, {"title": "vhs", "creation_time": "now"}, dur=1)
    vhs_out = tmp / "meta_vhs_now.mp4"
    ff("-i", str(vhs), "-c", "copy", *meta.ffmpeg_args(vhs, 0), str(vhs_out))
    check(meta.format_tags(vhs)["creation_time"].endswith(";now") and mvhd_creation_time(vhs_out) == mvhd_creation_time(vhs)
          and not meta.compare(vhs_out, vhs),
          "creation_time 原串是 now(ComfyUI VHS 保存节点默认):mvhd 不变、compare 不误报",
          f"{meta.format_tags(vhs)['creation_time']} → mvhd {mvhd_creation_time(vhs)}/{mvhd_creation_time(vhs_out)}")
    vhs_args = meta.ffmpeg_args(vhs, 0)
    mvhd_half = meta.format_tags(vhs)["creation_time"].split(";")[0]
    check(f"creation_time={mvhd_half}" in vhs_args and "creation_time=now" not in vhs_args,
          "creation_time 原串是 now 时写回的是 mvhd 那半(不是 now)", " ".join(a for a in vhs_args if "creation" in a))
    vhs_moved = tmp / "meta_vhs_moved.mp4"
    ff("-i", str(vhs), "-c", "copy", *vhs_args, "-metadata", "creation_time=2030-01-01T00:00:00.000000Z", str(vhs_moved))
    check(any("creation_time" in x for x in meta.compare(vhs_moved, vhs)),
          "creation_time 原串是 now、终版时刻真被改了仍判变")
    for zone, raw in (("+08:00", "2026-08-04T20:34:56+08:00"), ("-05:30", "2026-08-04T07:04:56-05:30")):
        zsrc = make_src(tmp / f"src_zone_{zone[1:3]}.mp4", 320, 240, DARK, {"creation_time": raw}, dur=1)
        zout = tmp / f"meta_zone_{zone[1:3]}.mp4"
        ff("-i", str(zsrc), "-c", "copy", *meta.ffmpeg_args(zsrc, 0), str(zout))
        same = meta.format_tags(zout)["creation_time"] == meta.format_tags(zsrc)["creation_time"]
        check(same and mvhd_creation_time(zout) == mvhd_creation_time(zsrc) and not meta.compare(zout, zsrc),
              f"creation_time 原串带时区 {zone}:终版读回逐字相同", meta.format_tags(zout)["creation_time"])
        rezoned = tmp / f"meta_zone_{zone[1:3]}_as_z.mp4"
        ff("-i", str(zsrc), "-c", "copy", *meta.ffmpeg_args(zsrc, 0), "-metadata",
           "creation_time=2026-08-04T12:34:56Z", str(rezoned))
        check(any("creation_time" in x for x in meta.compare(rezoned, zsrc)),
              f"creation_time 原串带时区 {zone}、终版改写成同一时刻的 Z 形式也判变(原串要逐字相同)")
    for label, raw in (("闰秒 :60", "2016-12-31T23:59:60Z"), ("2 月 30 日", "2026-02-30T00:00:00Z")):
        odd = make_src(tmp / f"src_odd_{len(label)}.mp4", 320, 240, DARK, {"title": "odd", "creation_time": raw}, dur=1)
        odd_out = tmp / f"meta_odd_{len(label)}.mp4"
        try:
            ff("-i", str(odd), "-c", "copy", *meta.ffmpeg_args(odd, 0), str(odd_out))
            problems = meta.compare(odd_out, odd)
            cli = meta_cli("check", str(odd_out), "--source", str(odd))
            check(isinstance(problems, list) and "Traceback" not in cli.stderr and cli.returncode in (0, 1),
                  f"creation_time 是不存在的时间({label}):不崩溃", f"check exit={cli.returncode}")
        except Exception as exc:  # noqa: BLE001
            check(False, f"creation_time 是不存在的时间({label}):不崩溃", f"{type(exc).__name__}: {exc}")
    mkv_ct = tmp / "src_mkv_subsecond.mkv"
    ff("-f", "lavfi", "-i", f"color=c={DARK}:s=320x240:d=1:r=25", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-metadata", "creation_time=2026-08-04T12:34:56.500000Z", str(mkv_ct))
    mkv_ct_out = tmp / "meta_mkv_subsecond.mp4"
    ff("-i", str(mkv_ct), "-c", "copy", *meta.ffmpeg_args(mkv_ct, 0), str(mkv_ct_out))
    check(not meta.compare(mkv_ct_out, mkv_ct), "Matroska 源片 creation_time 带小数秒(MP4 的 mvhd 只到秒)不误报",
          "; ".join(meta.compare(mkv_ct_out, mkv_ct)))
    brand = tmp / "src_brand_mp42.mp4"
    ff("-f", "lavfi", "-i", "color=c=0x202020:s=320x240:d=1:r=25", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-brand", "mp42", "-metadata", "title=brand", str(brand))
    brand_out = tmp / "meta_brand_mp42.mp4"
    ff("-i", str(brand), "-c", "copy", *meta.ffmpeg_args(brand, 0), str(brand_out))
    src_b, out_b = meta.format_tags(brand).get("major_brand"), meta.format_tags(brand_out).get("major_brand")
    check(src_b != out_b and not meta.compare(brand_out, brand), "封装器键(brand)源片与终版不同时不误报",
          f"{src_b} → {out_b}")
    mkv = tmp / "src_matroska.mkv"
    ff("-f", "lavfi", "-i", f"color=c={DARK}:s=320x240:d=1:r=25", "-f", "lavfi", "-i", "sine=d=1",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-metadata", "title=mkv", str(mkv))
    mkv_out = tmp / "meta_from_mkv.mp4"
    ff("-i", str(mkv), "-c", "copy", *meta.ffmpeg_args(mkv, 0), str(mkv_out))
    check("ENCODER" in meta.format_tags(mkv) and not meta.compare(mkv_out, mkv),
          "Matroska 源片(键名大写 ENCODER)不误报封装器键", "; ".join(meta.compare(mkv_out, mkv)))
    plain_mp4 = make_src(tmp / "src_plain_title.mp4", 320, 240, DARK, {"title": "mkv"}, dur=1)
    check(not meta.write_sidecar(tmp / "mixed.json", [mkv, plain_mp4]), "sidecar:MKV 与 MP4 源片只差封装器键时不报「各段不同」")
    win = make_src(tmp / "src_winpath.mp4", 320, 240, DARK, {"comment": "D:\\ComfyUI\\output\\clip.mp4"}, dur=1)
    check(any("Windows" in f for f in meta.privacy_flags(win)), "privacy_flags 提示 Windows 盘符路径")
    side = meta_cli("sidecar", str(tmp / "side.json"), str(tmp / "nope.mp4"))
    check(side.returncode == 2 and "Traceback" not in side.stderr, "CLI sidecar 读不了文件时退出码 2、不抛 traceback")
    cli = meta_cli("check", str(mdta_out), "--source", f"file:{mdta_ct}")
    check("Traceback" not in cli.stderr and cli.returncode == 0, "CLI check 的 --source 带 file: 前缀照常比对、不抛 traceback",
          f"exit={cli.returncode}")
    try:
        meta.ffmpeg_args(src, -1)
        check(False, "ffmpeg_args 拒绝负的输入序号")
    except ValueError:
        check(True, "ffmpeg_args 拒绝负的输入序号")
    missing = subprocess.run([sys.executable, str(HERE / "aigc_meta.py"), "check", str(tmp / "nope.mp4"),
                              "--source", str(src)], capture_output=True, text=True, check=False)
    check(missing.returncode != 0 and "Traceback" not in missing.stderr, "CLI check 读不了文件时报 FAIL、不抛 traceback")

    for label, desc in (("另一句含两要素的说明", "视频由AI生成；人物画面由开源模型生成"),
                        ("我方说明字段后面多了附注", meta.AI_DESCRIPTION + "（附注）")):
        other = make_src(tmp / f"src_desc_{len(desc)}.mp4", 320, 240, DARK, {"description": desc}, dur=1)
        try:
            meta.ffmpeg_args(other, 0)
            check(False, f"冲突护栏:源片 description 是{label}时也报冲突")
        except meta.DescriptionConflict:
            check(True, f"冲突护栏:源片 description 是{label}时也报冲突")
    mkv_desc = tmp / "src_desc.mkv"
    ff("-f", "lavfi", "-i", f"color=c={DARK}:s=320x240:d=1:r=25", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-metadata", "description=SRC DESC", str(mkv_desc))
    try:
        meta.ffmpeg_args(mkv_desc, 0)
        raised = False
    except meta.DescriptionConflict:
        raised = True
    cli = meta_cli("args", str(mkv_desc), "0")
    check("DESCRIPTION" in meta.format_tags(mkv_desc) and raised and cli.returncode == 2,
          "冲突护栏:Matroska 源片的 DESCRIPTION(大写键名)也报冲突", f"args exit={cli.returncode}")
    clash = make_src(tmp / "src_desc.mp4", 1080, 1920, DARK, dict(SRC_TAGS, description="SRCDESC"), dur=1)
    try:
        meta.ffmpeg_args(clash, 0)
        check(False, "冲突护栏:源片已有 description 时 ffmpeg_args 抛错")
    except meta.DescriptionConflict as exc:
        check(True, "冲突护栏:源片已有 description 时 ffmpeg_args 抛错", str(exc)[-40:])
    cli = subprocess.run([sys.executable, str(HERE / "aigc_meta.py"), "args", str(clash), "0"],
                         capture_output=True, text=True, check=False)
    check(cli.returncode != 0 and not cli.stdout.strip(), "冲突护栏:CLI args 非零退出且不输出参数")


def section_compose(tmp: Path) -> None:
    print("\n== 2b. compose.sh 端到端(/bin/bash) ==")
    seg_dur = {"dh1": 1.2, "card": 1.0, "dh2": 1.2}

    def build(work: Path, first_tags: dict[str, str]) -> None:
        work.mkdir()
        make_src(work / "dh1.mp4", 1088, 1920, "0x404858", first_tags, dur=seg_dur["dh1"])
        make_src(work / "dh2.mp4", 1088, 1920, "0x485840", dict(SRC_TAGS, AIGC=aigc_value("test-0002")),
                 dur=seg_dur["dh2"])
        Image.new("RGB", (1080, 1920), (40, 60, 90)).save(work / "card.png")
        ff("-f", "lavfi", "-i", f"sine=f=330:d={seg_dur['card']}", str(work / "card.wav"))
        for n in (1, 2, 3):
            (work / f"s{n}.txt").write_text(f"自测字幕第{n}段", encoding="utf-8")
        (work / "segments.txt").write_text(
            "dh dh1.mp4 - s1.txt\ncard card.png card.wav s2.txt\ndh dh2.mp4 - s3.txt\n", encoding="utf-8")

    bash = "/bin/bash"
    work = tmp / "compose_ok"
    build(work, SRC_TAGS)
    out = tmp / "compose_ok.mp4"
    run = subprocess.run([bash, str(HERE / "compose.sh"), str(work), str(out)],
                         capture_output=True, text=True, check=False)
    if not check(run.returncode == 0 and out.is_file(), "compose.sh 正常出片", run.stderr.strip()[-400:]):
        return
    check("⚠️" in run.stdout, "compose.sh 各段元数据不同时打印 ⚠️")
    tags = meta.format_tags(out)
    check(tags.get("AIGC") == SRC_TAGS["AIGC"], "compose 成片带第一段的 AIGC")
    check(tags.get("description") == meta.AI_DESCRIPTION, "compose 成片带说明字段")
    check(not meta.compare(out, work / "dh1.mp4"), "compose 成片对第一段源片 compare 无问题")
    side = Path(str(out) + ".source-metadata.json")
    data = json.loads(side.read_text(encoding="utf-8")) if side.is_file() else {"segments": []}
    ids = [json.loads(s["tags"].get("AIGC", "{}")).get("ProduceID") for s in data["segments"]]
    check(ids == ["test-0001", "test-0002"], "sidecar 记下两段的 AIGC", json.dumps(ids))
    duration = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                     str(out)], capture_output=True, text=True, check=True).stdout)
    check(abs(duration - sum(seg_dur.values())) <= 0.1, "compose 成片时长 ≈ 各段之和(±0.1 s)",
          f"{duration:.3f} vs {sum(seg_dur.values()):.3f}")
    spec_path = work / "_tmp/badge.png.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    inks = ink_heights(out, spec["x"], spec["y"], spec["w"], spec["h"])
    check(all(v >= 54 for v in inks.values()), "compose 成片实测字形高 ≥ 54", json.dumps(inks))
    code, report = verify(out, "--badge-spec", str(spec_path), "--source", str(work / "dh1.mp4"))
    check(code == 0, "verify 正例·compose 成片", "exit 0" if code == 0 else report[-600:])

    bgm = tmp / "bgm.wav"
    ff("-f", "lavfi", "-i", "sine=f=220:d=4", str(bgm))
    args_txt = meta_cli("args", str(out), "0")
    peiyue = tmp / "compose_ok-配乐.mp4"
    if check(args_txt.returncode == 0, "成片再混音:aigc_meta.py args 接受我方自己写的说明字段", args_txt.stderr.strip()[-120:]):
        ff("-i", str(out), "-i", str(bgm), "-filter_complex",
           "[1:a]volume=0.09[m];[0:a][m]amix=inputs=2:normalize=0:duration=first[a]",
           "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
           *args_txt.stdout.splitlines(), str(peiyue))
        check(not meta.compare(peiyue, work / "dh1.mp4"), "成片再混音(按 SKILL.md 命令):对第一段源片 compare 无问题",
              "; ".join(meta.compare(peiyue, work / "dh1.mp4")))

    clips = tmp / "compose_clip_first"
    clips.mkdir()
    make_src(clips / "clip1.mp4", 1088, 1920, "0x405060", SRC_TAGS, dur=1.5)
    ff("-f", "lavfi", "-i", "sine=f=300:d=1.2", str(clips / "narr.wav"))
    Image.new("RGB", (1080, 1920), (40, 60, 90)).save(clips / "card.png")
    ff("-f", "lavfi", "-i", "sine=f=330:d=1", str(clips / "card.wav"))
    for n in (1, 2):
        (clips / f"s{n}.txt").write_text(f"生成视频段第{n}段", encoding="utf-8")
    (clips / "segments.txt").write_text("clip clip1.mp4 narr.wav s1.txt\ncard card.png card.wav s2.txt\n", encoding="utf-8")
    clip_out = tmp / "compose_clip_first.mp4"
    run4 = subprocess.run([bash, str(HERE / "compose.sh"), str(clips), str(clip_out)],
                          capture_output=True, text=True, check=False)
    ctags = meta.format_tags(clip_out) if clip_out.is_file() else {}
    check(run4.returncode == 0 and ctags.get("AIGC") == SRC_TAGS["AIGC"] and ctags.get("description") == meta.AI_DESCRIPTION
          and Path(str(clip_out) + ".source-metadata.json").is_file() and not meta.compare(clip_out, clips / "clip1.mp4"),
          "compose 第一段是 clip(生成视频段):保留它的 AIGC、写说明字段与 sidecar", f"exit={run4.returncode}")

    cards = tmp / "compose_cards"
    cards.mkdir()
    Image.new("RGB", (1080, 1920), (40, 60, 90)).save(cards / "card.png")
    ff("-f", "lavfi", "-i", "sine=f=330:d=1", str(cards / "card.wav"))
    (cards / "s1.txt").write_text("只有图卡", encoding="utf-8")
    (cards / "segments.txt").write_text("card card.png card.wav s1.txt\ncard card.png card.wav s1.txt\n", encoding="utf-8")
    cards_out = tmp / "compose_cards.mp4"
    Path(str(cards_out) + ".source-metadata.json").write_text("{}", encoding="utf-8")   # 上一次同名出片留下的
    run3 = subprocess.run([bash, str(HERE / "compose.sh"), str(cards), str(cards_out)],
                          capture_output=True, text=True, check=False)
    tags = meta.format_tags(cards_out) if cards_out.is_file() else {}
    check(run3.returncode == 0 and tags.get("description") == meta.AI_DESCRIPTION
          and all(";" not in tags.get(k, "") for k in meta.BRAND_KEYS)
          and not Path(str(cards_out) + ".source-metadata.json").exists(),
          "compose 全是图卡:出片、带说明字段、brand 无重复值、不写 sidecar", f"exit={run3.returncode}")

    work2 = tmp / "compose_clash"
    build(work2, dict(SRC_TAGS, description="SRCDESC"))
    out2 = tmp / "compose_clash.mp4"
    check(not out2.exists(), "冲突用例输出路径事先不存在")
    run2 = subprocess.run([bash, str(HERE / "compose.sh"), str(work2), str(out2)],
                          capture_output=True, text=True, check=False)
    check(run2.returncode != 0 and "aigc_meta.py" in run2.stderr and "unbound variable" not in run2.stderr
          and not out2.exists(), "compose 路径 description 冲突:非零退出、护栏报错、不出片",
          f"exit={run2.returncode} 出片={out2.exists()} stderr={run2.stderr.strip()[-160:]}")


def main() -> int:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            print(f"[FAIL] 缺 {tool}")
            return 1
    if not check(VERIFY.is_file(), "找到 verify_package.py", str(VERIFY.name)):
        return 1
    tmp = Path(tempfile.mkdtemp(prefix="aigc_selftest_"))
    try:
        samples: dict = {}
        for name, run in (("1. 角标", lambda: samples.update(section_badge(tmp))),
                          ("2. 元数据", lambda: section_metadata(tmp, samples)),
                          ("2b. compose.sh", lambda: section_compose(tmp)),
                          ("3. verify 反例", lambda: section_verify_negatives(tmp, samples))):
            try:
                run()
            except Exception as exc:  # noqa: BLE001 - 一节抛异常也要记成具名 FAIL,别的节照跑
                check(False, f"{name} 一节抛异常", f"{type(exc).__name__}: {exc}"[:300])
    finally:
        if os.environ.get("AIGC_SELFTEST_KEEP"):
            print(f"\n样本保留在 {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print(f"\n{len(FAILURES)} 项失败: " + "、".join(FAILURES))
        return 1
    print("\nAIGC_SELFTEST_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
