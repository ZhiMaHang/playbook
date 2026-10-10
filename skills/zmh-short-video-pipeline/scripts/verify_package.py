#!/usr/bin/env python3
"""Verify a ZhiMaHang short-video delivery package without modifying source assets."""

from __future__ import annotations

import argparse
import bisect
import importlib.util
import json
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

# AI 标识的唯一真源(措辞常量、两要素、字高下限、元数据核对)在同插件 digital-human-video 里
AIGC_META_PATH = Path(__file__).resolve().parents[2] / "digital-human-video/scripts/aigc_meta.py"

# 角标像素判据(合成样本正反例校准,依据见 digital-human-video/scripts/aigc_selftest.py):
# ① 字形处亮度 ≥ 170 的像素占比 ≥ 95%(白字 alpha 220,叠上去理论亮度 ≥ 220;编码会把个别像素压低,暗底、高 crf、
#    小字号退回不腐蚀掩码含笔画边缘时更明显,所以按占比判)。局限:压在逐像素黑白跳变的高频纹理上时,画幅越小、
#    压得越狠,占比越可能跌破 95% 而误判 FAIL——只往安全方向错;
# ② 字形与底框的亮度中位数差 ≥ 40。只有 ① 不够:纯白画面上没有角标也能过 ①。
GLYPH_ALPHA_MIN = 200   # 角标 PNG 里 alpha ≥ 200 的像素是字形,腐蚀 1 px 后作字形掩码
BOX_ALPHA_MAX = 100     # 0 < alpha ≤ 100 是底框
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")   # 环境里设了 AV_LOG_FORCE_COLOR 时 ffmpeg 日志带颜色码
SHOWINFO_PTS = re.compile(r"\] n:\s*\d+\s+pts:\s*\S+\s+pts_time:\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")
GLYPH_BANDS, GLYPH_BAND_MIN_SHARE = 5, 0.10   # 字形横向五等分:腐蚀后任一段留不到一成像素就改用不腐蚀的掩码
GLYPH_INK_ALPHA = 158   # 量 PNG 实际字高:底框 95、白字 220,alpha ≥ 158 约等于字形覆盖过半,与渲染时「L 模式 >128」口径一致(实测四画幅与 JSON 相同)
GLYPH_LUMA_MIN = 170
GLYPH_RATIO_MIN = 0.95
GLYPH_BOX_CONTRAST_MIN = 40


@dataclass
class Check:
    level: str
    name: str
    detail: str


class Report:
    def __init__(self) -> None:
        self.checks: list[Check] = []
        self.media: dict[str, Any] = {}
        self.frames: list[str] = []
        self.include_cover_manual_check = False

    def add(self, level: str, name: str, detail: str) -> None:
        self.checks.append(Check(level, name, detail))

    def passed(self, name: str, detail: str) -> None:
        self.add("PASS", name, detail)

    def warn(self, name: str, detail: str) -> None:
        self.add("WARN", name, detail)

    def fail(self, name: str, detail: str) -> None:
        self.add("FAIL", name, detail)

    @property
    def has_failures(self) -> bool:
        return any(check.level == "FAIL" for check in self.checks)

    def payload(self) -> dict[str, Any]:
        manual_checks = [
            "逐字核对首句、末句、关键数字与锁定稿",
            "查看每个剪点/分镜的字幕、角标、遮挡与画面顺序",
            "首帧角标在各平台手机播放界面下不被遮挡（新角标版式首次发布前用手机截图核过；之后换版式或平台改版时重核）",
            "确认第一句实际口播从开头即有对应字幕，且没有混入未口播的封面标题、文档标题或栏目名",
            "确认本期截图、录屏或示意图实际入片并对应讲解，手机画幅可读且字幕/角标不遮挡重点；本期明确不用素材时记录用户覆盖指令",
            "确认特效字幕保留完整口播，关键词高亮与轻动效跟随发声，不添加未口播结论",
            "监听口型同步、人声清晰度及无禁用音乐残留，辅助素材插入前后人声连续且录屏原声未意外叠入",
            "新期默认无 BGM；本期另加新音乐时确认目标平台使用权和混音听感，并核对肖像等素材权利",
        ]
        if self.include_cover_manual_check:
            manual_checks[4:4] = [
                "将三版平台封面与用户指定或当前已验收的同规格基准并排核对标题区黑底/遮罩及字体排版样式",
                "缩略查看 B站 4:3 封面，确认左文右像、近黑标题区、金色标签、金/白两行、描边与标题安全区符合当前统一基准",
                "缩略查看 YouTube Shorts 9:16 封面，确认标题避开右侧操作列和底部标题/频道信息区",
                "后续实际发布时，发布页仍需单独勾选 AI 生成声明",
            ]
        return {
            "ok": not self.has_failures,
            "checks": [asdict(check) for check in self.checks],
            "media": self.media,
            "frames": self.frames,
            "manual_checks": manual_checks,
        }


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    # stdin 一律断开:ffmpeg 会把 stdin 当键盘指令读(q 提前收工却照样退出 0),批量 `while read` 时还会吃掉循环的输入
    return subprocess.run(command, text=True, capture_output=True, check=False, stdin=subprocess.DEVNULL)


