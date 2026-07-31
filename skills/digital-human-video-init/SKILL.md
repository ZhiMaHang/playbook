---
name: digital-human-video-init
description: 初始化数字人视频技能的凭据与配置:收集火山引擎 API Key(VOLC_AK/SK)、可选豆包 TTS 凭据、素材服务器配置,写入本机配置文件并做零成本连通性验证。当用户要"初始化视频技能/配置视频 API key/换视频密钥"时使用。只在本轮首次配置或换钥时用;日常做视频用 digital-human-video。
---

# 数字人视频技能 · 初始化

为 `digital-human-video` 技能准备凭据与配置。配置文件:`~/.config/zmh-dhv/env`(chmod 600,shell env 格式,制作技能的所有脚本会自动读取)。

## 流程

1. **收集凭据**(让用户在聊天里逐项提供;缺哪项问哪项,已配置项显示「已配置」并询问是否覆盖):
   - `VOLC_AK` / `VOLC_SK`(必填):火山引擎控制台 → 访问控制 → API 密钥管理。提醒用户:企业主账号的 AK/SK 权限很大,若在意最小权限,可建 IAM 子用户只授权视觉智能(cv)服务后用子用户密钥。
   - `TTS_APPID` / `TTS_TOKEN`(可选):豆包语音合成(语音技术控制台)。不配则制作技能自动用免费 edge-tts。
   - `TTS_VOICE`(可选):豆包音色代号,默认 zh_female_shuangkuaisisi_moon_bigtts。
   - `ZMH_ASSET_SSH` / `ZMH_ASSET_DIR` / `ZMH_ASSET_URLBASE`(必填):素材临时上传的服务器与公网 URL 前缀(即梦 API 只收公网 URL)。默认 SSH 目标 `zmh`;目录与 URL 前缀须与用户确认真实站点路径,不得瞎猜。
   - `AIGC_PRODUCER`(可选):隐式 AI 标识里的服务商标识,默认 zhimahang。

2. **写配置**:写入 `~/.config/zmh-dhv/env`,`chmod 600`。**密钥值不回显、不写入任何 git 仓、不进对话记录以外的文件**。已有文件先备份为 `env.bak`。

3. **零成本验证**:`python3 scripts/validate.py`(在本技能 scripts/ 下)。它用假 task_id 调一次 CVGetResult——不产生任何生成费用:
   - 返回鉴权类错误 → AK/SK 不对,让用户重查;
   - 返回「服务未开通」类错误 → 密钥有效但即梦AI「数字人快速模式」还没开通,引导用户去控制台开通(条款须用户本人点);
   - 返回 task 不存在/参数类业务错误 → **鉴权与服务通,初始化成功**。
   若配置了 TTS,再跑 `python3 validate.py --tts`,合成一个字验证(费用忽略不计)。
   若配置了素材服务器,`ssh` 测目录可写、`curl -I` 测 URL 前缀可达。

4. **收尾**:报告各项状态(✅/⚠️);提醒余额(数字人 1 元/秒,余额在 console.volcengine.com 首页看);之后用 `digital-human-video` 技能做视频。

## 红线

- 绝不主动向用户索要密码/登录凭据;只收 API 密钥这类用户主动交给自动化用的凭据。
- 密钥只落在 `~/.config/zmh-dhv/env`(600 权限)一处;报告状态时只说「已配置」,不复述密钥内容。
- 验证阶段禁止调用任何计费生成接口(CVSubmitTask 一次都不许)。
