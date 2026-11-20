# CAD Agent 会反复收到同一份观测吗？

公开操作链复核，2026 年 11 月 20 日。这里的「观测」指工具交给 AI 的当前模型信息，例如一张预览图或者一份几何检查结果。「重复」只在同一次会话里比较。开头供人看目标长什么样的题图与中途生成的成品预览分开统计。

**发现。** 部分实际运行过的公开 CAD Agent 会反复收到相同的模型预览和数字。在自动回传预览的基准里，有 705 张图与本次会话之前发过的图完全相同。重复特别集中在脚本报错后。另一组使用成品图较少的工作流显示出不同的行为。现有记录足以支持研究这种现象，尚不足以给出真实工业装配中可归因的等待时间或账单比例。

## 1. 有多少观测真的重复

取 CADGenBench 公共数据固定快照 `baselines/` 下全部 869 份对话，覆盖六种模型标识、84 个题号和 15 个发布目录。包含创建、编辑、不同后端、分片及重试，按归档纳入，未按任务得分筛选。把 869 份对话逐份对上 Git 对象校验值。按同一会话、同一轮次连接对话和 PNG。只统计满足三个条件的回执。成品等轴预览一栏明确写着已附图，消息末尾确有图片块，紧接这份反馈之后还有模型回复。公开对话隐藏了图片的 base64，原版程序先附自行生成的图，最后附自动等轴预览，并将同一份预览字节存到本轮 PNG。[固定数据](https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-submissions/tree/d12435f55a6cf5d1ce15443361abb3f595a52908/baselines) · [基线程序](https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/src/cadgenbench/baseline/agent.py)

| 计数单位 | 结果 |
| --- | --- |
| 归档预览 / 有下轮回复的等轴图回执 / 可匹配到原图 | 2,188 / 1,852 / 1,852。另外 667 条明确写着自动渲染失败或跳过，其中 22 条附有别的图片，均未算作自动预览 |
| 同一会话再次出现相同 PNG | 705 / 1,852，38.07%。612 次与紧邻上一轮相同 |
| 图片和随图自动校验的整段正文一起重复 | 698 / 1,852，37.69%。图片与数字必须同时匹配同一会话的同一个先前回合。报错内容单独保留 |
| 分脚本执行结果 | 报错 367 / 421，87.17%。执行成功 338 / 1,431，23.62% |
| 一次会话算一票 | 651 份会话至少有一张匹配图，其中 244 份发生重复。会话内重复比例的均值 19.20%，中位数 0% |
| 分模型及稳定性 | 六种模型的逐图比例为 7.36% 到 63.24%，任务、后端和回合数不平衡。剔除上述 22 条易混回执所在的 15 份会话后为 702 / 1,841，即 38.13%。按整份对话字节去重后仍为 705 / 1,852 |

用归档图片的 SHA-256 核对是否相同。对非 LFS 小图实际下载并计算，对示例的两张图都下载验算。以题号为整体重采样 5,000 次，逐图重复比例的第 2.5 至 97.5 百分位为 34.0% 到 42.0%。它描述这批题目选择对结果的影响，工业人群的抽样误差仍未知。1,852 张图不应当作 1,852 个独立实验。

