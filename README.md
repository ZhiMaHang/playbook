# playbook

智码航提示词工具集(Claude Code 插件)。

## 技能

### prompt-optimizer

把一句没说透的话整理成可直接发送的提示词,固定三步交互:

1. **消歧**:原话有实质歧义时,出选择题让你选定读法(末项"其他"可自由填写);
2. **两道选择题**:覆盖最影响产出的两个未述维度,每道末项"其他"可自由填写;
3. **纯拼接**:原话 + 你的各次选择拼成成品——不扩写、不套模版、不加角色、不编造背景;
4. **交付选择**:成品出来后你选——直接让当前会话照着执行,或自行复制发给目标对象。

面向所有工种,成品可直接发送。

### five-sentence-brief

把一件要办的事按"五句话说清楚"补全成任务简报,同样全程零猜测:

1. **槽位盘点**:我要做什么/给谁用/解决什么问题/基于什么资料/交付成什么样——已说的按原文归位;
2. **只问空槽**:缺的槽位出选择题(末项"其他"可自由填写),明确跳过的整行省略、绝不代填;
3. **五行拼装**:固定骨架 + 你的原话与选择,零扩写零编造;
4. **交付选择**:直接让当前会话照简报执行,或自行复制发送。

分工:只想把现成一句话理顺 → prompt-optimizer;要把一件事补全成结构化需求 → five-sentence-brief。

## 安装

```
/plugin marketplace add ZhiMaHang/playbook
/plugin install playbook@playbook
```

## 回归测试

改完 SKILL.md 后跑:

```
bash evals/run.sh          # 全部用例
bash evals/run.sh 消歧     # 按名字过滤
```

每个用例 = `evals/cases/<名字>/prompt.txt`(输入) + `rubric.md`(判分标准);脚本无头跑真插件、haiku 判分,全 PASS 退出码 0。只覆盖首轮行为,多轮交互(回"都行"、拒答、部分作答)仍需手测。

## 本地开发测试

不发布也能装,任选其一:

```
claude --plugin-dir /path/to/playbook          # 直接加载,免安装
/plugin marketplace add /path/to/playbook      # 走完整市场流程
/plugin install playbook@playbook
```
