#!/usr/bin/env python3
"""原创环境背景音乐生成(纯代码合成,零成本、零版权风险)。

**为什么必须自己合成,不能找一首现成的**:口播视频要发到公开平台,用了受版权保护的
曲子就是侵权,平台的版权识别还会给视频打标记直接限流——两头都是硬伤。所谓「免费下载」
的素材站也多半只是「免费试听」,授权条款经常不允许商用或要求署名,核实成本比自己合成高。
本脚本产出的每一个采样点都是现算的,版权归使用方,可商用、可修改、无需署名。

音色设计(合成音听起来廉价通常是因为省了这几步,别删):
  · 每个音都由 3 个微失谐振荡器叠加(±4 音分),失谐产生自然的合唱感与宽度
  · 2.5 秒的慢起音包络,和弦是「涌」出来的而不是「按」出来的,也避免爆音
  · 一极低通把高次谐波滚掉,留下柔和的芯
  · 左右声道用不同的失谐与相位,拉开立体声宽度
  · 三个延迟抽头模拟空间残响,让声音有房间感而不是贴在耳朵上

用法:
  make_bgm.py --out bgm.wav --seconds 33
  make_bgm.py --out bgm.wav --seconds 33 --mood bright   # calm(默认) / bright / tense
"""
import argparse, math, wave, struct
import numpy as np

SR = 44100

# 和弦进行。用七和弦与九音而非三和音——三和音太"正",容易像手机铃声。
MOODS = {
    # 沉静、适合夜景与思考类内容(默认)
    "calm":   [[220.00, 261.63, 329.63, 493.88],   # Am(add9)
               [174.61, 220.00, 261.63, 329.63],   # Fmaj7
               [130.81, 164.81, 196.00, 246.94],   # Cmaj7
               [164.81, 196.00, 246.94, 293.66]],  # Em7
    # 明亮、适合正面结论与产品类内容
    "bright": [[261.63, 329.63, 392.00, 493.88],   # Cmaj7
               [196.00, 246.94, 293.66, 392.00],   # G
               [220.00, 261.63, 329.63, 415.30],   # Am(#9 色彩)
               [174.61, 220.00, 261.63, 349.23]],  # F
    # 紧张、适合提出问题与风险类内容
    "tense":  [[146.83, 174.61, 220.00, 261.63],   # Dm7
               [155.56, 185.00, 233.08, 277.18],   # Eb
               [146.83, 174.61, 207.65, 246.94],   # Dm(b5 色彩)
               [130.81, 155.56, 196.00, 233.08]],  # Cm7
}


def one_pole_lowpass(x, cutoff_hz):
    """一极低通。滚掉高次谐波,合成音才不刺耳。"""
    a = math.exp(-2.0 * math.pi * cutoff_hz / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc = (1 - a) * x[i] + a * acc
        y[i] = acc
    return y


def voice(freq, n, detune_cents, phase):
    """单个音:3 个微失谐振荡器 + 少量三次谐波(加暖度)。"""
    t = np.arange(n) / SR
    out = np.zeros(n)
    for k, d in enumerate((-detune_cents, 0.0, detune_cents)):
        f = freq * (2 ** (d / 1200.0))
        p = phase + k * 0.37
        out += np.sin(2 * math.pi * f * t + p)
        out += 0.12 * np.sin(2 * math.pi * f * 3 * t + p)   # 三次谐波,一点点就够
    return out / 3.0


def swell(n, attack, release):
    """慢起慢落包络。和弦要『涌』出来,不能是开关。"""
    env = np.ones(n)
    a, r = int(attack * SR), int(release * SR)
    a, r = min(a, n // 2), min(r, n // 2)
    # 用 1-cos 的半周期做 S 形,比线性自然
    env[:a] = 0.5 - 0.5 * np.cos(np.linspace(0, math.pi, a))
    env[n - r:] = 0.5 + 0.5 * np.cos(np.linspace(0, math.pi, r))
    return env


def space(x, taps=((0.131, 0.34), (0.269, 0.22), (0.413, 0.13))):
    """几个延迟抽头做空间感。不是真混响,但足以让声音离开耳朵、退到房间里。"""
    y = x.copy()
    for delay, gain in taps:
        d = int(delay * SR)
        if d < len(x):
            y[d:] += gain * x[:-d]
    return y


def build(seconds, mood):
    chords = MOODS[mood]
    bar = 8.0                                  # 每个和弦 8 秒,慢到不抢旁白
    total = int(seconds * SR)
    # 多留一段尾巴给延迟与释放,最后再裁回
    n = total + int(3 * SR)
    left = np.zeros(n)
    right = np.zeros(n)

    seg = int(bar * SR)
    i, idx = 0, 0
    while i < total:
        ch = chords[idx % len(chords)]
        # 和弦之间重叠 2.5 秒,交叠处形成自然的推移而不是硬切
        ln = min(seg + int(2.5 * SR), n - i)
        env = swell(ln, 2.5, 2.5)
        for vi, f in enumerate(ch):
            amp = 1.0 / (1.0 + 0.6 * vi)       # 高音渐弱,避免上方拥挤
            left[i:i + ln] += amp * voice(f, ln, 4.0, 0.0 + 0.5 * vi) * env
            right[i:i + ln] += amp * voice(f, ln, -4.0, 1.1 + 0.5 * vi) * env
        # 根音低八度做垫底,给整体一个地面
        sub = ch[0] / 2.0
        left[i:i + ln] += 0.5 * voice(sub, ln, 2.0, 0.3) * env
        right[i:i + ln] += 0.5 * voice(sub, ln, -2.0, 0.9) * env
        i += seg
        idx += 1

    # 一层极轻的滤波噪声当"空气",纯正弦叠出来的声音太干净,反而假
    rng = np.random.default_rng(7)
    air = one_pole_lowpass(rng.standard_normal(n) * 0.02, 900)
    left += air
    right += np.roll(air, 977)                 # 左右错开,噪声也有宽度

    left = space(one_pole_lowpass(left, 1800))
    right = space(one_pole_lowpass(right, 1800))

    left, right = left[:total], right[:total]
    peak = max(np.abs(left).max(), np.abs(right).max(), 1e-9)
    left, right = left / peak * 0.9, right / peak * 0.9
    # 整曲首尾各淡入淡出 1.5 秒,避免起播和收尾突兀
    f = int(1.5 * SR)
    ramp = np.linspace(0, 1, f)
    for ch in (left, right):
        ch[:f] *= ramp
        ch[-f:] *= ramp[::-1]
    return left, right


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--seconds", type=float, required=True)
    p.add_argument("--mood", default="calm", choices=sorted(MOODS))
    a = p.parse_args()

    l, r = build(a.seconds, a.mood)
    data = np.empty(len(l) * 2, dtype=np.int16)
    data[0::2] = np.clip(l * 32767, -32768, 32767).astype(np.int16)
    data[1::2] = np.clip(r * 32767, -32768, 32767).astype(np.int16)
    with wave.open(a.out, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(data.tobytes())
    print(f"背景音乐 → {a.out} ({a.seconds:.1f}s, {a.mood}, 原创合成,可商用无需署名)")


if __name__ == "__main__":
    main()