def require_binary(name: str, report: Report) -> bool:
    path = shutil.which(name)
    if path:
        report.passed(f"工具 {name}", path)
        return True
    report.fail(f"工具 {name}", "未在 PATH 中找到")
    return False


def probe(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    result = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ]
    )
    if result.returncode != 0:
        return None, result.stderr.strip() or f"ffprobe 退出码 {result.returncode}"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"无法解析 ffprobe JSON: {exc}"


def as_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def duration_of(data: dict[str, Any]) -> float | None:
    duration = as_float(data.get("format", {}).get("duration"))
    if duration is not None:
        return duration
    candidates = [as_float(stream.get("duration")) for stream in data.get("streams", [])]
    valid = [value for value in candidates if value is not None]
    return max(valid) if valid else None


def frame_rate_of(stream: dict[str, Any]) -> float | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = stream.get(key)
        if not value or value == "0/0":
            continue
        try:
            rate = float(Fraction(value))
        except (ValueError, ZeroDivisionError):
            continue
        if math.isfinite(rate) and rate > 0:
            return rate
    return None


def first_stream(data: dict[str, Any], codec_type: str) -> dict[str, Any] | None:
    return next(
        (stream for stream in data.get("streams", []) if stream.get("codec_type") == codec_type),
        None,
    )


def check_source_file(path: Path, label: str, report: Report) -> bool:
    if not path.exists():
        report.fail(label, f"文件不存在: {path}")
        return False
    if not path.is_file():
        report.fail(label, f"不是普通文件: {path}")
        return False
    size = path.stat().st_size
    if size <= 0:
        report.fail(label, f"空文件: {path}")
        return False
    report.passed(label, f"{path} ({size} bytes)")
    return True


