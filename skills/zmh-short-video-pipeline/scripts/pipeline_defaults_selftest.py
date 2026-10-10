#!/usr/bin/env python3
"""短视频流程「新期默认不加 BGM」口径的自测:只读模板、人工清单与文档,不调 ffmpeg;除一个用完即删的临时目录外不写任何文件。
用法: python3 pipeline_defaults_selftest.py     # 全过打印 PIPELINE_DEFAULTS_SELFTEST_PASS,否则非零退出

改分镜参数模板的音频默认值、verify_package.py 的人工清单,或文档里讲 BGM 的句子之后跑这一条。覆盖:
  1. assets/storyboard-motion-v2.json 的 audio_defaults:bgm_enabled 是 false,bgm、bgm_rights_evidence、
     end_fade_seconds 是 null,保留锁定口播的两项仍是 true;
  2. verify_package.py 的人工清单(带不带封面两种)里有「无禁用音乐残留」和「新期默认无 BGM」两条;
  3. 已退役的「默认加 BGM」措辞不再出现在本技能的 SKILL.md、references/、assets/、verify_package.py
     和仓库 README.md 里(改口径时最容易漏的是散在各处的旧句子);扫到不是 UTF-8 文本的文件
     (如 macOS 在 assets/ 里留下的 .DS_Store)跳过并打印 [SKIP],不抛异常、不因此报失败;
  4. production.md 写了禁用清单的本地覆盖文件放哪:推荐的 ~/.config 位置写在前,技能目录的后备位置
     落在仓库 .gitignore 的 .local.md 排除规则里(清单里是使用方自己的文件名与校验值,不许进版本库);
     并写了找不到清单时先问用户、不当成没有禁用曲目。
"""
from __future__ import annotations

import contextlib
import fnmatch
import importlib.util
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True   # 下面按路径载入 verify_package.py,不在脚本目录留 __pycache__

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
REPO = SKILL.parents[1]
VERIFY = HERE / "verify_package.py"
TEMPLATE = SKILL / "assets/storyboard-motion-v2.json"
PRODUCTION = SKILL / "references/production.md"

# 期望值独立写死,不从被测文件里反推
AUDIO_DEFAULTS = {"bgm_enabled": False, "bgm": None, "bgm_rights_evidence": None, "end_fade_seconds": None,
                  "preserve_locked_voice": True, "use_master_narration": True}
MANUAL_REQUIRED = ("无禁用音乐残留", "新期默认无 BGM")
BLOCKLIST_NAME = "bgm-blocklist.local.md"
GITIGNORE_RULE = "skills/*/references/*.local.md"
# 已退役措辞登记:旧口径「默认加 BGM」留下的句子,任何一条重新出现都算口径漂回去了
RETIRED = (
    "inherit_current_verified_track_and_mix",
    "混入已有权利依据的系列 BGM",
    "沿用本期可用配乐与已验收混音",
    "默认使用自制或明确可商用的 BGM",
    "公共 BGM 是否放顶层",
    "BGM 稳定性及人声清晰度",
    "检查 BGM 稳定、可闻、不压人声",
    "口型、BGM 和角标",
    "AI 角标、BGM、三版封面",
    "AI 角标和 BGM",
)

FAILURES: list[str] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        FAILURES.append(name)
    return ok


