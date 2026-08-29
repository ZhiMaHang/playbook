#!/usr/bin/env python3
"""Verify a ZhiMaHang short-video delivery package without modifying source assets."""

from __future__ import annotations

import argparse
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
            "确认第一句实际口播从开头即有对应字幕，且没有混入未口播的封面标题、文档标题或栏目名",
            "监听口型同步、BGM 稳定性及人声清晰度",
            "确认音乐和肖像等素材拥有目标平台使用权",
        ]
        if self.include_cover_manual_check:
            manual_checks[3:3] = [
                "将两版平台封面与用户指定或当前已验收的同规格基准并排核对标题区黑底/遮罩及字体排版样式",
                "缩略查看 B站 4:3 封面，确认左文右像、近黑标题区、金色标签、金/白两行、描边与标题安全区符合当前统一基准",
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True, help="终版 MP4")
    parser.add_argument("--cover-vertical", type=Path, help="1080x1440 竖版封面")
    parser.add_argument("--cover-bilibili", type=Path, help="1440x1080 B站横版封面")
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = Report()
    report.include_cover_manual_check = any((args.cover_vertical, args.cover_bilibili))
    have_ffprobe = require_binary("ffprobe", report)
    need_ffmpeg = bool(args.frames_dir or args.decode_check or args.target_lufs is not None)
    have_ffmpeg = require_binary("ffmpeg", report) if need_ffmpeg else True

    duration: float | None = None
    if have_ffprobe:
        duration = check_video(args, report)
        check_cover(args.cover_vertical, "竖版封面", (1080, 1440), report)
        check_cover(args.cover_bilibili, "B站封面", (1440, 1080), report)

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
