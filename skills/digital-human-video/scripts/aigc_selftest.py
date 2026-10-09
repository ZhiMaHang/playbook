#!/usr/bin/env python3
"""AI 角标与成片元数据的自测:全部样本在临时目录里现造,不读写任何项目文件。
用法: python3 aigc_selftest.py        # 全过打印 AIGC_SELFTEST_PASS,否则非零退出

改 render_text.badge()、aigc_meta.py、compose.sh 的角标/元数据部分,或 verify_package.py 的
角标/元数据检查之后,都跑这一条。覆盖:
  1. 角标:1080x1920、1920x1080、1440x2560(抓写死字号)、720x1280 四种画幅,解码第 0 帧、
     1 s、2 s、末帧实测字形高;位置贴角;深浅两种底色过 verify_package 的像素判据;
  2. 元数据:源片键逐字保留(含非标准键)、说明字段、brand 三键无重复值、源片在第 0/1 路、
     description 冲突护栏、creation_time(普通源片、mdta 源片「X;X」与「A;B」都不让 mvhd 归零、真被改仍判变)、
     封装器键不同的源片;compose.sh 端到端(多段不同元数据、全是图卡、成片再混音、/bin/bash 3.2 下的冲突用例);
     变帧率、头部 -c copy 剪过的正例不误判截断;
  3. verify_package.py:正例 exit 0;不叠角标、角标晚出/只在开头/中段掉了/末尾掉了、淡角标、旧角标、
     几何 JSON 墨迹或字号不足、JSON 自报够但 PNG 实测不够、措辞缺一要素、终版被截断(含只少 2 帧)、画幅不符、
     缺说明字段、源键丢失/被改/brand 重复值、--source 传错/不存在/读不了、缺 --badge-spec、缺 --source、
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


def verify(video: Path, *extra: str) -> tuple[int, str]:
    result = subprocess.run([sys.executable, str(VERIFY), "--video", str(video), *extra],
                            capture_output=True, text=True, check=False)
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
            check(code == 0, f"verify 正例·{w}x{h} 底色 {bg}", "exit 0" if code == 0 else out[-600:])
    try:
        rt.badge(str(tmp / "bad_anchor.png"), width=1080, height=1920, anchor="TR")
        check(False, "anchor 只认小写 tr/tl/br/bl")
    except ValueError:
        check(True, "anchor 只认小写 tr/tl/br/bl")
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
                     "not(between(t,0.9,1.1))")
    expect_fail("中段 0.9–1.1 s 掉角标(只有 1 s 那一帧抽得到)", middle, "AI 角标像素", "--badge-spec", spec, "--source", str(src))
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
    tdata = tex_fin.read_bytes()
    cut3 = tmp / "neg_truncated_3pct.mp4"
    cut3.write_bytes(tdata[:len(tdata) * 97 // 100])
    expect_fail("终版被截断(只截掉末尾 3%，少 2 帧左右)", cut3, "AI 角标像素", "--badge-spec", spec, "--source", str(tex),
                detail="截断")
    trimmed = tmp / "fin_head_trimmed.mp4"
    ff("-ss", "1", "-i", str(fin), "-c", "copy", *meta.ffmpeg_args(fin, 0), str(trimmed))
    code, out = verify(trimmed, "--badge-spec", spec, "--source", str(src))
    check(code == 0, "verify 正例·头部 -c copy 剪掉 1 s(编辑表藏了预滚帧，不误判截断)", "exit 0" if code == 0 else out[-400:])
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
    data = fin.read_bytes()
    for label, cut in (("截掉后 60%", len(data) * 4 // 10), ("只剩 moov", data.find(b"mdat") + 4)):
        cut_file = tmp / f"neg_truncated_{cut}.mp4"
        cut_file.write_bytes(data[:cut])
        expect_fail(f"终版被截断({label})", cut_file, "AI 角标像素", "--badge-spec", spec, "--source", str(src),
                    detail="截断")
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
    brand = tmp / "src_brand_mp42.mp4"
    ff("-f", "lavfi", "-i", "color=c=0x202020:s=320x240:d=1:r=25", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "-brand", "mp42", "-metadata", "title=brand", str(brand))
    brand_out = tmp / "meta_brand_mp42.mp4"
    ff("-i", str(brand), "-c", "copy", *meta.ffmpeg_args(brand, 0), str(brand_out))
    src_b, out_b = meta.format_tags(brand).get("major_brand"), meta.format_tags(brand_out).get("major_brand")
    check(src_b != out_b and not meta.compare(brand_out, brand), "封装器键(brand)源片与终版不同时不误报",
          f"{src_b} → {out_b}")
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

    cards = tmp / "compose_cards"
    cards.mkdir()
    Image.new("RGB", (1080, 1920), (40, 60, 90)).save(cards / "card.png")
    ff("-f", "lavfi", "-i", "sine=f=330:d=1", str(cards / "card.wav"))
    (cards / "s1.txt").write_text("只有图卡", encoding="utf-8")
    (cards / "segments.txt").write_text("card card.png card.wav s1.txt\ncard card.png card.wav s1.txt\n", encoding="utf-8")
    cards_out = tmp / "compose_cards.mp4"
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
