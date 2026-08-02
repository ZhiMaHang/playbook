#!/usr/bin/env python3
"""口播稿 → 分段配音(≤15s/段)+每段字幕。
输入格式:Markdown,以「## 段N」为分镜段落边界,段内纯口播文本(≤65 汉字,约 13~15 秒)。
优先豆包语音合成(TTS_APPID/TTS_TOKEN,字节系,按字符计费,60s 稿 <1 元);
未配置则降级 edge-tts(免费,仅用于跑通流程,正式片建议豆包声)。
用法: python3 tts.py --script 口播稿.md --outdir work/audio [--voice <voice_type>]
产物: seg_01.wav / seg_01.txt(该段文本,供字幕) …
"""
import argparse, base64, json, os, re, subprocess, sys, urllib.request, uuid, pathlib

CONF = os.path.expanduser("~/.config/zmh-dhv/env")
if os.path.exists(CONF):
    for _line in open(CONF):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k, _v)

def parse_segments(md: str):
    """按二级标题切节,只取「## 段N」节的口播正文;段前段后的表格/清单/分隔线一律不进段。"""
    segs = []
    for part in re.split(r"^##\s+", md, flags=re.M):
        m = re.match(r"段\s*\d+\s*\n(.*)", part, re.S)
        if not m:
            continue
        text = " ".join(l.strip() for l in m.group(1).splitlines()
                        if l.strip() and not l.strip().startswith(("#", ">", "<!--", "|", "-", "[")))
        if text:
            segs.append(text)
    if not segs: sys.exit("未解析到「## 段N」分镜段")
    for i, s in enumerate(segs, 1):
        if len(s) > 70: print(f"⚠️ 段{i} 共 {len(s)} 字,可能超 15 秒,建议拆分", file=sys.stderr)
    return segs

def doubao_tts(text, out_wav, voice):
    appid, token = os.environ["TTS_APPID"], os.environ["TTS_TOKEN"]
    body = {"app": {"appid": appid, "token": token, "cluster": "volcano_tts"},
            "user": {"uid": "dhv"},
            "audio": {"voice_type": voice, "encoding": "wav", "speed_ratio": 1.0},
            "request": {"reqid": str(uuid.uuid4()), "text": text, "operation": "query"}}
    req = urllib.request.Request("https://openspeech.bytedance.com/api/v1/tts",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer;{token}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read())
    if resp.get("code") != 3000:
        sys.exit(f"豆包TTS失败: {resp.get('code')} {resp.get('message')}")
    pathlib.Path(out_wav).write_bytes(base64.b64decode(resp["data"]))

PROXY_VARS = ("http_proxy", "https_proxy", "all_proxy",
              "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")

def edge_tts(text, out_wav, voice, no_proxy=False):
    """edge-tts 合成。no_proxy=True 时剥掉代理环境变量再跑。

    2026-08-02 实测:本机代理会拦 speech.platform.bing.com,带代理必失败、
    剥掉就通。但别的网络环境可能反过来(必须走代理才出得去),所以策略是
    「先按现状跑,失败再剥代理重试一次」,而不是无条件剥。
    """
    mp3 = out_wav.replace(".wav", ".mp3")
    env = os.environ.copy()
    if no_proxy:
        for v in PROXY_VARS:
            env.pop(v, None)
        env["NO_PROXY"] = "*"
    subprocess.run([sys.executable, "-m", "edge_tts", "--voice", voice,
                    "--text", text, "--write-media", mp3], check=True, env=env)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", mp3,
                    "-ar", "44100", "-ac", "1", out_wav], check=True)
    os.remove(mp3)

def say_tts(text, out_wav, voice="Tingting"):
    """三级兜底:macOS 本地 say(零网络;本机代理连 edge-tts 的微软端点都拦)。草稿用,正式片换豆包声。"""
    aiff = out_wav.replace(".wav", ".aiff")
    subprocess.run(["say", "-v", voice, "-o", aiff, text], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", aiff,
                    "-ar", "44100", "-ac", "1", out_wav], check=True)
    os.remove(aiff)

def edge_or_say(text, out_wav, voice):
    """edge-tts →(剥代理重试)→ macOS say。

    降级到 say 是**最后手段**:macOS 多数中文音色其实没随系统安装(实测
    Eddy/Reed/Rocko/Grandpa 产出 0 字节),真正可用的只有女声 Tingting——
    形象是男性时会造成明显的声画不符,这种片子不该直接发。故降级时显式警告。
    """
    try:
        edge_tts(text, out_wav, voice)
        return
    except subprocess.CalledProcessError:
        pass
    try:
        edge_tts(text, out_wav, voice, no_proxy=True)
        print("  edge-tts 带代理失败,剥代理后成功", file=sys.stderr)
        return
    except subprocess.CalledProcessError:
        pass
    say_voice = os.environ.get("SAY_VOICE", "Tingting")
    print(f"  ⚠️ edge-tts 两次均失败,降级 macOS say(voice={say_voice})。"
          f"注意音色可能与人物性别不符,正式片请勿直接使用", file=sys.stderr)
    say_tts(text, out_wav, say_voice)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--script", required=True); p.add_argument("--outdir", required=True)
    p.add_argument("--voice", default=None)
    a = p.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    segs = parse_segments(open(a.script, encoding="utf-8").read())
    use_doubao = bool(os.environ.get("TTS_APPID") and os.environ.get("TTS_TOKEN"))
    voice = a.voice or (os.environ.get("TTS_VOICE", "zh_female_shuangkuaisisi_moon_bigtts")
                        if use_doubao else os.environ.get("EDGE_VOICE", "zh-CN-XiaoxiaoNeural"))
    print(f"引擎={'豆包TTS' if use_doubao else 'edge-tts(降级)'} voice={voice} 共{len(segs)}段")
    for i, text in enumerate(segs, 1):
        wav = os.path.join(a.outdir, f"seg_{i:02d}.wav")
        (doubao_tts if use_doubao else edge_or_say)(text, wav, voice)
        pathlib.Path(os.path.join(a.outdir, f"seg_{i:02d}.txt")).write_text(text, encoding="utf-8")
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", wav], capture_output=True, text=True).stdout.strip()
        print(f"  seg_{i:02d}: {len(text)}字 {float(dur):.1f}s {'⚠️超15s' if float(dur)>15 else ''}")

if __name__ == "__main__":
    main()
