#!/usr/bin/env python3
"""即梦AI 文生图 → 口播背景场景图。

用途:OmniHuman 数字人**没有场景/提示词参数**(2026-08 核实,输入只有 image_url +
audio_url),成片背景 100% 由输入图决定。所以「在车里口播」的正确做法是:
本脚本按提示词生成纯场景图 → make_portrait.sh 把真人抠像贴进去 → 喂给 OmniHuman。

**为什么不用图生图(i2i)直接把人放进场景**:i2i 会重新生成像素,人脸相似度不保证。
数字人是在这张脸上驱动口型的,脸一变就不是本人了——涉及肖像的片子不接受这种不确定性。
故本脚本只生成**不含人物**的场景,人像始终来自真实照片。

用法:
  gen_scene.py --prompt "汽车驾驶座视角,暖色车内灯,车窗外夜景虚化" --out scene.png
  gen_scene.py --prompt "..." --out scene.png --size 1080x1920 --req-key jimeng_t2i_v31
"""
import argparse, base64, json, os, sys, time, urllib.request
import importlib.util

_DIR = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("jimeng_dh", os.path.join(_DIR, "jimeng_dh.py"))
_dh = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_dh)

DEFAULT_T2I = os.environ.get("DHV_T2I_REQ_KEY", "jimeng_t2i_v40")

# 场景提示词的固定后缀:数字人要贴进来,所以场景必须给人留位、且不能自带人物
# ⚠️ 别写「留出人物站位」这类话——2026-08-02 实测,模型会照办,直接画一个灰色人形占位块。
SCENE_SUFFIX = "，空镜，画面中没有人，不要出现文字和水印，景深虚化，真实摄影质感"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--prompt", required=True, help="场景描述(中文即可);会自动追加「无人物无文字」约束")
    p.add_argument("--out", required=True)
    p.add_argument("--size", default="1080x1920", help="默认竖版 9:16,与数字人成片同画幅")
    p.add_argument("--req-key", default=DEFAULT_T2I)
    p.add_argument("--raw-prompt", action="store_true", help="不追加场景约束后缀,原样发送")
    p.add_argument("--image", help="图生图:参考图公网 URL。给了就走 i2i,人物由参考图带入")
    p.add_argument("--strength", type=float, default=0.55,
                   help="i2i 重绘强度 0~1,越小越像原图。**涉及本人肖像时建议 ≤0.5**,再大脸就不是本人了")
    a = p.parse_args()

    w, h = (int(x) for x in a.size.lower().split("x"))
    prompt = a.prompt if a.raw_prompt else a.prompt + SCENE_SUFFIX

    if a.image:
        # 图生图:参考图带入人物。注意 i2i 是重新生成像素,人脸相似度**不保证**;
        # 若成片要用作本人出镜,合成后必须由本人确认「这还是我」,不确认不进 OmniHuman。
        if a.req_key == DEFAULT_T2I:
            a.req_key = os.environ.get("DHV_I2I_REQ_KEY", "jimeng_i2i_v30")
        body = {"req_key": a.req_key, "prompt": prompt, "image_urls": [a.image],
                "width": w, "height": h, "strength": a.strength}
        print(f"模式=图生图 req_key={a.req_key} strength={a.strength}")
    else:
        body = {"req_key": a.req_key, "prompt": prompt, "width": w, "height": h}
        print(f"模式=文生图 req_key={a.req_key}")
    r = _dh.signed_post("CVProcess", body)
    if r.get("code") != 10000:
        # 同步接口不通就退到异步提交
        r = _dh.signed_post("CVSubmitTask", body)
        if r.get("code") != 10000:
            sys.exit(f"生成失败 code={r.get('code')} msg={r.get('message')} request_id={r.get('request_id')}")
        task_id = r["data"]["task_id"]
        print(f"task_id={task_id} 已提交,轮询中…")
        while True:
            time.sleep(5)
            q = _dh.signed_post("CVGetResult", {"req_key": a.req_key, "task_id": task_id})
            if q.get("code") != 10000:
                sys.exit(f"查询失败 code={q.get('code')} msg={q.get('message')}")
            st = q["data"].get("status")
            print(f"  status={st}")
            if st == "done":
                r = q; break
            if st in ("not_found", "expired"):
                sys.exit(f"任务异常 status={st}")

    data = r.get("data", {})
    # 两种返回形态:image_urls(链接) 或 binary_data_base64(内联)
    urls = data.get("image_urls") or []
    b64s = data.get("binary_data_base64") or []
    if urls:
        urllib.request.urlretrieve(urls[0], a.out)
    elif b64s:
        open(a.out, "wb").write(base64.b64decode(b64s[0]))
    else:
        sys.exit(f"返回里既无 image_urls 也无 binary_data_base64: {json.dumps(r, ensure_ascii=False)[:300]}")
    print(f"场景图 → {a.out} ({w}x{h})")
    print("下一步:make_portrait.sh green <绿幕人像> " + a.out + " <合成图>  然后喂 jimeng_dh.py")

if __name__ == "__main__":
    main()
