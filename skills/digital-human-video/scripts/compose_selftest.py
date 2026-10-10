#!/usr/bin/env python3
"""compose.sh 的自测:全部样本在临时目录里现造,不读写任何项目文件。
用法: python3 compose_selftest.py        # 全过打印 COMPOSE_SELFTEST_PASS,否则非零退出

改 compose.sh 读 segments.txt 的循环之后跑这一条。覆盖:
  segments.txt 末行带不带换行符,最后一段都要进成片(成片时长 ≈ 各段之和)。
  末行不带换行符时 read 返回非零,循环条件少了 `|| [ -n "${kind:-}" ]` 就会静默丢掉最后一段、
  脚本照样 exit 0——所以这里量成片时长,不看退出码。
compose.sh 一律用 /bin/bash 跑(macOS 自带的是 3.2)。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
BASH = "/bin/bash"

# 期望值独立写死,不从 compose.sh 的产物里反推
SEG_DUR = {"dh": 1.2, "card": 2.0}
TOLERANCE = 0.1
SEGMENTS = "dh dh.mp4 - s1.txt\ncard card.png card.wav s2.txt"   # 末尾故意不带换行符,各用例自己补

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


def build(work: Path, segments: str) -> None:
    work.mkdir()
    ff("-f", "lavfi", "-i", f"color=c=0x404858:s=1088x1920:d={SEG_DUR['dh']}:r=25",
       "-f", "lavfi", "-i", f"sine=f=440:d={SEG_DUR['dh']}",
       "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", str(work / "dh.mp4"))
    Image.new("RGB", (1080, 1920), (40, 60, 90)).save(work / "card.png")
    ff("-f", "lavfi", "-i", f"sine=f=330:d={SEG_DUR['card']}", str(work / "card.wav"))
    for n in (1, 2):
        (work / f"s{n}.txt").write_text(f"自测字幕第{n}段", encoding="utf-8")
    (work / "segments.txt").write_bytes(segments.encode("utf-8"))


def compose_case(tmp: Path, slug: str, name: str, segments: str) -> None:
    work, out = tmp / slug, tmp / f"{slug}.mp4"
    build(work, segments)
    run = subprocess.run([BASH, str(HERE / "compose.sh"), str(work), str(out)],
                         capture_output=True, text=True, check=False)
    if not check(run.returncode == 0 and out.is_file(), f"{name}:compose.sh 正常出片", run.stderr.strip()[-400:]):
        return
    want, got = sum(SEG_DUR.values()), duration(out)
    check(abs(got - want) <= TOLERANCE, f"{name}:成片时长 ≈ 各段之和(±{TOLERANCE} s)", f"{got:.3f} vs {want:.3f}")


def main() -> int:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            print(f"[FAIL] 缺 {tool}")
            return 1
    tmp = Path(tempfile.mkdtemp(prefix="compose_selftest_"))
    try:
        check(not SEGMENTS.endswith("\n"), "样本前提:基准清单末尾没有换行符")
        for slug, name, segments in (("with_newline", "末行带换行符(对照)", SEGMENTS + "\n"),
                                     ("no_newline", "末行不带换行符", SEGMENTS)):
            try:
                compose_case(tmp, slug, name, segments)
            except Exception as exc:  # noqa: BLE001 - 一个用例抛异常也要记成具名 FAIL,另一个照跑
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
