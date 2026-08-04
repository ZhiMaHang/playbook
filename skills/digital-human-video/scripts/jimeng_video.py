#!/usr/bin/env python3
"""即梦AI·视频生成 3.0 Pro(图/文 → 运动画面) — 提交+轮询+下载。

**与数字人(jimeng_dh.py)的根本区别:本接口不对口型。**
它按「一张图 + 一句提示词」生成一段有运镜和动态的画面,人物嘴型不会跟着念稿。
所以走本脚本的片子只能是**旁白式**(画外音配画面),不能是口播式。想要嘴型对得上
必须走 jimeng_dh.py 的 OmniHuman。这条别搞混——搞混了会做出「人在动、嘴不动、
声音在念」的诡异片子。

接口契约(与数字人不同,注意 Action 名):
  提交 CVSync2AsyncSubmitTask   查询 CVSync2AsyncGetResult   (Version=2022-08-31)
  请求体 req_key / image_urls[] / prompt / seed / aspect_ratio / frames
  返回   data.video_url

req_key 三个码的判别法与数字人一致(50200 名字不存在 / 50400 未开通 / 10000 可用),
排查见 jimeng_dh.py 文件头。

用法:
  jimeng_video.py --image <公网图URL> --prompt "镜头缓慢推近,人物轻微呼吸" --out clip1.mp4 --confirmed
  jimeng_video.py --prompt "雨夜街道霓虹" --out clip1.mp4 --confirmed        # 纯文生视频,不给图
  jimeng_video.py --resume <task_id> --out clip1.mp4                          # 续跑,不重复计费
"""
import argparse, json, os, sys, time, urllib.request
import importlib.util

_DIR = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("jimeng_dh", os.path.join(_DIR, "jimeng_dh.py"))
_dh = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_dh)

DEFAULT_REQ_KEY = "jimeng_ti2v_v30_pro"

# frames → 时长。即梦视频 3.0 系按 24fps 计,常用两档;超出范围服务端会拒。
# 拿不准就用默认 121(5 秒)——**先出一段验通再放量**,别一上来批量提交。
FRAMES_5S, FRAMES_10S = 121, 241


def req_key():
    return os.environ.get("DHV_VIDEO_REQ_KEY") or DEFAULT_REQ_KEY


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image", help="首帧参考图的公网 URL;不给则为纯文生视频")
    p.add_argument("--prompt", default="", help="运镜与动态描述(不是画面内容描述——内容已由图决定)")
    p.add_argument("--out", required=True)
    p.add_argument("--frames", type=int, default=FRAMES_5S, help=f"{FRAMES_5S}≈5秒 / {FRAMES_10S}≈10秒")
    p.add_argument("--aspect-ratio", default="9:16")
    p.add_argument("--seed", type=int, default=-1, help="-1 为随机;固定值可复现同一镜头")
    p.add_argument("--req-key", default=None)
    p.add_argument("--resume", help="已提交任务的 task_id;跳过提交直接轮询(不重复计费)")
    p.add_argument("--confirmed", action="store_true",
                   help="已向用户报过成本并拿到确认。**没有这个开关不会提交计费任务**")
    a = p.parse_args()
    rk = a.req_key or req_key()

    # task_id 落盘:与 jimeng_dh.py 同样的理由——提交后进程被杀,任务仍在服务端跑、
    # 仍计费,task_id 只在 stdout 里就永久丢了。提交成功立刻写 sidecar,启动时自动续跑。
    sidecar = a.out + ".task"
    task_id = a.resume
    if not task_id and os.path.exists(sidecar):
        task_id = open(sidecar).read().strip() or None
        if task_id:
            print(f"发现未完成任务 {task_id}(来自 {sidecar}),直接续跑,不重复提交")

    if not task_id:
        # 计费闸门:CVSync2AsyncSubmitTask 是本脚本唯一会花钱的调用。
        # SKILL.md 的确认门是写给执行者的纪律,纪律会漏,故这里再加一道机器闸。
        if not (a.confirmed or os.environ.get("DHV_CONFIRMED") == "1"):
            secs = a.frames / 24.0
            sys.exit("⛔ 未确认,不提交。\n"
                     f"   这一步会调用 CVSync2AsyncSubmitTask 并计费"
                     f"(req_key={rk},{a.frames} 帧 ≈ {secs:.1f} 秒)。\n"
                     "   请先把分镜与成本报给用户、拿到明确的「确认/开做」,\n"
                     "   再加 --confirmed 重跑(或设 DHV_CONFIRMED=1)。")
        body = {"req_key": rk, "prompt": a.prompt, "seed": a.seed,
                "aspect_ratio": a.aspect_ratio, "frames": a.frames}
        if a.image:
            body["image_urls"] = [a.image]
        r = _dh.signed_post("CVSync2AsyncSubmitTask", body)
        if r.get("code") != 10000:
            sys.exit(f"提交失败 code={r.get('code')} msg={r.get('message')} request_id={r.get('request_id')}")
        task_id = r["data"]["task_id"]
        with open(sidecar, "w") as f:
            f.write(task_id)
        mode = "图生视频" if a.image else "文生视频"
        print(f"[{mode}] task_id={task_id} 已提交(已存 {sidecar}),{a.frames} 帧,轮询中…")

    while True:
        time.sleep(15)
        q = _dh.signed_post("CVSync2AsyncGetResult", {"req_key": rk, "task_id": task_id})
        if q.get("code") != 10000:
            sys.exit(f"查询失败 code={q.get('code')} msg={q.get('message')} request_id={q.get('request_id')}")
        data = q.get("data", {})
        st = data.get("status")
        print(f"  status={st}")
        if st == "done" or data.get("video_url"):
            url = data.get("video_url")
            if not url:
                sys.exit(f"done 但无 video_url: {json.dumps(q, ensure_ascii=False)[:400]}")
            urllib.request.urlretrieve(url, a.out)   # URL 有效期短,立即落盘
            if os.path.exists(sidecar):
                os.remove(sidecar)
            print(f"已下载 → {a.out}")
            return
        if st in ("not_found", "expired", "failed"):
            sys.exit(f"任务异常 status={st} {json.dumps(q, ensure_ascii=False)[:300]}")


if __name__ == "__main__":
    main()
