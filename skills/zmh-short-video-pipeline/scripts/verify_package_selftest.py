#!/usr/bin/env python3
"""verify_package.py「完整解码」判定的自测:全部样本在临时目录里现造,不读写任何项目文件。
用法: python3 verify_package_selftest.py     # 全过打印 VERIFY_PACKAGE_SELFTEST_PASS,否则非零退出

改 decode_check / analyze_loudness 之后跑这一条。覆盖:
  盒结构完好、后 40% 的音频包(或视频包)被清零的成片,--decode-check 与 --target-lufs 都要报 FAIL。
  这种文件不带 -xerror 时 ffmpeg 一路打印解码错误却照样退出 0,只看退出码就判成 PASS——
  样本前提里先确认造出来的确实是「报错但退出 0」的那一种,免得用例空转。
  两条完整解码命令都带 -xerror 与 -threads 1(写在 -i 之前)。默认多线程解码时同一处小损坏
  报不报错是竞态,行为测不稳,所以这一项按调用参数查。
只核「完整解码」这一项:其余检查项的结论拿完好样本不做完整解码时的结果当基线来比,
所以 verify_package.py 增减别的检查项不会让这里变红。
查不出的:解码器不报错的损坏。这条检查发现不了,仍靠人工观看与监听。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True   # 下面按路径载入 verify_package.py,不在脚本目录留 __pycache__

HERE = Path(__file__).resolve().parent
VERIFY = HERE / "verify_package.py"

# 样本规格与期望值独立写死,不从 verify_package.py 的产物里反推
WIDTH, HEIGHT, SECONDS = 270, 480, 5
ZERO_FROM = 0.6                     # 从 60% 处起把包清零,即后 40%
# 440 Hz 正弦样本约 -21.8 LUFS;容差放宽,这里核的是解码判定不是响度
LUFS_TARGET, LUFS_TOLERANCE = -22.0, 6.0
LUFS_FLAGS = ("--target-lufs", str(LUFS_TARGET), "--lufs-tolerance", str(LUFS_TOLERANCE))
DECODE, DECODE_LUFS, LOUDNESS = "完整解码", "完整解码 / 响度", "响度"   # verify_package.py 报告里的检查名

FAILURES: list[str] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)
    return ok


def sh(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)


def build_ok(path: Path) -> None:
    result = sh("ffmpeg", "-nostdin", "-v", "error", "-y",
                "-f", "lavfi", "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r=25:d={SECONDS}",
                "-f", "lavfi", "-i", f"sine=f=440:d={SECONDS}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", "-ac", "2", str(path))
    if result.returncode != 0:
        raise RuntimeError(f"造样本失败: {result.stderr.strip()}")


def zero_tail(src: Path, dst: Path, stream: str) -> tuple[int, int]:
    """把 stream 这一路从 ZERO_FROM 处起的包原地清零(盒结构、索引、另一路都不动)。返回(清零包数, 总包数)。"""
    probe = sh("ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", "packet=pos,size", "-of", "json", str(src))
    packets = [(int(p["pos"]), int(p["size"])) for p in json.loads(probe.stdout)["packets"]]
    tail = packets[int(len(packets) * ZERO_FROM):]
    shutil.copyfile(src, dst)
    with dst.open("r+b") as handle:
        for pos, size in tail:
            handle.seek(pos)
            handle.write(b"\0" * size)
    return len(tail), len(packets)


def verify(video: Path, *flags: str) -> tuple[int, dict[str, list[str]]]:
    """跑 verify_package.py,返回(退出码, {检查名: [级别, …]})。"""
    # --legacy-audit:样本没有角标和源片。不带它时「缺 --badge-spec／--source」本身就让完好样本退出 1,
    # 下面「损坏样本必须退出 1」就成了空断言。
    result = sh(sys.executable, str(VERIFY), "--video", str(video),
                "--expected-width", str(WIDTH), "--expected-height", str(HEIGHT), "--legacy-audit", *flags)
    levels: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        for level in ("PASS", "WARN", "FAIL"):
            if line.startswith(f"[{level}] ") and ": " in line:
                levels.setdefault(line[len(level) + 3:].split(": ", 1)[0], []).append(level)
    return result.returncode, levels


def failing(levels: dict[str, list[str]]) -> set[str]:
    return {name for name, seen in levels.items() if "FAIL" in seen}


def healthy_case(ok: Path, base: tuple[int, set[str]]) -> None:
    code, levels = verify(ok, "--decode-check")
    check(levels.get(DECODE) == ["PASS"] and (code, failing(levels)) == base,
          "完好样本 --decode-check:完整解码 PASS,退出码与失败项同基线",
          f"退出码 {code},完整解码 {levels.get(DECODE)},失败项 {sorted(failing(levels))}")
    code, levels = verify(ok, *LUFS_FLAGS)
    check(levels.get(DECODE) == ["PASS"] and levels.get(LOUDNESS) == ["PASS"] and (code, failing(levels)) == base,
          "完好样本 --target-lufs:完整解码 PASS、响度读得到,退出码与失败项同基线",
          f"退出码 {code},完整解码 {levels.get(DECODE)},响度 {levels.get(LOUDNESS)},失败项 {sorted(failing(levels))}")


def damaged_case(ok: Path, bad: Path, stream: str, name: str, base: tuple[int, set[str]]) -> None:
    zeroed, total = zero_tail(ok, bad, stream)
    plain = sh("ffmpeg", "-nostdin", "-v", "error", "-i", str(bad), "-f", "null", "-")
    check(zeroed > 0 and plain.returncode == 0 and bool(plain.stderr.strip()),
          f"样本前提:{name}不带 -xerror 时 ffmpeg 报错却退出 0",
          f"清零 {zeroed}/{total} 包,退出码 {plain.returncode},报错 {len(plain.stderr.splitlines())} 行")
    code, levels = verify(bad)
    check((code, failing(levels)) == base, f"样本前提:{name}不做完整解码时,其余检查看不出损坏(退出码与失败项同基线)",
          f"退出码 {code},失败项 {sorted(failing(levels))}")
    code, levels = verify(bad, "--decode-check")
    check(code == 1 and levels.get(DECODE) == ["FAIL"] and failing(levels) == base[1] | {DECODE},
          f"{name} --decode-check:完整解码 FAIL、退出码 1,只比基线多出这一项失败",
          f"退出码 {code},完整解码 {levels.get(DECODE)},失败项 {sorted(failing(levels))}")
    code, levels = verify(bad, *LUFS_FLAGS)
    check(code == 1 and levels.get(DECODE_LUFS) == ["FAIL"] and failing(levels) == base[1] | {DECODE_LUFS}
          and DECODE not in levels and LOUDNESS not in levels,
          f"{name} --target-lufs:完整解码 / 响度 FAIL、不给响度结论、退出码 1,只比基线多出这一项失败",
          f"退出码 {code},完整解码 / 响度 {levels.get(DECODE_LUFS)},完整解码 {levels.get(DECODE)},"
          f"响度 {levels.get(LOUDNESS)},失败项 {sorted(failing(levels))}")


def flags_case(ok: Path) -> None:
    spec = importlib.util.spec_from_file_location("verify_package_under_test", VERIFY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    seen: list[list[str]] = []
    real_run = subprocess.run

    def spy(command, *args, **kwargs):
        seen.append([str(part) for part in command])
        return real_run(command, *args, **kwargs)

    subprocess.run = spy
    try:
        module.decode_check(ok, module.Report())
        module.analyze_loudness(ok, LUFS_TARGET, LUFS_TOLERANCE, module.Report())
    finally:
        subprocess.run = real_run
    decodes = [cmd for cmd in seen if Path(cmd[0]).name == "ffmpeg"]

    def before_input(cmd: list[str], *option: str) -> bool:
        size = len(option)
        return "-i" in cmd and any(tuple(cmd[k:k + size]) == option for k in range(cmd.index("-i")))

    check(len(decodes) == 2 and all(before_input(cmd, "-xerror") for cmd in decodes),
          "两条完整解码命令都带 -xerror(写在 -i 之前)", f"ffmpeg 调用 {len(decodes)} 次")
    check(len(decodes) == 2 and all(before_input(cmd, "-threads", "1") for cmd in decodes),
          "两条完整解码命令都单线程解码(-threads 1 写在 -i 之前)", f"ffmpeg 调用 {len(decodes)} 次")


def main() -> int:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            print(f"[FAIL] 缺 {tool}")
            return 1
    tmp = Path(tempfile.mkdtemp(prefix="verify_package_selftest_"))
    try:
        ok = tmp / "ok.mp4"
        build_ok(ok)
        code, levels = verify(ok)
        base = (code, failing(levels))
        check(not {DECODE, DECODE_LUFS, LOUDNESS} & set(levels), "基线:完好样本不传解码参数时不做完整解码",
              f"退出码 {code},失败项 {sorted(base[1])}")
        cases = (("完好样本", lambda: healthy_case(ok, base)),
                 ("音频尾部清零", lambda: damaged_case(ok, tmp / "audio_tail.mp4", "a:0", "音频尾部清零的样本", base)),
                 ("视频尾部清零", lambda: damaged_case(ok, tmp / "video_tail.mp4", "v:0", "视频尾部清零的样本", base)),
                 ("解码参数", lambda: flags_case(ok)))
        for name, case in cases:
            try:
                case()
            except Exception as exc:  # noqa: BLE001 - 一个用例抛异常也要记成具名 FAIL,其余照跑
                check(False, f"{name} 抛异常", f"{type(exc).__name__}: {exc}"[:300])
    finally:
        if os.environ.get("VERIFY_PACKAGE_SELFTEST_KEEP"):
            print(f"\n样本保留在 {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print(f"\n{len(FAILURES)} 项失败: " + "、".join(FAILURES))
        return 1
    print("\nVERIFY_PACKAGE_SELFTEST_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
