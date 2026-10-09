#!/usr/bin/env python3
"""AI 生成合成内容标识的唯一真源:角标措辞、字高下限、成片元数据参数与核对(只用标准库 + ffprobe)。

自设基线按 GB 45438-2025 图解所列数值:显式标识同时含「AI/人工智能」与「生成/合成」两要素,
文字高度不低于画面最短边的 5%,从起始画面起全程显示。这是我方自律基线,不是认证结论。

元数据:成片保留源片的全部元数据(含服务商写入的隐式标识),只另写一条 description 普通说明。
⛔ 我方不是生成合成服务提供者,不写 AIGC、ContentProducer、ProduceID 这类形似服务商隐式标识的键。

用法:
  aigc_meta.py args <源片.mp4> <源片输入序号>    # 每行一个 ffmpeg 参数;源片已有别的 description(不是我方说明字段)时退出码 2
  aigc_meta.py args --no-source                  # 没有源片(全是图卡)时:只写说明字段并清理 brand 三键
  aigc_meta.py check <终版.mp4> --source <源片.mp4>   # 有问题时退出码 1;隐私提示只打印、不删
  aigc_meta.py sidecar <out.json> <源片1> [源片2 ...]  # 多段源片元数据全量落本地;各段不同时打印 ⚠️
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
import re
import subprocess
import sys
from pathlib import Path

BADGE_TEXT = "视频由 AI 生成"
RETIRED_BADGE_TEXTS = ("视频由 AI 制作", "内容由 AI 制作")  # ⛔ 已退役,不许再用(缺「生成/合成」要素)
AI_DESCRIPTION = "本视频含人工智能生成合成内容（AI 生成），画面内已加显式标识"

MIN_GLYPH_RATIO = 0.05   # 字高下限:画面最短边的 5%
INK_SAFETY_PX = 2        # 渲染时墨迹再多留 2 px,吸收编码带来的边缘模糊

# 由封装器决定、和源片无关的键:比对时排除(不分大小写:Matroska/WebM 源片读出来是 ENCODER)
MUXER_KEYS = {"major_brand", "minor_version", "compatible_brands", "encoder"}
BRAND_KEYS = ("major_brand", "minor_version", "compatible_brands")
# 既写 mvhd 又写 mdta 的键:带 use_metadata_tags 时 ffprobe 把两处拼成「mvhd 值;mdta 原串」读出。
# 源片本身已带「;」(源片也是 use_metadata_tags 写的)时原样映射过去,封装器解析不了,mvhd 的创建时间会被写成 0,
# 所以 ffmpeg_args 显式写回一个值:mdta 原串是带时区的绝对时间就写它(终版读回与源片逐字相同);
# 不是(如 ComfyUI 的 VHS 保存节点写的 now、或不带时区的本地时间)就写 mvhd 那半(已归一成 UTC),
# 否则封装器会按出片时刻或出片机时区重新解析,创建时间就变了。
DUAL_STORED_KEYS = {"creation_time"}
ABS_TIME_RE = re.compile(
    r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)")


def _utc_second(text: str) -> datetime | None:
    """带时区的绝对时间 → 精确到秒的 UTC 时刻;不是这种格式返回 None。"""
    m = ABS_TIME_RE.fullmatch(text.strip())
    if not m:
        return None
    tz = m.group(7)
    offset = timedelta(0) if tz == "Z" else timedelta(
        hours=int(tz[1:3]), minutes=int(tz[-2:])) * (1 if tz[0] == "+" else -1)
    return datetime(*(int(g) for g in m.groups()[:6]), tzinfo=timezone(offset)).astimezone(timezone.utc)

PRIVATE_KEYS = {"workflow", "prompt"}   # ComfyUI 原始输出自带,含工作流、模型名和提示词
WINDOWS_PATH_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")


class DescriptionConflict(ValueError):
    """源片已有 description:不覆盖,交人处理。"""


def min_glyph_px(width: int, height: int) -> int:
    """字高下限 ceil(5% × 最短边)。round 先消掉浮点尾差,免得 54.000…01 被 ceil 成 55。"""
    return math.ceil(round(min(int(width), int(height)) * MIN_GLYPH_RATIO, 6))


def has_two_elements(text: str | None) -> bool:
    """两要素:含「AI」或「人工智能」,且含「生成」或「合成」。"""
    s = text or ""
    return ("AI" in s or "人工智能" in s) and ("生成" in s or "合成" in s)


def format_tags(path: str | Path) -> dict[str, str]:
    """ffprobe 读容器级元数据(format.tags)。读不了就抛错,不当作「没有元数据」。"""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 读不了 {path}: {result.stderr.strip() or result.returncode}")
    data = json.loads(result.stdout or "{}")
    return dict(data.get("format", {}).get("tags", {}) or {})


def ffmpeg_args(source_path: str | Path | None, input_index: int | None) -> list[str]:
    """成片编码时追加的元数据参数。source_path 是源片,input_index 是它在本条 ffmpeg 命令里的输入序号
    (必须显式给:不写时 ffmpeg 取第 0 路,源片不在第 0 路就整套丢光)。
    source_path 为 None 表示没有源片(全是图卡),只写说明字段、清 brand 三键,不映射元数据。
    带 use_metadata_tags 才能保住非标准键(如服务商的 AIGC);它会把 brand 三键写成「isom;isom」重复值,所以显式清掉。"""
    if source_path is None:
        mapping: list[str] = []
    else:
        tags = format_tags(source_path)
        # 已是我方说明字段(如对成片再混音)时原样再写一遍不算覆盖;别的 description 一律交人处理
        clash = [k for k in tags if k.lower() == "description" and tags[k] != AI_DESCRIPTION]
        if clash:
            raise DescriptionConflict(
                f"源片 {source_path} 已有 {clash[0]} 键,写说明字段会把它静默覆盖;请先由人决定怎么处理")
        index = int(input_index)
        if index < 0:
            raise ValueError(f"源片输入序号必须 ≥ 0,收到 {index}")
        mapping = ["-map_metadata", str(index)]
        for key in sorted(DUAL_STORED_KEYS & tags.keys()):
            parts = tags[key].split(";")
            if len(parts) > 1:
                keep = parts[-1] if _utc_second(parts[-1]) else parts[0]
                mapping += ["-metadata", f"{key}={keep}"]
    clear_brands = [a for k in BRAND_KEYS for a in ("-metadata", f"{k}=")]
    return mapping + clear_brands + ["-metadata", f"description={AI_DESCRIPTION}",
                                     "-movflags", "+faststart+use_metadata_tags"]


def _same_value(key: str, final: str, source: str) -> bool:
    if key in DUAL_STORED_KEYS:
        fin, src = final.split(";"), source.split(";")
        if _utc_second(src[-1]):   # 源片原串是绝对时间:终版的原串(最后一段)必须逐字相同
            return fin[-1] == src[-1]
        # 源片原串不是绝对时间(now、本地时间):只比两边能解析的时刻,精确到秒
        return {t for t in map(_utc_second, fin) if t} == {t for t in map(_utc_second, src) if t}
    return final == source


def compare(final: str | Path, source: str | Path) -> list[str]:
    """终版对源片的元数据核对,返回问题清单(空表示通过)。
    传错源片(终版自己、终版副本)时比对不出任何东西,先拦下来。"""
    src, fin = format_tags(source), format_tags(final)
    try:
        same = Path(source).resolve().samefile(Path(final).resolve())
    except OSError:   # ffprobe 打得开、文件系统 stat 不到(如 file: 前缀)的路径
        same = False
    if same:
        return ["源片与终版是同一个文件,比对不出任何东西;--source 请传出片前的源片"]
    if any(k.lower() == "description" for k in src):
        return ["源片带 description 键,不像出片前的源片(最初的源片不带说明字段);--source 请传最初的源片,已出成片再混音时也一样"]
    problems = []
    for key, value in src.items():
        if key.lower() in MUXER_KEYS:
            continue
        if key not in fin:
            problems.append(f"源片键 {key} 在终版里丢了")
        elif not _same_value(key, fin[key], value):
            problems.append(f"源片键 {key} 的值在终版里变了")
    if not has_two_elements(fin.get("description")):
        problems.append("终版 description 缺失或不含两要素(AI/人工智能 + 生成/合成)")
    for key in BRAND_KEYS:
        if ";" in str(fin.get(key, "")):
            problems.append(f"终版 {key}={fin[key]!r} 是重复值(brand 三键没清)")
    return problems


def privacy_flags(path: str | Path) -> list[str]:
    """只提示、不删:源片元数据按原样保留,发不发由人决定。"""
    tags = format_tags(path)
    flags = [f"元数据含 {k} 键({len(v)} 字符),可能带工作流、模型文件名和提示词"
             for k, v in tags.items() if k.lower() in PRIVATE_KEYS]
    blob = "\n".join(tags.values())
    if WINDOWS_PATH_RE.search(blob):
        flags.append("元数据含 Windows 盘符路径")
    if "/Users/" in blob:
        flags.append("元数据含 /Users/ 本机路径")
    return flags


def write_sidecar(out_json: str | Path, sources: list[str | Path]) -> bool:
    """把每个片段源文件的 format.tags 全量写进本地 sidecar(不上传)。
    一个文件只留一份隐式标识:容器里只映射第一段的。各段(除封装器键外)不同时返回 True。"""
    entries = [{"source": Path(s).name, "tags": format_tags(s)} for s in sources]
    own = [{k: v for k, v in e["tags"].items() if k.lower() not in MUXER_KEYS} for e in entries]
    differs = any(o != own[0] for o in own[1:])
    payload = {"kept_in_container": entries[0]["source"] if entries else None,
               "segments_differ": differs, "segments": entries}
    Path(out_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return differs


def _main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "args":
        try:
            if argv[1:] == ["--no-source"]:
                args = ffmpeg_args(None, None)
            elif len(argv) == 3:
                args = ffmpeg_args(argv[1], int(argv[2]))
            else:
                print(__doc__, file=sys.stderr)
                return 2
        except (DescriptionConflict, RuntimeError, ValueError) as exc:
            print(f"aigc_meta.py: ⛔ {exc}", file=sys.stderr)
            return 2
        print("\n".join(args))
        return 0
    if len(argv) == 4 and argv[0] == "check" and argv[2] == "--source":
        try:
            problems, flags = compare(argv[1], argv[3]), privacy_flags(argv[1])
        except RuntimeError as exc:
            print(f"[FAIL] {exc}")
            return 2
        for p in problems:
            print(f"[FAIL] {p}")
        for f in flags:
            print(f"[WARN] {f}")
        if not problems:
            print("[PASS] 源元数据保留、说明字段两要素、brand 三键无重复值")
        return 1 if problems else 0
    if len(argv) >= 3 and argv[0] == "sidecar":
        try:
            differs = write_sidecar(argv[1], argv[2:])
        except RuntimeError as exc:
            print(f"aigc_meta.py: ⛔ {exc}", file=sys.stderr)
            return 2
        if differs:
            print(f"⚠️ 各片段源文件的元数据不同:容器里只保留第一段的,全部片段见 {argv[1]}(本地留存,不上传)")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