def check_video(args: argparse.Namespace, report: Report) -> float | None:
    path = args.video.resolve()
    if not check_source_file(path, "终版视频", report):
        return None

    data, error = probe(path)
    if error or data is None:
        report.fail("视频 ffprobe", error or "未知错误")
        return None

    video = first_stream(data, "video")
    audio = first_stream(data, "audio")
    duration = duration_of(data)
    report.media["video"] = {
        "path": str(path),
        "duration": duration,
        "video_codec": video.get("codec_name") if video else None,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "fps": frame_rate_of(video) if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "sample_rate": audio.get("sample_rate") if audio else None,
        "channels": audio.get("channels") if audio else None,
    }

    if video is None:
        report.fail("视频流", "没有检测到视频流")
    else:
        report.passed("视频流", f"codec={video.get('codec_name')}")
        width, height = video.get("width"), video.get("height")
        if width == args.expected_width and height == args.expected_height:
            report.passed("成片画幅", f"{width}x{height}")
        else:
            report.fail(
                "成片画幅",
                f"实测 {width}x{height}，期望 {args.expected_width}x{args.expected_height}",
            )
        if video.get("codec_name") == args.video_codec:
            report.passed("视频编码", args.video_codec)
        else:
            report.fail(
                "视频编码",
                f"实测 {video.get('codec_name')}，期望 {args.video_codec}",
            )
        fps = frame_rate_of(video)
        if fps:
            report.passed("帧率", f"{fps:.3f} fps")
        else:
            report.warn("帧率", "ffprobe 未得到有效帧率")

    if audio is None:
        report.fail("音频流", "没有检测到音频流")
    else:
        report.passed(
            "音频流",
            f"codec={audio.get('codec_name')} sample_rate={audio.get('sample_rate')} channels={audio.get('channels')}",
        )
        if audio.get("codec_name") == args.audio_codec:
            report.passed("音频编码", args.audio_codec)
        else:
            report.fail(
                "音频编码",
                f"实测 {audio.get('codec_name')}，期望 {args.audio_codec}",
            )

    if duration is None or duration <= 0:
        report.fail("成片时长", "无法得到正数时长")
    else:
        report.passed("成片时长", f"{duration:.3f} 秒")
        if args.max_duration is not None:
            if duration <= args.max_duration + args.duration_tolerance:
                report.passed(
                    "时长上限",
                    f"{duration:.3f} <= {args.max_duration:.3f} + {args.duration_tolerance:.3f} 秒容差",
                )
            else:
                report.fail(
                    "时长上限",
                    f"{duration:.3f} > {args.max_duration:.3f} + {args.duration_tolerance:.3f} 秒容差",
                )
    return duration


def check_cover(path: Path | None, label: str, expected: tuple[int, int], report: Report) -> None:
    if path is None:
        report.warn(label, "未提供，未检查")
        return
    resolved = path.resolve()
    if not check_source_file(resolved, label, report):
        return
    data, error = probe(resolved)
    if error or data is None:
        report.fail(f"{label} ffprobe", error or "未知错误")
        return
    stream = first_stream(data, "video")
    if stream is None:
        report.fail(f"{label} 尺寸", "没有检测到图像流")
        return
    width, height = stream.get("width"), stream.get("height")
    report.media[label] = {
        "path": str(resolved),
        "width": width,
        "height": height,
        "codec": stream.get("codec_name"),
    }
    if (width, height) == expected:
        report.passed(f"{label} 尺寸", f"{width}x{height}")
    else:
        report.fail(f"{label} 尺寸", f"实测 {width}x{height}，期望 {expected[0]}x{expected[1]}")


def extract_frames(video: Path, duration: float | None, directory: Path, report: Report) -> None:
    if duration is None or duration <= 0:
        report.warn("抽帧", "没有有效时长，跳过")
        return
    directory.mkdir(parents=True, exist_ok=True)
    candidates = [min(0.20, duration / 4), duration / 2, max(duration - 0.20, 0)]
    times: list[float] = []
    for value in candidates:
        if not any(abs(value - existing) < 0.01 for existing in times):
            times.append(value)
    for index, value in enumerate(times, start=1):
        output = directory / f"frame-{index:02d}-{value:.3f}s.png"
        result = run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                f"{value:.6f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                "scale=540:-2",
                str(output),
            ]
        )
        if result.returncode == 0 and output.exists() and output.stat().st_size > 0:
            report.frames.append(str(output.resolve()))
            report.passed("抽帧", f"{value:.3f}s -> {output}")
        else:
            report.fail("抽帧", result.stderr.strip() or f"{value:.3f}s 抽帧失败")


# 完整解码的两条命令都带这两个参数（写在 -i 之前）：
# -xerror：解码器一报错就非零退出。不带时 ffmpeg 一路打印解码错误却照样退出 0，
#   要到约三分之二的包都解不出才变非零，只看退出码会把坏了一截的文件判成 PASS。
# -threads 1：默认多线程解码时，同一处小损坏有时报错、有时被遮过去退出 0，同一个文件判定时过时不过。
# 解码器不报错的损坏这里查不出，仍靠人工观看与监听。（verify_package_selftest.py 守着这两个参数）
STRICT_DECODE = ["-xerror", "-threads", "1"]


