#!/usr/bin/env python3
"""即梦AI·数字人(OmniHuman) — 提交+轮询+下载。
文档: docs.volcengine.com/docs/85621/1810471
鉴权: 火山 OpenAPI HMAC-SHA256 签名(host=visual.volcengineapi.com, region=cn-north-1, service=cv)
用法: VOLC_AK=xx VOLC_SK=xx python3 jimeng_dh.py --image <公网图URL> --audio <公网音频URL> --out seg1.mp4
注意: 计费 1 元/秒(按产出视频时长),并发 1;调用前须经用户确认成本。

req_key 与控制台开通的 SKU 必须对应,否则 CVSubmitTask/CVGetResult 一律回
`50400 Access Denied`——这个错**不是**鉴权失败也**不是**没开通服务,是「这个
req_key 对应的能力没对本账号开放」。三个码可以互相区分(2026-08-02 实测):
  50200 Invalid Input Parameters: req_key  → req_key 名字根本不存在
  50400 Access Denied                      → 名字有效,但该能力未对本账号开放
  10000 Success                            → 通了
排查时拿一个已知开通的 req_key 做对照探针即可定位,别去动 IAM 权限。
默认值对应控制台「即梦AI → OmniHuman1.5」;若账号开的是别的 SKU,在
~/.config/zmh-dhv/env 里加一行 DHV_REQ_KEY=<你的 req_key> 覆盖,不必改代码。
"""
import argparse, datetime, hashlib, hmac, json, os, sys, time, urllib.request

HOST, REGION, SERVICE = "visual.volcengineapi.com", "cn-north-1", "cv"
DEFAULT_REQ_KEY = "jimeng_realman_avatar_picture_omni_v15"


def req_key():
    """控制台 SKU 对应的 req_key;可用 DHV_REQ_KEY 覆盖(见文件头说明)。"""
    return os.environ.get("DHV_REQ_KEY") or DEFAULT_REQ_KEY
CONF = os.path.expanduser("~/.config/zmh-dhv/env")

def load_env():
    """配置文件 → 环境变量(已设的环境变量优先)。由 digital-human-video-init 技能生成。"""
    if os.path.exists(CONF):
        for line in open(CONF):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)

load_env()

def _hmac(key, msg): return hmac.new(key, msg.encode(), hashlib.sha256).digest()