看第 101 题的归档第 5、6 轮（原始编号从 0 开始）。第 5 轮成功导出零件，第 6 轮脚本抛出 Python 错误。旧 STEP 还在目录里，于是两轮回传了相同的体积、外形尺寸和图片。两张实际下载的 PNG 的 SHA-256 均为 `326e6088f0337933f5783ba1e41e7d7e57dd1f757c415a6f12df0bfb44919e39`。第 6 轮的错误消息提供了新信息。[原始对话](https://huggingface.co/datasets/HuggingAI4Engineering/cadgenbench-submissions/blob/d12435f55a6cf5d1ce15443361abb3f595a52908/baselines/baseline_v1_claude_fable_5_20260610_091106/claude-fable-5/shards/shard-000/20260610_091209_claude-fable-5/101/conversation.json)

**文字的实际比重。** 同批对话里共提取到 2,519 份成品自动校验摘要，其中 1,854 份显示 `Valid: True`。765 份校验通过的摘要与同一成品文件名的一份先前摘要逐字相同。按固定分词器 `cl100k_base`，这 765 段正文共 41,452 个代理 token。所有每轮新送达的反馈文字共 2,003,924 个代理 token，占比约 **2.07%**。正文单独分词，比例为近似值。另在固定对话中，把这 765 段正文各记成一条带原轮次的短引用，对整条文本块重新分词，体量差为 36,097 个，即 **1.80%**。这只是固定文本的离线计量。分母排除了图片、系统提示、模型回答及历史前缀重新计入的成本。相同面数、体积和外形尺寸仍允许零件内部不同，不能用这些字段断言整个模型没变。

更严格的资料核验也显示，比对对象很重要。在 1,612 个同轮有 STEP 留档的图片回执里，477 次的 STEP 内容键已在同会话出现过。与该键最近一次对应图比较，432 张图片相同，另外 45 张下载后解码到 RGBA，像素或尺寸确有不同。我们没有据此推断差异成因。

## 2. 重复观测是否是时间和钱的大头

| 来源 | 怎样计量 | 能回答的范围 |
| --- | --- | --- |
| Parametric CAD Bench v3 | 完整表有 13 套系统 × 100 题。另读相同 30 道创建后编辑题的四套系统，共 120 条实际轨迹。Antigravity + Gemini 3.8 Flash 的 29 题有命令明确重开成品检查或试改，总计 79 条，含 15 条非零退出。各题把工具区间取并集后相加，39 秒 / 31,677.64 秒 Agent 执行时长，约 **0.12%** | 起止时间精确至整秒。整条命令包括可能的试改和重算。这一口径看不到生成脚本内置的检查、临时模型、模型读结果和推理的时间 |
| 同组 Gemini 的输入 | 上述 79 条回执的文字为 27,849 / 全部 2,671 条工具回执的 663,994 个代理文字 token，约 **4.19%**。逐题中位数为 3.54%。该组 30 题公开记录共 207,760,997 输入 token，其中缓存 189,043,881，输出 4,134,588，记录金额 **43.72 美元** | 4.19% 包含首次与再次检查，每条新回执只计一次。整题费用来自作者运行记录，缺少观测项目的单独金额 |
| 同 30 题的三个 Codex 配置 | Astra 有 21 题回传过图片，共 25 张。Sol 有 4 题，共 14 张。Terra 为 0 题。39 张都检查了文件哈希及解码后的像素，同一会话重复数为 0 | 同样的题在不同模型上，主动看图的次数有很大差别。这里测的是新增的工具回图，未计历史图片反复进入上下文 |
| FunCAD 图 9b | 四个模型在 41 个匹配任务族上，预览渲染占已记录标准工具活跃时间约 **0.94%–1.43%**，几何检查约 **2.86%–6.38%**，仿真与探测约 **75.08%–84.99%** | 从图 9b 的矢量矩形读数，按题等权，跨类别同时运行的区间单列。分母排除模型推理、读图和人的等待。正文、图注和附录计时段为 41，附录 E.6 的样本选择段另写 46。作者在 14 个计时完整的任务族上也得到仿真占比最大的结果 |
| RealCADBench 附录 A.8 表 12 | 25 道从产品实图重建装配的题。Codex + GPT-5.5 的 25 条有记录，平均 12.12 分钟和 24.49k 个论文口径 token。Claude Code + Opus 4.8 有记录的 23 条平均 31.57 分钟和 47.14k，另外两条缺失。保留有时间但未导出成品的案例 | 证明装配整题可耗费数十分钟。表格没有把总量拆到每次观测。作者也没有在这里计量工程师被阻塞多久 |

上述不同分母分别回答「收到什么」「工具跑多久」「整道题花多少」。把同一耗时同时记在两个并行步骤上会双算，把整次模型调用都归因给最后一张图也会高估。原始依据见 [Parametric CAD Bench v3 原文](https://arxiv.org/abs/2611.02659) · [固定轨迹与汇总表](https://huggingface.co/datasets/gnucleus-ai/cad-gen-freecad-bench-v3/tree/114f19d80b6b72763a0d7e2681148417081bbe64) · [FunCAD 图 9 和附录 E.6](https://arxiv.org/html/2610.26861v1#S5.F9) · [公开版本的发布范围](https://github.com/thegenlab/fun-CAD-harness) · [RealCADBench 表 12](https://arxiv.org/html/2609.03773v1#A1.T12)。

## 3. 近年的 Agent 怎样「看」，问题还剩在哪里

- **看画面。** CADWorld 的 GUI Agent 看 FreeCAD 屏幕来定位和操作。CADGenBench 的这套基线在成品存在时会自动附一张斜着看的预览。两类图分别回答「屏幕在哪里可点」和「刚导出的零件长什么样」。[CADWorld 观测协议](https://arxiv.org/html/2609.16251v2) · [CADGenBench 原版实现](https://github.com/huggingface/cadgenbench/blob/33304cf771fc5639144b1df9611e347251052cf8/src/cadgenbench/baseline/agent.py)
- **读数字、关系或物理测试。** AgenticCADedit 在 `run_step` 后给几何摘要，可按需列面和边、查看带高亮的图。DeciCAD 返回操作后果，还能按需查对象、约束和依赖。FunCAD 用程序与物理仿真判断机构能否工作。GOPR-CAD 逐步构造操作图，交给确定性编译器生成几何，采用另一种操作链。[AgenticCADedit §3.2](https://arxiv.org/html/2609.29621v1) · [DeciCAD §3.3](https://arxiv.org/html/2610.21755v1) · [FunCAD §5.4](https://arxiv.org/html/2610.26861v1) · [GOPR-CAD 原文](https://arxiv.org/abs/2611.11470)
- **已有改善证据，也有与新意直接相关的前人工作。** DeciCAD 表 2 在文本生成零件题上，把面向下一步决定的结构化观测与原始状态全量反馈对照。每题平均输入由 345 万变为 39.054 万，几何 F1 由 21.62 到 43.63。这个对照包含观测内容、查阅方式以及后续行为。通用 GUI 的 ReVision 已研究相邻截屏冗余，工业 Agent 也已有工作研究旧工程证据在新版本中是否仍适用。因而值得追问的是特定 CAD 装配中重复跨轮观测有多常见、对工程判断和实际等待有何影响。[DeciCAD 表 2](https://arxiv.org/html/2610.21755v1#S4.T2) · [ReVision](https://www.microsoft.com/en-us/research/publication/revision-scaling-computer-use-agents-via-temporal-visual-redundancy-reduction/) · [Trustworthy Execution of Industrial Agents](https://arxiv.org/abs/2611.02300)
- **图和账单。** 图片可消耗输入 token，具体随模型、尺寸及细节级别而异。同一历史前缀命中缓存、输出长度和模型调用轮数都能改变账单。AgenticCADedit 的 192 题对照提供了直观例子。其新循环输入共 301.37M，旧循环 99.39M，但输入缓存命中比例分别为 94.8% 和 0.2%，输出也减少，总费用分别为 11.00 和 26.30 美元。[图片计量文档](https://developers.openai.com/api/docs/guides/image-cost-calculator) · [AgenticCADedit §6.5](https://arxiv.org/html/2609.29621v1)

最接近装配场景的数据分两类。CADWorld 提供 25 道装配题及初始、参考文件。归档中已备好 003（两面间距 5 mm）、016（销的方向和固定）与 005（多螺钉关节）作为可核查的例子。这里核验了六份原生文档的 ZIP 完整性，没有在三题上新跑模型。原题要求使用 FreeCAD GUI。Multi-Agent CAD 的作者还公开了四个精选复杂装配案例按规划、生成与修复、评判及其他工作的 token 汇总，最后一栏混合多种开销。Autodesk Fusion 360 Gallery 能提供真实装配及关节，人机协作研究也有工程师的 Onshape 行为记录。在所查资料的公开入口里，我没有找到在同一条工业装配轨迹中同时对齐「中途观测原文或图片、逐步计时、模型用量和人的等待」的日志。[CADWorld 数据](https://huggingface.co/datasets/Zihan1004/CADWORLD/tree/fa02aa03b944daf2ae5b23e56d135bb7cbb75994) · [复杂装配用量](https://github.com/Pan-Chera/Multi-Agent-CAD/blob/fa279076cf412d90533144702f9b11bc953fa989/README.md#complex-gallery-generation-usage) · [Fusion 360 Gallery](https://github.com/AutodeskAILab/Fusion360GalleryDataset) · [人机同步协作研究](https://doi.org/10.1017/dsj.2026.10073)

在 DBCAD 科研版的 AgenticCADedit 适配器内，成功的 `run_step` 自动执行 `inspect()` 并随结果附回模型摘要，哪怕这步只读。`render_views` 则由工具调用触发。项目里已经有一个具体可追踪的问题：在真实多步装配的自动摘要里，哪些信息在这一步确实变了，哪些是上一步已经知道的？若未来想把发现进一步写成「人等待的主要原因」，还需要界面时间线上用户发出请求、结果可用且用户能继续工作的记录。当前数字支持测量该问题，也容许最终测出实际代价很小。[本地研究适配器](../../DBCAD-mac6/code/CAD_DB/backend/app/ai_cad_facade.py#L155)

## 4. 复算及逐条核对

在本目录执行以下命令。已用 Python 3.12 运行。首次运行需联网取得固定快照及分词词表。脚本使用标准公开数据接口，复算本身不调用付费模型。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install duckdb==1.4.4 tiktoken==0.9.0 pdfplumber==0.11.9 Pillow==13.0.0 lxml==6.1.1
.venv/bin/python analyze.py
```

可用 `--out` 指定另一输出目录，`--cache` 指定原始数据缓存目录，`--funcad` 指定论文 PDF。默认复用本工作区 Release 的 FunCAD PDF，若缺失则下载固定的 arXiv v1。其 SHA-256 记录于汇总文件。两组逐轮数据和 CADWorld 固定到下方文件及代码内的修订，不跟随排行榜刷新。

| 文件 | 核对方式 |
| --- | --- |
| [summary.json](summary.json) | 汇总计数、时间、文字计量及按题重采样区间 |
| [cadgen_feedback.csv](cadgen_feedback.csv) · [cadgen_validation.csv](cadgen_validation.csv) | 每张匹配图的路径、摘要及前次轮号，每个数字校验块的校验值、长度及重复位置 |
| [cadgen_runs.csv](cadgen_runs.csv) · [cadgen_by_model.csv](cadgen_by_model.csv) | 每份对话的分母、缺失与重复，按模型单列 |
| [v3_trials.csv](v3_trials.csv) · [v3_saved_model_checks.csv](v3_saved_model_checks.csv) · [v3_image_returns.csv](v3_image_returns.csv) | 120 份轨迹、79 个显式成品检查命令及 39 个图片回执。可沿 `raw_directory` 和 `call_id` 回原轨迹核对 |
| [v3_by_system_workflow.csv](v3_by_system_workflow.csv) · [assembly_paper_rows.csv](assembly_paper_rows.csv) · [funcad_figure9b.csv](funcad_figure9b.csv) | 13 × 3 个系统与流程组合、25 个装配 × 两套 Agent 原表、功能机构图 9b 读数 |
| [cadworld_tasks.csv](cadworld_tasks.csv) · [cadworld_seed](cadworld_seed) | 25 题目录与三个已核验样本，原数据的 `LICENSE` 在样本目录中 |