def analyze_loudness(
    video: Path, target: float | None, tolerance: float, report: Report
) -> None:
    if target is None:
        return
    result = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            *STRICT_DECODE,
            "-i",
            str(video),
            "-filter_complex",
            # framelog=verbose：逐帧读数不进 stderr，失败时报出来的是解码错误而不是几百行读数；Summary 照常输出
            "ebur128=peak=true:framelog=verbose",
            "-f",
            "null",
            "-",
        ]
    )
    if result.returncode != 0:
        report.fail("完整解码 / 响度", result.stderr.strip() or f"ffmpeg 退出码 {result.returncode}")
        return
    report.passed("完整解码", "ffmpeg 响度分析期间未返回解码错误")
    matches = re.findall(r"\bI:\s*(-?\d+(?:\.\d+)?)\s+LUFS", result.stderr)
    if not matches:
        report.warn("响度", "完整解码成功，但未解析到 integrated LUFS")
        return
    measured = float(matches[-1])
    report.media.setdefault("video", {})["integrated_lufs"] = measured
    if abs(measured - target) <= tolerance:
        report.passed("响度", f"{measured:.1f} LUFS，目标 {target:.1f} ± {tolerance:.1f}")
    else:
        report.fail("响度", f"{measured:.1f} LUFS，目标 {target:.1f} ± {tolerance:.1f}")


def decode_check(video: Path, report: Report) -> None:
    result = run(["ffmpeg", "-v", "error", *STRICT_DECODE, "-i", str(video), "-f", "null", "-"])
    if result.returncode == 0:
        report.passed("完整解码", "ffmpeg 未返回解码错误")
    else:
        report.fail("完整解码", result.stderr.strip() or f"ffmpeg 退出码 {result.returncode}")


