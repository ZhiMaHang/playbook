#!/usr/bin/env python3
"""Verify a ZhiMaHang short-video delivery package without modifying source assets."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

# AI 标识的唯一真源(措辞常量、两要素、字高下限、元数据核对)在同插件 digital-human-video 里
AIGC_META_PATH = Path(__file__).resolve().parents[2] / "digital-human-video/scripts/aigc_meta.py"

# 角标像素判据(合成样本正反例校准,依据见 digital-human-video/scripts/aigc_selftest.py):
# ① 字形处亮度 ≥ 170 的像素占比 ≥ 95%(白字 alpha 220,压在任何底色上亮度都 ≥ 220);
# ② 字形与底框的亮度中位数差 ≥ 40。只有 ① 不够:纯白画面上没有角标也能过 ①。
GLYPH_ALPHA_MIN = 200   # 角标 PNG 里 alpha ≥ 200 的像素是字形,腐蚀 1 px 后作字形掩码
BOX_ALPHA_MAX = 100     # 0 < alpha ≤ 100 是底框
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
            "监听口型同步、BGM 稳定性及人声清晰度，辅助素材插入前后人声连续且录屏原声未意外叠入",
            "确认音乐和肖像等素材拥有目标平台使用权",
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
    return subprocess.run(command, text=True, capture_output=True, check=False)


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
            "-i",
            str(video),
            "-filter_complex",
            "ebur128=peak=true",
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
    result = run(["ffmpeg", "-v", "error", "-i", str(video), "-f", "null", "-"])
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
    )
    if result.returncode != 0 or len(result.stdout) != width * height * 4:
        return None
    return result.stdout[3::4]


def badge_masks(alpha: bytes, width: int, height: int) -> tuple[list[int], list[int]]:
    """字形掩码 = alpha ≥ GLYPH_ALPHA_MIN 再腐蚀 1 px（3×3 邻域全是字形才留下）；底框掩码 = 0 < alpha ≤ BOX_ALPHA_MAX。"""
    raw = [value >= GLYPH_ALPHA_MIN for value in alpha]
    glyph = [
        y * width + x
        for y in range(1, height - 1)
        for x in range(1, width - 1)
        if all(raw[(y + dy) * width + x + dx] for dy in (-1, 0, 1) for dx in (-1, 0, 1))
    ]
    box = [index for index, value in enumerate(alpha) if 0 < value <= BOX_ALPHA_MAX]
    return glyph, box


def median(values: list[int]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return float(ordered[middle]) if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def badge_frames(video: Path, x: int, y: int, w: int, h: int, fps: float):
    """只解角标区域（crop），逐帧读灰度；产出第 0 帧、之后每 1 秒一帧、末帧。"""
    process = subprocess.Popen(
        [
            "ffmpeg", "-v", "error", "-i", str(video), "-map", "0:v:0",
            # 先转灰度再裁:yuv420p 上 crop 会把奇数宽高、坐标按色度对齐取偶,逐帧步长就错位了
            "-vf", f"format=gray,crop={w}:{h}:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    size = w * h
    index, next_second, last = 0, 1, None
    assert process.stdout is not None
    while True:
        frame = process.stdout.read(size)
        if len(frame) < size:
            break
        if index == 0 or index >= round(next_second * fps):
            if index:
                next_second = math.floor(index / fps) + 1
            yield index, frame
            last = None
        else:
            last = (index, frame)
        index += 1
    process.stdout.close()
    process.wait()
    if last is not None:
        yield last


def check_badge(args: argparse.Namespace, report: Report, meta: Any, strict: bool) -> None:
    fail = report.fail if strict else report.warn
    if args.badge_spec is None:
        fail("AI 角标未机检", "缺 --badge-spec <角标 PNG 旁的 .json>；从旧期复制的脚本过不了这一项")
        return
    spec_path = args.badge_spec.resolve()
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        x, y, w, h = (int(spec[key]) for key in ("x", "y", "w", "h"))
        canvas = [int(value) for value in spec["canvas"]]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        fail("AI 角标几何", f"读不了 {spec_path}: {exc}")
        return
    video = report.media.get("video", {})
    width, height = video.get("width"), video.get("height")
    if canvas != [width, height]:
        fail("AI 角标画幅", f"角标按 {canvas[0]}x{canvas[1]} 计算，终版实际 {width}x{height}")
        return
    report.passed("AI 角标画幅", f"{width}x{height}")

    need = meta.min_glyph_px(width, height)
    ink, font_size = spec.get("ink_height_px"), spec.get("font_size")
    if isinstance(ink, int) and isinstance(font_size, int) and ink >= need and font_size >= need:
        report.passed("AI 角标字高", f"墨迹 {ink}px、字号 {font_size}，下限 {need}px（最短边 5%）")
    else:
        fail("AI 角标字高", f"墨迹 {ink}px、字号 {font_size}，下限 {need}px（最短边 5%）")

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

    png = spec_path.with_name(spec["png"]) if spec.get("png") else spec_path.with_suffix("")
    alpha = png_alpha(png, w, h)
    if alpha is None:
        fail("AI 角标像素", f"读不了角标 PNG {png}，或尺寸不是 {w}x{h}")
        return
    glyph, box = badge_masks(alpha, w, h)
    fps = video.get("fps") or 25.0
    if not glyph or not box:
        fail("AI 角标像素", "角标 PNG 里取不出字形或底框掩码")
        return
    bad: list[str] = []
    checked = 0
    for index, frame in badge_frames(args.video.resolve(), x, y, w, h, fps):
        checked += 1
        glyph_values = [frame[i] for i in glyph]
        ratio = sum(value >= GLYPH_LUMA_MIN for value in glyph_values) / len(glyph_values)
        contrast = median(glyph_values) - median([frame[i] for i in box])
        if ratio < GLYPH_RATIO_MIN or contrast < GLYPH_BOX_CONTRAST_MIN:
            bad.append(f"第 {index} 帧（{index / fps:.2f}s）亮字占比 {ratio:.1%}、字形−底框 {contrast:.0f}")
    if checked == 0:
        fail("AI 角标像素", "终版解不出任何帧")
    elif bad:
        fail("AI 角标像素", f"{len(bad)}/{checked} 帧不像有角标：" + "；".join(bad[:5]))
    else:
        report.passed("AI 角标像素", f"{checked} 帧（第 0 帧、每秒一帧、末帧）都检出角标")


def check_source_metadata(video: Path, source: Path, meta: Any, report: Report, fail: Any) -> None:
    if not source.is_file():
        fail("源元数据保留", f"源片不存在: {source}")
        return
    if source.samefile(video):
        fail("源元数据保留", "--source 与 --video 是同一个文件，比对不出任何东西；请传出片前的源片")
        return
    try:
        source_tags = meta.format_tags(source)
        problems = meta.compare(video, source)
    except RuntimeError as exc:
        fail("源元数据保留", str(exc))
        return
    if any(key.lower() == "description" for key in source_tags):
        fail("源元数据保留", "--source 带 description 键，不像源片（源片带 description 时 ffmpeg_args 会拒绝出片）；请传出片前的源片")
        return
    for problem in problems:
        fail("源元数据保留", problem)
    if not problems:
        report.passed("源元数据保留", "源片非封装器键逐字保留，brand 三键无重复值")


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
    parser.add_argument("--decode-check", action="store_true", help="完整解码成空输出以检查损坏帧")
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
