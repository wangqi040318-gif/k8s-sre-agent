# K8s 故障诊断 Agent（传智杯 · 云原生/SRE 作品集骨架）

面向「告警 → 工具取证 → RAG Runbook → 结构化诊断报告」的最小可运行闭环。  
**默认 MOCK 模式**，无真实集群也能演示；配置 `KUBECONFIG` 后可切真实集群。

> **两周作品定位一句话**：做一个会查 K8s、会翻 Runbook、会出「需人工确认」处置建议的故障诊断 Agent，而不是又一个聊天框。

## 架构

```
告警/场景 ──► collect(取证) ──► retrieve(RAG) ──► diagnose(LLM 或规则回退)
                 │                  │                    │
            k8s_tools           runbooks/*.md      Markdown + JSON 报告
           (MOCK|live)         TF-IDF 本地索引
```

- **编排**：优先 [LangGraph](https://github.com/langchain-ai/langgraph) 三节点图；导入失败时自动回退纯 Python 状态机（同序：`collect → retrieve → diagnose`）。
- **RAG**：本地 TF-IDF（无 chromadb / 无外网），`python -m src.rag.ingest` 一键建索引。
- **LLM**：`OPENAI_API_KEY` + `OPENAI_BASE_URL`（兼容通义/DeepSeek/OpenAI）；无 Key 时规则模板填报告，保证 CLI/测试绿灯。

## 目录

见仓库内 `src/`、`runbooks/`、`demo/`、`prompts/`、`tests/`。

## 快速开始（MOCK）

```bash
cd /workspace/k8s-sre-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 可选：复制环境变量模板
cp .env.example .env

# 建索引 + CLI 诊断
python -m src.rag.ingest
MOCK=1 python -m src.cli --scenario imagepull

# 测试
pytest -q

# UI
MOCK=1 streamlit run src/ui/app.py
```

其他场景：`--scenario oom` / `--scenario crashloop`。

## 接真实集群

```bash
# kind 示例
# kind create cluster --name sre-demo
# minikube 示例
# minikube start

export KUBECONFIG=~/.kube/config   # 按实际路径
export MOCK=0
./demo/inject_faults.sh
MOCK=0 python -m src.cli --scenario imagepull --pod demo-imagepull
./demo/cleanup.sh
```

无 `kubectl` 或连不上集群时，demo 脚本会打印跳过说明并退出 0。

## 演示 5 分钟故事线

1. **开场（30s）**：说明 MOCK 可复现；打开 Streamlit 选 ImagePullBackOff。  
2. **跑诊断（1min）**：展示报告中的现象 / 根因 / 证据 /「需人工确认」命令。  
3. **换场景（1min）**：切 OOM、CrashLoop，强调证据与 Runbook 命中变化。  
4. **架构（1min）**：LangGraph 节点 + 本地 RAG + LLM/规则双路径。  
5. **真实集群彩蛋（1min，可选）**：`inject_faults.sh` → `MOCK=0` CLI；强调 Agent **不自动写集群**。  
6. **收尾（30s）**：作品集定位——可观测取证 + 知识检索 + 可控处置建议。

## 赛道提交注意

- 不要提交 `.env`、kubeconfig、API Key；只用 `.env.example`。  
- 演示优先 MOCK，评委环境无集群也能复现。  
- 报告中的 kubectl **写操作**必须保留「需人工确认」标注。  
- 写清个人贡献：工具设计、Runbook、Agent 编排、UI/演示脚本。

## 已实现 / 刻意简化

| 项 | 状态 |
|----|------|
| Mock 三场景一致证据 | ✅ |
| TF-IDF RAG | ✅（未强制 chromadb，减负且离线可用） |
| LangGraph + 状态机回退 | ✅ |
| 无 Key 规则诊断 | ✅ |
| Streamlit UI | ✅ |
| 真实 client 封装 | ✅（无集群不炸） |
| 多 Agent / 自动修复 | ❌ 刻意不做，聚焦诊断闭环 |
| 向量库云服务 | ❌ 本地检索即可 |

## 验证命令摘要

```bash
cd /workspace/k8s-sre-agent && source .venv/bin/activate
pytest -q
MOCK=1 python -m src.cli --scenario imagepull
# 本环境若无 python 命令，统一用 python3 / 已激活的 venv
```