def load_aigc_meta(report: Report) -> Any:
    if not AIGC_META_PATH.is_file():
        report.fail("aigc_meta.py", f"未找到 {AIGC_META_PATH}，AI 标识无法机检")
        return None
    try:
        spec = importlib.util.spec_from_file_location("aigc_meta", AIGC_META_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001 - 任何加载失败都要报 FAIL，不许跳过
        report.fail("aigc_meta.py", f"加载失败: {exc}")
        return None
    return module


def png_alpha(path: Path, width: int, height: int) -> bytes | None:
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0 or len(result.stdout) != width * height * 4:
        return None
    return result.stdout[3::4]


def badge_masks(alpha: bytes, width: int, height: int) -> tuple[list[int], list[int]]:
    """字形掩码 = alpha ≥ GLYPH_ALPHA_MIN 再腐蚀 1 px（3×3 邻域全是字形才留下）；底框掩码 = 0 < alpha ≤ BOX_ALPHA_MAX。
    字号小时腐蚀会把某几个字整个吃掉（STHeiti 下短边 ≤ 700 一带，换回退字体时范围略有不同），只剩的掩码偏在少数字上，
    盖住别的字也看不出；所以字形横向五等分，腐蚀后任一段剩不到一成就整体退回不腐蚀的掩码。STHeiti 下短边 720 及以上不触发。"""
    raw = [value >= GLYPH_ALPHA_MIN for value in alpha]
    glyph = [
        y * width + x
        for y in range(1, height - 1)
        for x in range(1, width - 1)
        if all(raw[(y + dy) * width + x + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    ]
    full = [index for index, keep in enumerate(raw) if keep]
    if full:
        left = min(i % width for i in full)
        span = max(i % width for i in full) + 1 - left
        kept, total = [0] * GLYPH_BANDS, [0] * GLYPH_BANDS
        for i in full:
            total[min(GLYPH_BANDS - 1, (i % width - left) * GLYPH_BANDS // span)] += 1
        for i in glyph:
            kept[min(GLYPH_BANDS - 1, (i % width - left) * GLYPH_BANDS // span)] += 1
        if any(t and k < GLYPH_BAND_MIN_SHARE * t for k, t in zip(kept, total)):
            glyph = full
    box = [index for index, value in enumerate(alpha) if 0 < value <= BOX_ALPHA_MAX]
    return glyph, box


def median(values: list[int]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return float(ordered[middle]) if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def png_ink_height(alpha: bytes, width: int) -> int:
    rows = [r for r in range(len(alpha) // width) if max(alpha[r * width:(r + 1) * width]) >= GLYPH_INK_ALPHA]
    return rows[-1] - rows[0] + 1 if rows else 0


def badge_frames(video: Path, x: int, y: int, w: int, h: int) -> tuple[list[tuple[float, bytes]], list[float], int, int]:
    """只解角标区域，一遍解完：灰度帧写临时文件，showinfo 记下每帧时间戳。
    返回（抽样帧 [(时间, 灰度帧)]，全部帧的时间戳，写出的帧数，ffmpeg 退出码）。-xerror：解码器一报错就非零退出，
    否则盒结构完整、内容坏了一截的文件只核了能解的前半段也会「全程 PASS」。-threads 1：多线程解码时同一处损坏
    有时报错、有时只是被悄悄补上（退出码 0），同一个文件的判定会随运行变；单线程下每次结果相同。
    解码器自己没报错的损坏（有些坏了的码流照样解得出画面）这里查不出。抽样：第 0 帧、以它的时间为起点此后每个整秒时刻
    正在屏上的那一帧（时间戳 ≤ 该时刻的最后一帧；起点为 0 时相当于 fps=1:round=up；变帧率里一帧停留跨过整秒也抽得到）、末帧。
    passthrough：不补帧丢帧，帧和时间戳一一对应。"""
    size = w * h
    with tempfile.TemporaryDirectory() as tmp:
        raw, log = Path(tmp) / "badge.gray", Path(tmp) / "showinfo.log"
        with log.open("wb") as err:
            decode = subprocess.run(
                [
                    "ffmpeg", "-v", "info", "-nostats", "-xerror", "-threads", "1", "-i", str(video), "-map", "0:v:0", "-fps_mode", "passthrough",
                    # 先转灰度再裁:yuv420p 上 crop 会把奇数宽高、坐标按色度对齐取偶,逐帧步长就错位了
                    "-vf", f"format=gray,crop={w}:{h}:{x}:{y},showinfo", "-f", "rawvideo", "-pix_fmt", "gray",
                    "-y", str(raw),
                ],
                stdin=subprocess.DEVNULL,   # 理由同 run():读到 q 会只核前半段却照样「PASS」
                stdout=subprocess.DEVNULL,
                stderr=err,
                check=False,
            )
        times = [float(t) for t in SHOWINFO_PTS.findall(ANSI_ESCAPE.sub("", log.read_text("utf-8", "replace")))]
        count = raw.stat().st_size // size if raw.exists() else 0
        if not count or len(times) != count:   # 交给调用方报具名 FAIL,别在下面按下标取时间戳时崩掉
            return [], times, count, decode.returncode
        picks = [0] if count else []
        second = 1
        while count and times[0] + second <= times[count - 1]:
            index = bisect.bisect_right(times, times[0] + second + 1e-6, 0, count) - 1
            if index != picks[-1]:
                picks.append(index)
            second += 1
        if count and picks[-1] != count - 1:
            picks.append(count - 1)
        frames: list[tuple[float, bytes]] = []
        if picks:
            with raw.open("rb") as fh:
                for index in picks:
                    fh.seek(index * size)
                    frames.append((times[index] if index < len(times) else float("nan"), fh.read(size)))
    return frames, times, count, decode.returncode


BMFF_FIRST_BOXES = {b"ftyp", b"styp", b"moov", b"mdat", b"free", b"skip", b"wide", b"pnot"}


def container_problem(path: Path) -> tuple[bool, str | None]:
    """逐个走 MP4/MOV 的顶层 box：哪个 box 声明的长度超出文件，就是被截断（字节级，与帧时间无关）。
    返回（能否核，问题）：不是 MP4/MOV 盒结构、或某个 box 长度写 0（到文件尾，声明不了长度）时返回 (False, None)；
    结构完整时返回 (True, None)。"""
    size = path.stat().st_size
    with path.open("rb") as fh:
        offset = 0
        while offset < size:
            fh.seek(offset)
            head = fh.read(16)
            if len(head) < 8:
                return True, f"文件末尾只剩 {len(head)} 字节的残缺 box 头"
            box_size, box_type = struct.unpack(">I4s", head[:8])
            if offset == 0 and box_type not in BMFF_FIRST_BOXES:
                return False, None
            if box_size == 1:   # 64 位长度
                if len(head) < 16:
                    return True, "64 位 box 长度不全"
                box_size = struct.unpack(">Q", head[8:16])[0]
            elif box_size == 0:   # 「一直到文件尾」：声明不了长度，截断无从核对
                return False, None
            name = box_type.decode("latin-1")
            if box_size < 8:
                return True, f"{name} box 长度 {box_size} 不合法"
            if offset + box_size > size:
                return True, f"{name} box 声明 {box_size} 字节，文件从它起只剩 {size - offset} 字节"
            offset += box_size
    return True, None


def load_badge_spec(path: Path) -> dict[str, Any]:
    """读角标几何 JSON 并校验字段类型；不合格抛 ValueError（由调用方报「AI 角标几何」FAIL，别在后面用到时崩）。"""
    spec = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError("顶层应是对象")
    for key in ("x", "y", "w", "h", "font_size", "ink_height_px"):
        value = spec.get(key)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{key} 应是整数，收到 {value!r}")
    canvas = spec.get("canvas")
    if not (isinstance(canvas, list) and len(canvas) == 2
            and all(isinstance(v, int) and not isinstance(v, bool) for v in canvas)):
        raise ValueError(f"canvas 应是两个整数 [宽, 高]，收到 {canvas!r}")
    if not isinstance(spec.get("text"), str):
        raise ValueError(f"text 应是字符串，收到 {spec.get('text')!r}")
    png_name = spec.get("png")
    if png_name is not None and not (isinstance(png_name, str) and png_name and Path(png_name).name == png_name):
        raise ValueError(f"png 字段应是与几何 JSON 同目录的文件名，收到 {png_name!r}")
    return spec


def check_badge(args: argparse.Namespace, report: Report, meta: Any, strict: bool) -> None:
    fail = report.fail if strict else report.warn
    if args.badge_spec is None:
        fail("AI 角标未机检", "缺 --badge-spec <角标 PNG 旁的 .json>；从旧期复制的脚本过不了这一项")
        return
    spec_path = args.badge_spec.resolve()
    try:
        spec = load_badge_spec(spec_path)
    except (OSError, ValueError) as exc:
        fail("AI 角标几何", f"读不了 {spec_path}: {exc}")
        return
    x, y, w, h, canvas, png_name = spec["x"], spec["y"], spec["w"], spec["h"], spec["canvas"], spec.get("png")
    video = report.media.get("video", {})
    width, height = video.get("width"), video.get("height")
    if canvas != [width, height]:
        fail("AI 角标画幅", f"角标按 {canvas[0]}x{canvas[1]} 计算，终版实际 {width}x{height}")
        return
    report.passed("AI 角标画幅", f"{width}x{height}")

    need = meta.min_glyph_px(width, height)
    ink, font_size = spec.get("ink_height_px"), spec.get("font_size")
    if not (isinstance(ink, int) and ink >= need):
        fail("AI 角标字高", f"几何 JSON 记的墨迹 {ink}px，低于下限 {need}px（最短边 5%）")
    elif not (isinstance(font_size, int) and font_size >= need):
        fail("AI 角标字高", f"几何 JSON 记的字号 {font_size}，低于下限 {need}px（最短边 5%）")
    else:
        report.passed("AI 角标字高", f"几何 JSON：墨迹 {ink}px、字号 {font_size}，下限 {need}px（最短边 5%）")

    text = spec.get("text", "")
    if text in meta.RETIRED_BADGE_TEXTS:
        fail("AI 角标措辞", f"「{text}」已退役")
    elif not meta.has_two_elements(text):
        fail("AI 角标措辞", f"「{text}」缺两要素（AI/人工智能 + 生成/合成）")
    else:
        report.passed("AI 角标措辞", f"「{text}」")
        if text != meta.BADGE_TEXT:
            report.warn("AI 角标措辞", f"与措辞常量「{meta.BADGE_TEXT}」不一致")

    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
        fail("AI 角标位置", f"框 ({x},{y}) {w}x{h} 超出 {width}x{height}")
        return
    report.passed("AI 角标位置", f"框 ({x},{y}) {w}x{h}，锚点 {spec.get('anchor')}")

    png = spec_path.with_name(png_name) if png_name else spec_path.with_suffix("")
    alpha = png_alpha(png, w, h)
    if alpha is None:
        fail("AI 角标像素", f"读不了角标 PNG {png}，或尺寸不是 {w}x{h}")
        return
    measured = png_ink_height(alpha, w)
    if measured < need:
        fail("AI 角标字高", f"角标 PNG 实测字高 {measured}px，低于下限 {need}px（不只信 JSON 自报）")
    else:
        report.passed("AI 角标字高", f"角标 PNG 实测字高 {measured}px ≥ {need}px")
    glyph, box = badge_masks(alpha, w, h)
    if not glyph or not box:
        fail("AI 角标像素", "角标 PNG 里取不出字形或底框掩码")
        return
    checkable, problem = container_problem(args.video.resolve())
    if problem:
        fail("AI 角标像素", f"终版被截断或结构损坏（{problem}），末帧不是真末帧，角标全程无从核起")
        return
    if not checkable:
        report.warn("AI 角标像素", "终版不是 MP4/MOV 盒结构或末尾 box 未声明长度，截断无从核对")
    picked, times, decoded, decode_rc = badge_frames(args.video.resolve(), x, y, w, h)
    if decoded == 0 or decoded != len(times):
        fail("AI 角标像素", f"终版解出 {decoded} 帧、时间戳 {len(times)} 个（为零或对不上：文件损坏，或临时目录写不下）")
        return
    if decode_rc != 0:
        fail("AI 角标像素", f"终版解码出错（ffmpeg 退出码 {decode_rc}，只解出前 {decoded} 帧）：内容可能损坏，后面的画面核不了")
        return
    bad: list[str] = []
    checked = 0
    for moment, frame in picked:
        checked += 1
        glyph_values = [frame[i] for i in glyph]
        ratio = sum(value >= GLYPH_LUMA_MIN for value in glyph_values) / len(glyph_values)
        contrast = median(glyph_values) - median([frame[i] for i in box])
        if ratio < GLYPH_RATIO_MIN or contrast < GLYPH_BOX_CONTRAST_MIN:
            bad.append(f"{moment:.2f}s 那一帧亮字占比 {ratio:.1%}、字形−底框 {contrast:.0f}")
    if bad:
        fail("AI 角标像素", f"{len(bad)}/{checked} 帧不像有角标：" + "；".join(bad[:5]))
    else:
        report.passed("AI 角标像素", f"{checked} 帧（第 0 帧、此后每个整秒时刻屏上那一帧、末帧）都检出角标")


def check_source_metadata(video: Path, source: Path, meta: Any, report: Report, fail: Any) -> None:
    if not source.is_file():
        fail("源元数据保留", f"源片不存在: {source}")
        return
    try:
        problems = meta.compare(video, source)  # 含「传了终版自己／终版副本」两道拦截
    except RuntimeError as exc:
        fail("源元数据保留", str(exc))
        return
    for problem in problems:
        fail("源元数据保留", problem)
    if not problems:
        report.passed("源元数据保留", "源片非封装器键逐字保留（creation_time 原串不是带时区的绝对时间时按时刻核），brand 三键无重复值")


def check_metadata(args: argparse.Namespace, report: Report, meta: Any, strict: bool) -> None:
    fail = report.fail if strict else report.warn
    video = args.video.resolve()
    try:
        tags = meta.format_tags(video)
    except RuntimeError as exc:
        fail("说明字段", str(exc))
        return
    description = tags.get("description", "")
    if meta.has_two_elements(description):
        report.passed("说明字段", f"description「{description}」")
    else:
        fail("说明字段", "终版 description 缺失或不含两要素（AI/人工智能 + 生成/合成）")
    if args.source is None:
        fail("源元数据保留未核", "缺 --source <源片.mp4>；从旧期复制的脚本过不了这一项")
    else:
        check_source_metadata(video, args.source.resolve(), meta, report, fail)
    for flag in meta.privacy_flags(video):
        report.warn("元数据隐私提示", f"{flag}（按原样保留，只提示、不删）")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True, help="终版 MP4")
    parser.add_argument("--cover-vertical", type=Path, help="1080x1440 竖版封面")
    parser.add_argument("--cover-bilibili", type=Path, help="1440x1080 B站横版封面")
    parser.add_argument("--cover-youtube-shorts", type=Path, help="1080x1920 YouTube Shorts 封面")
    parser.add_argument("--expected-width", type=int, default=1080)
    parser.add_argument("--expected-height", type=int, default=1920)
    parser.add_argument("--video-codec", default="h264")
    parser.add_argument("--audio-codec", default="aac")
    parser.add_argument("--max-duration", type=float)
    parser.add_argument("--duration-tolerance", type=float, default=0.05)
    parser.add_argument("--target-lufs", type=float)
    parser.add_argument("--lufs-tolerance", type=float, default=2.0)
    parser.add_argument("--frames-dir", type=Path, help="抽取首/中/末帧到此目录")
    parser.add_argument("--decode-check", action="store_true", help="完整解码成空输出，解码器报错即 FAIL（不报错的损坏查不出）")
    parser.add_argument("--json-out", type=Path, help="另存机器可读报告")
    parser.add_argument("--badge-spec", type=Path, help="render_text.py badge 写出的角标几何 JSON（<角标.png>.json）")
    parser.add_argument("--source", type=Path, help="源片 MP4，核对终版是否保留其元数据")
    parser.add_argument(
        "--legacy-audit",
        action="store_true",
        help="只读审计已发布旧片：缺角标几何/源片、角标与元数据不达新基线都降为 WARN",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = Report()
    report.include_cover_manual_check = any(
        (args.cover_vertical, args.cover_bilibili, args.cover_youtube_shorts)
    )
    have_ffprobe = require_binary("ffprobe", report)
    need_ffmpeg = bool(
        args.frames_dir or args.decode_check or args.target_lufs is not None or args.badge_spec
    )
    have_ffmpeg = require_binary("ffmpeg", report) if need_ffmpeg else True

    duration: float | None = None
    if have_ffprobe:
        duration = check_video(args, report)
        check_cover(args.cover_vertical, "竖版封面", (1080, 1440), report)
        check_cover(args.cover_bilibili, "B站封面", (1440, 1080), report)
        check_cover(args.cover_youtube_shorts, "YouTube Shorts 封面", (1080, 1920), report)
        if args.legacy_audit:
            report.warn("legacy-audit", "只读审计旧片：新增的角标/元数据项降为 WARN，不能据此写 FINAL_QA_PASS")
        meta = load_aigc_meta(report)
        if meta is not None and duration is not None and have_ffmpeg:
            check_badge(args, report, meta, strict=not args.legacy_audit)
        elif meta is not None and duration is not None:
            (report.warn if args.legacy_audit else report.fail)("AI 角标未机检", "缺 ffmpeg")
        if meta is not None and duration is not None:
            check_metadata(args, report, meta, strict=not args.legacy_audit)

    if have_ffmpeg:
        if args.target_lufs is not None:
            analyze_loudness(args.video.resolve(), args.target_lufs, args.lufs_tolerance, report)
        elif args.decode_check:
            decode_check(args.video.resolve(), report)
        if args.frames_dir:
            extract_frames(args.video.resolve(), duration, args.frames_dir.resolve(), report)

    payload = report.payload()
    for check in report.checks:
        print(f"[{check.level}] {check.name}: {check.detail}")
    print("\n人工验收仍必需：")
    for item in payload["manual_checks"]:
        print(f"- {item}")

    if args.json_out:
        output = args.json_out.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nJSON report: {output}")

    return 1 if report.has_failures else 0


if __name__ == "__main__":
    sys.exit(main())