def manual_checks(with_cover: bool) -> list[str]:
    spec = importlib.util.spec_from_file_location("verify_package_under_test", VERIFY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    report = module.Report()
    report.include_cover_manual_check = with_cover
    return list(report.payload()["manual_checks"])


def scanned_files() -> list[Path]:
    files = [SKILL / "SKILL.md", VERIFY]
    files += sorted((SKILL / "references").glob("*.md")) + sorted(p for p in (SKILL / "assets").iterdir() if p.is_file())
    readme = REPO / "README.md"
    return [p for p in files + [readme] if p.is_file() and not p.name.endswith(".local.md")]


def retired_hits(files: list[Path]) -> list[str]:
    """返回命中的旧措辞。不是 UTF-8 文本的文件不是文档,跳过,并当场打印一行 [SKIP] 说明跳过了哪个,不静默。"""
    hits: list[str] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            print(f"[SKIP] {path.name}: 不是 UTF-8 文本,没有扫")
            continue
        hits += [f"{path.name}:{number} 「{phrase}」" for number, line in enumerate(text.splitlines(), 1)
                 for phrase in RETIRED if phrase in line]
    return hits


def binary_file_case() -> None:
    """assets/ 的临时副本里放一个二进制文件和一份带旧措辞的文本:二进制的跳过并说一声,文本的照样抓到。"""
    name = "扫描遇到二进制文件:跳过它并打印 [SKIP]、不抛异常,别的文件照扫"
    tmp = Path(tempfile.mkdtemp(prefix="pipeline_defaults_selftest_"))
    try:
        assets = tmp / "assets"
        shutil.copytree(SKILL / "assets", assets)
        (assets / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1\xff\xfe\x80\x81")
        (assets / "zz_planted.md").write_text(f"旧句子:{RETIRED[1]}\n", encoding="utf-8")
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            hits = retired_hits(sorted(p for p in assets.iterdir() if p.is_file()))
        skip_lines = [line for line in said.getvalue().splitlines() if line.startswith("[SKIP] ")]
        check(len(skip_lines) == 1 and ".DS_Store" in skip_lines[0] and len(hits) == 1 and "zz_planted.md" in hits[0],
              name, f"SKIP 行 {skip_lines},命中 {hits}")
    except Exception as exc:  # noqa: BLE001 - 抛异常也要记成具名 FAIL
        check(False, name, f"{type(exc).__name__}: {exc}"[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    audio = json.loads(TEMPLATE.read_text(encoding="utf-8")).get("audio_defaults", {})
    wrong = {key: audio.get(key, "<缺>") for key, want in AUDIO_DEFAULTS.items() if key not in audio or audio[key] is not want}
    check(not wrong, "分镜参数模板:BGM 默认关闭、不带曲目与淡出,保留锁定口播",
          "不对的项: " + json.dumps(wrong, ensure_ascii=False) if wrong else "")

    for with_cover, label in ((False, "不带封面"), (True, "带封面")):
        items = manual_checks(with_cover)
        missing = [text for text in MANUAL_REQUIRED if not any(text in item for item in items)]
        check(not missing, f"人工清单({label}):有「无禁用音乐残留」「新期默认无 BGM」两条", "缺: " + "、".join(missing) if missing else "")

    hits = retired_hits(scanned_files())
    check(not hits, "已退役的「默认加 BGM」措辞没有再出现", "; ".join(hits[:6]))
    binary_file_case()

    production = PRODUCTION.read_text(encoding="utf-8")
    recommended, fallback = f"~/.config/{SKILL.name}/{BLOCKLIST_NAME}", f"references/{BLOCKLIST_NAME}"
    check(recommended in production and fallback in production and production.index(recommended) < production.index(fallback)
          and fnmatch.fnmatch(f"skills/{SKILL.name}/{fallback}", GITIGNORE_RULE),
          "production.md 写了禁用清单放哪:推荐的 ~/.config 位置在前,技能目录的后备位置落在 .local.md 的排除规则里")
    check("不能当成没有禁用曲目" in production, "production.md 写了找不到清单时先问用户,不当成没有禁用曲目")
    gitignore = REPO / ".gitignore"
    if gitignore.is_file():   # 装成插件后可能没有这个文件,有才核
        check(GITIGNORE_RULE in gitignore.read_text(encoding="utf-8").splitlines(),
              "仓库 .gitignore 排除了 references/ 下的 .local.md(禁用清单不进版本库)")

    if FAILURES:
        print(f"\n{len(FAILURES)} 项失败: " + "、".join(FAILURES))
        return 1
    print("\nPIPELINE_DEFAULTS_SELFTEST_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
