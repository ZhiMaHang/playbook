# 站点档案（模板）

**本文件是空模板。** 首次使用本技能前，复制一份并填入你自己的站点信息。**放到插件目录之外**，否则插件升级时会连同缓存一起被替换掉：

```
mkdir -p ~/.config/zmh-marketing
cp references/sites.md ~/.config/zmh-marketing/sites.local.md
chmod 600 ~/.config/zmh-marketing/sites.local.md   # 里面通常有服务器地址与密钥位置
```

技能按 `~/.config/zmh-marketing/sites.local.md` → 本目录 `sites.local.md` 的顺序取第一个存在的；两者都被 gitignore，不会进版本库。

档案与仓库实际不符时以仓库为准，并回来修正本文件。

## 共用事实

- **生产服务器**：部署目标（ssh 别名或主机）、反代方案（Nginx/Caddy/OpenResty…）、各站是独立域名还是主域子路径、前缀是否剥离。
- **埋点**：分析平台与各自的衡量 ID（GA4 / 百度统计 / Plausible…），多站共用时用什么参数区分。
  > ⚠️ 衡量 ID、API key、服务器地址这类信息只写进 `sites.local.md`，不要写回本模板。
- **根域 robots.txt**：由哪个仓库产出，是否带 Content-Signal，是否列出各站 sitemap。
- **内容缓存**：页面 revalidate 策略与「改动多久可见」，验证整改时要按这个时间等。

## <站点标识>（一句话定位）

对每个站点复制一份下表：

| 项 | 值 |
|---|---|
| 线上 | 线上 URL（含各类跳转关系，如子域 301 到主域子路径） |
| 仓库 | 仓库路径 + 框架（如 Next.js 15 App Router） |
| basePath | 子路径前缀、语言 cookie 名 |
| 页面 | 路由清单 |
| SEO 文件 | sitemap / robots / layout metadata 各自的文件路径 |
| 内容来源 | 页面文案来自仓库硬编码、CMS API 还是数据库；**改线上文案该改哪里** |
| 部署 | 该站的部署命令与监听端口 |
| 表单 | 表单落库位置与区分字段（如有） |

## 收录渠道

逐个引擎写清「能不能脚本化、走什么通道、当前是否可用」：

- **Google**：Search Console 属性形式（域名属性/URL 前缀）、提交是否只能走浏览器。
- **Bing**：IndexNow 是否已部署（key 文件放在哪个仓库、托管路径），Webmaster 后台的操作方式。
- **其他引擎**：账号状态、配额、是否有备案/验证类阻塞。被挂起的引擎要写明**不要尝试提交**，避免每轮重复失败。

台账文件：记录「哪轮提交覆盖了哪些 URL」的日志路径。判断「提交过没有」之前**必须先读它**。

## 已知遗留

上一轮审计留下的未决项，每条写清现状与下一步。已解决的用删除线标注并写明解决时间，便于下轮快速跳过。
