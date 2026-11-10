# 中文站、英文站与多语言

方法两种语言通用：先回答、按意图写、证据可核对、说法合口径。差别在搜索引擎、读者的叫法和几条本地规则。

## 1. 共通

- **两种叫法都写**：专家和新手对同一个东西常用不同的词（Google《SEO 新手指南》，2025-12-10），读者的叫法和工程师的叫法各写一次，在首段或问答里自然带出，不堆。
- **术语第一次出现写全称**，后面用简称。
- **形容词换成事实**：「高品质」「领先」「cutting-edge」「premium」没有数据就删，换成参数、条件和出处。
- **型号照规格书写**：大小写、连字符、空格照搬，不在型号里加备注（「(弯脚)」「短体」放到别的字段）。
- **单位和符号写对**：Ω、μH、°C、±，写法和规格书一致，全页统一；范围写全，例如 −40 °C 到 +85 °C / −40 °C to +85 °C。写错单位是 Google 自查问题里「拼写或格式问题」的典型。
- **每种语言给全文**：交用户审的多语言稿，每种语言都给完整正文，不只给摘要。

## 2. 中文站（以百度为主）

- 按百度的优质内容四维度（出身、颜值、内涵、口碑）和低质清单自查，见 `review-checklist.md` 第 3 节。
- 细雨算法（2019-11，针对 B2B）的几条写进习惯：标题和正文不写「官网」；不堆相近关键词；联系方式正常写，不用拆字、谐音、插空格的变形写法；产品图配文字说明。
- 语句要通顺：机器翻译或 AI 写出的句子，人工读一遍再发；「语句不通」「内容拼接」是百度《违规低质页面问题说明》（2023-08-29）点名的问题。
- 同义叫法写法示例：网络变压器（也叫网口变压器、以太网变压器）。
- 短句、主语明确、术语全文统一（经验）。避免空话套话（「赋能」「打造一站式」），它们不回答任何问题。
- 中文搜索数据多数来自下拉词和相关搜索，不能当搜索量用；选词归 seo-keyword-research。

## 3. 英文站（以 Google 为主）

- 按 Google 自查问题和警示信号自查，见 `review-checklist.md` 第 2 节。
- 用买家的叫法做主题词，工程师的叫法作同义叫法，例如 Ethernet transformer（also called LAN transformer）。叫法定哪个归 seo-keyword-research / seo-keyword-mapping。
- 身份和合规用词要准：manufacturer、distributor、supplier 按事实和口径；certified 只用于有证书的；有第三方检测报告的写 compliant (third-party test report)，按口径文件的原句。
- 替代类固定写法：replacement candidate; verify with samples before production。不写 pin-to-pin、drop-in、direct replacement、fully compatible（口径登记的例外除外）。
- 评测、对比类内容参考 Google《写出高质量评测》（2025-12-10）；Google 的评价系统支持英语、法语、德语等语言，不含中文和日文（同上，截至 2026-10）。
- 短句、主动语态、一段一个意思（经验）。

## 4. 翻译与多语言版本

- **AI 初译，母语审校**：统一型号和术语写法后才上线。机翻整站没人校对，是 Google 列的滥用规模化内容情形之一。
- **一页一种语言**：标题是法语、正文是英文的页面，正文翻成法语并请母语人员审校，审完前不上线。
- **译文和源文同步更新**：源文改了参数，所有语言版本一起改；术语用统一的对照表。
- **译文里的链接**：目标页要有这个语言的版本，没有就链到有该语言版本的页面。
- **小语种页面只有界面是译文、正文还是源语言的**：在交付里写明，不当成已完成。
- **同一公司的不同语言站各自维护内容时**：公司层面的事实（法定名、地址、成立年份、证书）必须一致，按实体信息表（`ai-citable-writing.md` 第 4 节）；各站的对外口径可以不同，各按各站的口径文件，不互相照搬。
- 标题、描述在小语种里常偏长，长度归 seo-tdk-page-elements。

## 5. 常用说法中英对照

| 中文 | English |
|---|---|
| 替代候选型号 | replacement candidate |
| 量产前请用样品验证 | verify with samples before production |
| 主要参数对照见下表 | key parameters are compared in the table below |
| 测试条件 | test conditions |
| 出处：规格书第 2 页（Rev.C） | Source: datasheet Rev.C, p. 2 |
| 发布于 / 更新于 | Published on / Updated on |
| 更新记录 | Revision history |
| 技术内容经应用工程师审核 | Technical content reviewed by an application engineer |
| 初稿由 AI 辅助整理，参数经人工逐项核对 | Drafted with AI assistance; every parameter checked by hand |
| 符合 RoHS（第三方检测报告） | RoHS compliant (third-party test report) |
| 部分型号 | selected models |
| 已停产，替代候选见下表 | Discontinued; see replacement candidates below |
| 待核实（稿件内部标记，不上线） | [TO VERIFY] (internal draft mark, never published) |

样品、交期、回复时效这类固定句，以口径文件里两种语言的登记原句为准；口径文件只有一种语言的，另一种语言留占位交用户，不自己翻一句凑上。
