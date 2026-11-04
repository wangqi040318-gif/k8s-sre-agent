# Prompt 快照模板 01：脚手架诊断 Agent

> **模板，不是真实对话导出。**
>
> 下面的来回是按仓库里已经落地的设计整理的示例，用来说明快照应包含哪些内容。它不能当作你本人的聊天记录提交。
>
> 报名 **AI创新应用挑战赛** 时，请整文件替换成 Cursor、通义、DeepSeek、ChatGPT 等工具的真实导出，并删掉 API Key、kubeconfig、内网地址、cookie。
>
> **AI大模型创新应用挑战赛** 的通知文本没有强制要求本文件。留着模板不影响主赛道；不要在报名表里把它写成「原始开发记录」。

## 这次协作要解决的问题

作者希望先有一条离线能跑的诊断闭环：告警或场景进入后，程序取证、检索 Runbook、写出中文报告。没有集群、没有模型 Key 时也要能演示。

## 人类提示（示例）

```text
做一个 K8s SRE 故障诊断的最小 Agent，默认不要连接真实集群。

约束：
- 三条演示故障：ImagePullBackOff、OOMKilled、CrashLoopBackOff
- 编排用 LangGraph，三个节点：collect、retrieve、diagnose
- LangGraph 导入失败时，用同顺序的纯 Python 函数兜底
- Runbook 放在 runbooks/*.md，检索用本地 TF-IDF，不要上 chromadb，也不要在演示时访问外网
- 模型走 OpenAI 兼容接口。没有 OPENAI_API_KEY 时用规则模板出报告
- 报告用中文，故障名保留英文。所有写操作建议都标注需人工确认
- 这一阶段不要实现自动修复，诊断结束只打印报告
- 提供 CLI：python -m src.cli --scenario imagepull|oom|crashloop
- 环境变量写在 .env.example，不要生成 .env
```

## 助手回复要点（示例，已去掉任何密钥）

- 新增 `src/agent/state.py` 定义 `DiagnoseState`。
- 新增 `src/agent/graph.py`：`node_collect`、`node_retrieve`、`node_diagnose`，以及 `_run_langgraph` / `_run_state_machine`。
- 取证放在 `src/tools/k8s_tools.py`，`MOCK=1` 时读 `src/tools/mock_data.py`。
- 索引入口 `python -m src.rag.ingest`，检索 `retrieve_runbooks`。
- 系统提示放在 `prompts/system.md`，要求区分事实和推断，并且不编造证据里没有的集群事实。
- CLI 在调用模型前先 `build_index()`，并在 stdout 标明当前是规则回退还是已配置 LLM。

## 和当前代码的对应关系

| 示例里的决定 | 仓库中的位置 |
|---|---|
| 三节点，诊断图不含 apply | `src/agent/graph.py` 的 `StateGraph` |
| 无 Key 规则报告 | `_rule_based_report` |
| Mock 三场景证据 | `src/tools/mock_data.py` |
| 本地 TF-IDF | `src/rag/ingest.py`、`src/rag/retrieve.py` |
| CLI | `src/cli.py` |

## 替换成真实快照时保留的结构

1. 时间或会话标题。
2. 你的原话（可删敏感句，不要改技术约束的含义）。
3. 助手给出的文件级方案。
4. 你接受、拒绝或改写了哪几条。例如：若你否决了「诊断完直接 kubectl apply」，把这句留在快照里。
5. 对应的 commit id。本模板不填写虚构的会话时间。
