#!/usr/bin/env python3
"""零成本验证 dhv 配置:签名调 CVGetResult(假 task_id,不产生生成费用)。
用法: python3 validate.py [--tts]
退出码 0=鉴权与服务可用;1=配置缺失;2=鉴权失败;3=服务未开通(疑似);4=其他
"""
import json, os, sys, importlib.util

CONF = os.path.expanduser("~/.config/zmh-dhv/env")

def load_env():
    if not os.path.exists(CONF):
        print(f"❌ 配置文件不存在: {CONF}"); sys.exit(1)
    for line in open(CONF):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1); os.environ.setdefault(k, v)

def main():
    load_env()
    for k in ("VOLC_AK", "VOLC_SK"):
        if not os.environ.get(k):
            print(f"❌ 缺 {k}"); sys.exit(1)
    # 复用制作技能的签名实现,避免两份签名代码漂移
    dh_path = os.path.join(os.path.dirname(__file__),
        "..", "..", "digital-human-video", "scripts", "jimeng_dh.py")
    spec = importlib.util.spec_from_file_location("jimeng_dh", os.path.abspath(dh_path))
    dh = importlib.util.module_from_spec(spec); spec.loader.exec_module(dh)

    r = dh.signed_post("CVGetResult", {"req_key": dh.REQ_KEY, "task_id": "0"})
    code, msg = r.get("code"), str(r.get("message", ""))
    meta = str(r.get("ResponseMetadata", {}).get("Error", {}) if isinstance(r.get("ResponseMetadata"), dict) else "")
    blob = f"{code} {msg} {meta}"
    print(f"探测响应: code={code} message={msg[:120]}")
    low = blob.lower()
    if any(s in low for s in ("signature", "credential", "accesskey", "authentication", "unauthorized")):
        print("❌ 鉴权失败:AK/SK 不对或已失效"); sys.exit(2)
    if any(s in low for s in ("not open", "未开通", "no permission", "denied", "not activated", "50401")):
        print("⚠️ 密钥有效,但服务疑似未开通:去控制台开通即梦AI「数字人快速模式」"); sys.exit(3)
    if code is not None:
        print("✅ 鉴权通过、服务可达(业务层报 task 不存在属预期)"); sys.exit(0)
    print(f"⚠️ 无法判读,原始响应: {json.dumps(r, ensure_ascii=False)[:400]}"); sys.exit(4)

if __name__ == "__main__":
    if "--tts" in sys.argv:
        load_env()
        if not (os.environ.get("TTS_APPID") and os.environ.get("TTS_TOKEN")):
            print("TTS 未配置(将使用免费 edge-tts 降级)"); sys.exit(0)
        tts_path = os.path.join(os.path.dirname(__file__),
            "..", "..", "digital-human-video", "scripts", "tts.py")
        spec = importlib.util.spec_from_file_location("tts", os.path.abspath(tts_path))
        t = importlib.util.module_from_spec(spec); spec.loader.exec_module(t)
        t.doubao_tts("好", "/tmp/dhv_tts_probe.wav", os.environ.get("TTS_VOICE", "zh_female_shuangkuaisisi_moon_bigtts"))
        print("✅ 豆包 TTS 可用"); sys.exit(0)
    main()
