#!/usr/bin/env python3
"""compose.sh 的自测:全部样本在临时目录里现造,不读写任何项目文件。
用法: python3 compose_selftest.py        # 全过打印 COMPOSE_SELFTEST_PASS,否则非零退出

改 compose.sh 读 segments.txt 的循环之后跑这一条。覆盖:
  segments.txt 末行带不带换行符,最后一段都要进成片(成片时长 ≈ 各段之和)。
  末行不带换行符时 read 返回非零,循环条件少了 `|| [ -n "${kind:-}" ]` 就会静默丢掉最后一段、
  脚本照样 exit 0——所以这里量成片时长,不看退出码。
  字幕文件读不到(segments.txt 里文件名写错)时 compose.sh 要非零退出,不许画一条空字幕条照常出片。
  CRLF 换行的 segments.txt 照常出片,且每段字幕条上有字。回车符粘在行尾的字幕文件名上时,
  旧写法每段字幕都读不到却 exit 0——所以这里数 sub_N.png 里的不透明白像素,不只看退出码。
  CRLF 清单里夹的空行和 LF 清单里的空行一样跳过,不当成一段。
compose.sh 一律用 /bin/bash 跑(macOS 自带的是 3.2)。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from functools import partial
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
BASH = "/bin/bash"

# 期望值独立写死,不从 compose.sh 的产物里反推
SEG_DUR = {"dh": 1.2, "card": 2.0}
TOLERANCE = 0.1
SEGMENTS = "dh dh.mp4 - s1.txt\ncard card.png card.wav s2.txt"   # 末尾故意不带换行符,各用例自己补
CRLF_SEGMENTS = SEGMENTS.replace("\n", "\r\n") + "\r\n"                # 全部 CRLF
CRLF_BLANK_SEGMENTS = SEGMENTS.replace("\n", "\r\n\r\n") + "\r\n"      # 全部 CRLF,两段之间再夹一个空行
TYPO_SUB = "s2-typo.txt"                                           # 样本里不造这个文件
SUB_TEXT = "自测字幕第{n}段"
# 一条有字的字幕条里不透明纯白像素的下限。字是不透明纯白、底是半透明黑,空字幕条一个都没有(样本前提里实测)
SUB_MIN_WHITE = 1000

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


def duration(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True, check=True).stdout)


def white_pixels(png: Path) -> int:
    data = Image.open(png).convert("RGBA").tobytes()
    return sum(1 for k in range(0, len(data), 4) if data[k:k + 4] == b"\xff\xff\xff\xff")


def strip_png(tmp: Path, slug: str, text: str) -> Path:
    out = tmp / f"{slug}.png"
    subprocess.run([sys.executable, str(HERE / "render_text.py"), "strip", str(out), text], check=True)
    return out


def build(work: Path, segments: str) -> None:
    work.mkdir()
    ff("-f", "lavfi", "-i", f"color=c=0x404858:s=1088x1920:d={SEG_DUR['dh']}:r=25",
       "-f", "lavfi", "-i", f"sine=f=440:d={SEG_DUR['dh']}",
       "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", str(work / "dh.mp4"))
    Image.new("RGB", (1080, 1920), (40, 60, 90)).save(work / "card.png")
    ff("-f", "lavfi", "-i", f"sine=f=330:d={SEG_DUR['card']}", str(work / "card.wav"))
    for n in (1, 2):
        (work / f"s{n}.txt").write_text(SUB_TEXT.format(n=n), encoding="utf-8")
    (work / "segments.txt").write_bytes(segments.encode("utf-8"))


def compose(tmp: Path, slug: str, segments: str) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    work, out = tmp / slug, tmp / f"{slug}.mp4"
    build(work, segments)
    run = subprocess.run([BASH, str(HERE / "compose.sh"), str(work), str(out)],
                         capture_output=True, text=True, check=False)
    return run, work, out


def compose_case(tmp: Path, slug: str, name: str, segments: str) -> None:
    run, work, out = compose(tmp, slug, segments)
    if not check(run.returncode == 0 and out.is_file(), f"{name}:compose.sh 正常出片", run.stderr.strip()[-400:]):
        return
    want, got = sum(SEG_DUR.values()), duration(out)
    check(abs(got - want) <= TOLERANCE, f"{name}:成片时长 ≈ 各段之和(±{TOLERANCE} s)", f"{got:.3f} vs {want:.3f}")
    subs = [work / "_tmp" / f"sub_{n}.png" for n in (1, 2)]
    white = [white_pixels(png) if png.is_file() else -1 for png in subs]
    check(min(white) >= SUB_MIN_WHITE, f"{name}:每段字幕条上有字(不透明白像素 ≥ {SUB_MIN_WHITE})", f"各段 {white}")


def missing_subtitle_case(tmp: Path) -> None:
    name = "字幕文件名写错"
    run, work, out = compose(tmp, "typo_sub", SEGMENTS.replace("s2.txt", TYPO_SUB) + "\n")
    check(run.returncode != 0, f"{name}:compose.sh 非零退出", f"退出码 {run.returncode}")
    check(TYPO_SUB in run.stderr, f"{name}:报错里点出读不到的文件", run.stderr.strip()[-200:])
    check(not out.exists(), f"{name}:不出成片")
    png = work / "_tmp" / "sub_2.png"
    white = white_pixels(png) if png.is_file() else None
    check(white is None or white >= SUB_MIN_WHITE, f"{name}:没有画出空字幕条",
          "没有渲染这一段的字幕条" if white is None else f"不透明白像素 {white}")


def main() -> int:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            print(f"[FAIL] 缺 {tool}")
            return 1
    tmp = Path(tempfile.mkdtemp(prefix="compose_selftest_"))
    try:
        check(not SEGMENTS.endswith("\n"), "样本前提:基准清单末尾没有换行符")
        check(all("\r\n" in text and "\n" not in text.replace("\r\n", "")
                  for text in (CRLF_SEGMENTS, CRLF_BLANK_SEGMENTS)), "样本前提:CRLF 清单每个换行都是 \\r\\n")
        blank, texted = (white_pixels(strip_png(tmp, slug, text))
                         for slug, text in (("strip_blank", ""), ("strip_text", SUB_TEXT.format(n=1))))
        check(blank == 0 and texted >= SUB_MIN_WHITE, "样本前提:数白像素分得出空字幕条和有字的字幕条",
              f"空 {blank},有字 {texted}")
        cases = [(name, partial(compose_case, tmp, slug, name, segments))
                 for slug, name, segments in (("with_newline", "末行带换行符(对照)", SEGMENTS + "\n"),
                                              ("no_newline", "末行不带换行符", SEGMENTS),
                                              ("crlf", "CRLF 换行", CRLF_SEGMENTS),
                                              ("crlf_blank", "CRLF 换行且夹空行", CRLF_BLANK_SEGMENTS))]
        cases.append(("字幕文件名写错", partial(missing_subtitle_case, tmp)))
        for name, case in cases:
            try:
                case()
            except Exception as exc:  # noqa: BLE001 - 一个用例抛异常也要记成具名 FAIL,其余照跑
                check(False, f"{name} 抛异常", f"{type(exc).__name__}: {exc}"[:300])
    finally:
        if os.environ.get("COMPOSE_SELFTEST_KEEP"):
            print(f"\n样本保留在 {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print(f"\n{len(FAILURES)} 项失败: " + "、".join(FAILURES))
        return 1
    print("\nCOMPOSE_SELFTEST_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
