#!/usr/bin/env python3
"""短视频流程「新期默认不加 BGM」口径的自测:只读模板、人工清单与文档,不造样本、不调 ffmpeg、不写任何文件。
用法: python3 pipeline_defaults_selftest.py     # 全过打印 PIPELINE_DEFAULTS_SELFTEST_PASS,否则非零退出

改分镜参数模板的音频默认值、verify_package.py 的人工清单,或文档里讲 BGM 的句子之后跑这一条。覆盖:
  1. assets/storyboard-motion-v2.json 的 audio_defaults:bgm_enabled 是 false,bgm、bgm_rights_evidence、
     end_fade_seconds 是 null,保留锁定口播的两项仍是 true;
  2. verify_package.py 的人工清单(带不带封面两种)里有「无禁用音乐残留」和「新期默认无 BGM」两条;
  3. 已退役的「默认加 BGM」措辞不再出现在本技能的 SKILL.md、references/、assets/、verify_package.py
     和仓库 README.md 里(改口径时最容易漏的是散在各处的旧句子);
  4. production.md 写了禁用清单的本地覆盖文件放哪,文件名落在仓库 .gitignore 的 .local.md 排除规则里
     (清单里是使用方自己的文件名与校验值,不许进版本库)。
"""
from __future__ import annotations

import fnmatch
import importlib.util
import json
import sys
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


def main() -> int:
    audio = json.loads(TEMPLATE.read_text(encoding="utf-8")).get("audio_defaults", {})
    wrong = {key: audio.get(key, "<缺>") for key, want in AUDIO_DEFAULTS.items() if key not in audio or audio[key] is not want}
    check(not wrong, "分镜参数模板:BGM 默认关闭、不带曲目与淡出,保留锁定口播",
          "不对的项: " + json.dumps(wrong, ensure_ascii=False) if wrong else "")

    for with_cover, label in ((False, "不带封面"), (True, "带封面")):
        items = manual_checks(with_cover)
        missing = [text for text in MANUAL_REQUIRED if not any(text in item for item in items)]
        check(not missing, f"人工清单({label}):有「无禁用音乐残留」「新期默认无 BGM」两条", "缺: " + "、".join(missing) if missing else "")

    hits = [f"{path.name}:{number} 「{phrase}」"
            for path in scanned_files()
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            for phrase in RETIRED if phrase in line]
    check(not hits, "已退役的「默认加 BGM」措辞没有再出现", "; ".join(hits[:6]))

    production = PRODUCTION.read_text(encoding="utf-8")
    check(f"references/{BLOCKLIST_NAME}" in production
          and fnmatch.fnmatch(f"skills/{SKILL.name}/references/{BLOCKLIST_NAME}", GITIGNORE_RULE),
          "production.md 写了禁用清单放哪,文件名落在 .local.md 的排除规则里")
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