def signed_post(action, body: dict):
    ak, sk = os.environ["VOLC_AK"], os.environ["VOLC_SK"]
    payload = json.dumps(body).encode()
    t = datetime.datetime.utcnow()
    xdate, short = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    query = f"Action={action}&Version=2022-08-31"
    payload_hash = hashlib.sha256(payload).hexdigest()
    headers = {"Content-Type": "application/json", "Host": HOST,
               "X-Date": xdate, "X-Content-Sha256": payload_hash}
    signed_headers = "content-type;host;x-content-sha256;x-date"
    canonical = "\n".join(["POST", "/", query,
        f"content-type:application/json\nhost:{HOST}\nx-content-sha256:{payload_hash}\nx-date:{xdate}\n",
        signed_headers, payload_hash])
    scope = f"{short}/{REGION}/{SERVICE}/request"
    to_sign = "\n".join(["HMAC-SHA256", xdate, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    kdate = _hmac(sk.encode(), short)
    ksig = _hmac(_hmac(_hmac(kdate, REGION), SERVICE), "request")
    sig = hmac.new(ksig, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["Authorization"] = (f"HMAC-SHA256 Credential={ak}/{scope}, "
                                f"SignedHeaders={signed_headers}, Signature={sig}")
    req = urllib.request.Request(f"https://{HOST}/?{query}", data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--image"); p.add_argument("--audio")
    p.add_argument("--out", required=True)
    p.add_argument("--resume", help="已提交任务的 task_id;跳过提交直接轮询(不重复计费)")
    p.add_argument("--confirmed", action="store_true",
                   help="已向用户报过成本并拿到确认。**没有这个开关不会提交计费任务**")
    p.add_argument("--producer", default=os.environ.get("AIGC_PRODUCER", "zhimahang"))
    args = p.parse_args()

    # task_id 落盘:提交后进程若被杀(超时/Ctrl-C),任务仍在服务端跑、仍占并发槽、
    # 仍计费,但 task_id 只在 stdout 里就永久丢了——只能干等槽位释放再重交一次,
    # 白花一次钱。故提交成功立刻写 sidecar,并在启动时自动续跑。
    sidecar = args.out + ".task"
    task_id = args.resume
    if not task_id and os.path.exists(sidecar):
        task_id = open(sidecar).read().strip() or None
        if task_id:
            print(f"发现未完成任务 {task_id}(来自 {sidecar}),直接续跑,不重复提交")
    if not task_id:
        if not (args.image and args.audio):
            sys.exit("首次提交需要 --image 与 --audio(续跑用 --resume <task_id>)")
        # 计费闸门:CVSubmitTask 是本技能唯一会花钱的调用。SKILL.md 的「确认门 C」是
        # 写给执行者的纪律,纪律会漏,所以这里再加一道机器闸——没有 --confirmed 就不提交。
        if not (args.confirmed or os.environ.get("DHV_CONFIRMED") == "1"):
            dur = ""
            try:
                import subprocess as _sp
                _local = args.audio if os.path.exists(args.audio) else None
                if _local:
                    _d = _sp.run(["ffprobe","-v","error","-show_entries","format=duration",
                                  "-of","csv=p=0",_local], capture_output=True, text=True).stdout.strip()
                    if _d: dur = f",本段音频 {float(_d):.1f} 秒 ≈ ¥{float(_d):.1f}"
            except Exception:
                pass
            sys.exit("⛔ 未确认,不提交。\n"
                     f"   这一步会调用 CVSubmitTask 并计费(数字人 1 元/秒{dur})。\n"
                     "   请先把分镜与成本表报给用户、拿到明确的「确认/开做」,\n"
                     "   再加 --confirmed 重跑(或设 DHV_CONFIRMED=1)。")
        r = signed_post("CVSubmitTask", {"req_key": req_key(), "image_url": args.image, "audio_url": args.audio})
        if r.get("code") != 10000:
            sys.exit(f"提交失败 code={r.get('code')} msg={r.get('message')} request_id={r.get('request_id')}")
        task_id = r["data"]["task_id"]
        with open(sidecar, "w") as f:
            f.write(task_id)
        print(f"task_id={task_id} 已提交(已存 {sidecar}),RTF≈20,15s 段约 5 分钟…")

    # 隐式标识(《人工智能生成合成内容标识办法》)经查询接口 req_json 注入
    aigc = json.dumps({"aigc_meta": {"content_producer": args.producer,
                                     "producer_id": f"dhv-{task_id}",
                                     "content_propagator": args.producer,
                                     "propagate_id": f"dhv-{task_id}"}})
    while True:
        time.sleep(20)
        q = signed_post("CVGetResult", {"req_key": req_key(), "task_id": task_id, "req_json": aigc})
        if q.get("code") != 10000:
            sys.exit(f"查询失败 code={q.get('code')} msg={q.get('message')} request_id={q.get('request_id')}")
        st = q["data"]["status"]
        print(f"  status={st}")
        if st == "done":
            url = q["data"].get("video_url")
            if not url: sys.exit(f"done 但无 video_url: {q}")
            print(f"  aigc_meta_tagged={q['data'].get('aigc_meta_tagged')}")
            urllib.request.urlretrieve(url, args.out)   # URL 仅 1 小时有效,立即落盘
            if os.path.exists(sidecar): os.remove(sidecar)   # 完成即清,免得下次误续跑
            print(f"已下载 → {args.out}"); return
        if st in ("not_found", "expired"):
            sys.exit(f"任务异常 status={st}")

if __name__ == "__main__":
    main()
