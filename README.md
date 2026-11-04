# K8s 故障诊断 Agent（传智杯 · 云原生/SRE 作品集骨架）

面向「告警 → 工具取证 → RAG Runbook → 结构化诊断报告」的最小可运行闭环。  
**默认 MOCK 模式**，无真实集群也能演示；配置 `KUBECONFIG` 后可切真实集群。

> **两周作品定位一句话**：做一个会查 K8s、会翻 Runbook、会出「需人工确认」处置建议的故障诊断 Agent，而不是又一个聊天框。

## 架构

```
告警/场景 ──► collect(取证) ──► retrieve(RAG) ──► diagnose(LLM 或规则回退)
                 │                  │                    │
            k8s_tools           runbooks/*.md      Markdown + JSON 报告
           (MOCK|live)         TF-IDF 本地索引     remediation 提案
                                                          │
                                          人工确认（YES / CONFIRM）
                                                          │
                                              apply：MOCK 模拟 | 真实集群
```

- **编排**：优先 [LangGraph](https://github.com/langchain-ai/langgraph) 三节点图；导入失败时自动回退纯 Python 状态机（同序：`collect → retrieve → diagnose`）。诊断图里没有 apply 节点，报告生成后不会自动改集群。
- **RAG**：本地 TF-IDF（无 chromadb / 无外网），`python -m src.rag.ingest` 一键建索引。
- **LLM**：`OPENAI_API_KEY` + `OPENAI_BASE_URL`（兼容通义/DeepSeek/OpenAI）；无 Key 时规则模板填报告，保证 CLI/测试绿灯。

## 目录

见仓库内 `src/`、`runbooks/`、`demo/`、`prompts/`、`tests/`。

## 快速开始（MOCK）

```bash
cd /workspace
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

## 人工确认后执行修复

诊断**只提案、不改集群**。报告里的 `remediation` 是动作列表，每条包含：

| 字段 | 含义 |
|------|------|
| `id` | 稳定动作 id，apply 时用它指定要执行哪一条 |
| `description` | 这条修复在做什么 |
| `command` | 等价的 kubectl，仅供阅读；程序不会把整段文本丢进 shell |
| `api_op` | 真正执行时使用的结构化 API 操作 |
| `risk_level` | `low` / `medium` / `high` |
| `reversible` | 该变更是否可以再改回去（回滚仍需人工确认） |

没有 `--confirm YES`，也没有在提示里输入 `CONFIRM` 时，apply 会拒绝，并且**不会**改 Mock 状态或真实集群。未知动作 id 同样拒绝。每次尝试（含拒绝）都写入审计日志，logger 名为 `src.remediation`，CLI 打到 stderr。

### CLI

```bash
# 1. 只诊断（默认）。输出里会有「修复方案（需人工确认）」
MOCK=1 python -m src.cli --scenario imagepull

# 2. 非交互确认。--confirm 只接受大写 YES
MOCK=1 python -m src.cli --apply --confirm YES --action fix-image --scenario imagepull

# 3. 交互确认。终端会提示「输入 CONFIRM 后执行」，必须原样输入 CONFIRM
MOCK=1 python -m src.cli --apply --action fix-image --scenario imagepull
```

| 场景 | 动作 id | 确认后做什么 |
|------|---------|----------------|
| imagepull | `fix-image` | 把容器镜像改为 `nginx:1.27`。属于 Deployment 时更新模板并删除异常 Pod；独立 Pod 则按新镜像重建 |
| oom | `bump-memory` | 把容器 memory limit 上调到 `256Mi` |
| crashloop | `fix-command` | 把启动命令改为健康休眠，并移除 liveness/readiness/startup 探针（若存在） |

`MOCK=1`（默认）时，确认后的 apply 只打印将要执行的命令，并更新进程内的 Mock 状态，不需要 kubeconfig。

### Streamlit

诊断结果下方列出每条动作的勾选框、命令和风险。文本框标签为「输入 CONFIRM 后执行」。**「执行修复」按钮在输入 `CONFIRM` 之前保持禁用。**

```bash
MOCK=1 streamlit run src/ui/app.py
```

### 真实集群（MOCK=0）

确认之后才进入写路径，优先使用官方 Python 客户端：

- 能定位到所属 Deployment：对模板做 JSON Patch（镜像、memory limit 或 command），再删除异常 Pod，让控制器重建。
- 独立 Pod（`demo/manifests` 就是这种）：删除并用修正后的规格重建。
- 客户端不可用、但本机有 `kubectl` 时，才退回到参数数组形式的 kubectl（`shell=False`），并且仍然只在已确认后执行。

请只在演示命名空间操作。没有 kubeconfig 时保持 `MOCK=1`。

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
5. **确认后修复（1min）**：指出报告里的 `fix-image`。先不加 `--confirm` 展示拒绝；再 `--confirm YES`，MOCK 下看到「已模拟执行」。强调默认不写集群。  
6. **真实集群彩蛋（可选）**：`inject_faults.sh` → `MOCK=0` 诊断；若要改集群，必须再带 `--confirm YES`。  
7. **收尾（30s）**：作品集定位——可观测取证 + 知识检索 + 人工确认后的可控处置。

## 赛道提交注意

- 不要提交 `.env`、kubeconfig、API Key；只用 `.env.example`。  
- 演示优先 MOCK，评委环境无集群也能复现。  
- 报告中的 kubectl **写操作**必须保留「需人工确认」。默认路径不会 apply。  
- 写清个人贡献：工具设计、Runbook、Agent 编排、确认门闩、UI/演示脚本。

## 已实现 / 刻意简化

| 项 | 状态 |
|----|------|
| Mock 三场景一致证据 | ✅ |
| TF-IDF RAG | ✅（未强制 chromadb，减负且离线可用） |
| LangGraph + 状态机回退 | ✅ |
| 无 Key 规则诊断 | ✅ |
| Streamlit UI | ✅ 提案 + CONFIRM 后才可点「执行修复」 |
| 真实 client 封装 | ✅（无集群不炸；确认后可 patch / 重建） |
| 人工确认后的修复 | ✅ 默认拒绝；`YES` 或 `CONFIRM` 才执行；MOCK 可离线模拟 |
| 诊断后自动修复 | ❌ 刻意不做 |
| 向量库云服务 | ❌ 本地检索即可 |

## 验证命令摘要

```bash
cd /workspace && source .venv/bin/activate
pytest -q
MOCK=1 python -m src.cli --scenario imagepull
MOCK=1 python -m src.cli --apply --action fix-image --scenario imagepull   # 应拒绝
MOCK=1 python -m src.cli --apply --confirm YES --action fix-image --scenario imagepull
# 本环境若无 python 命令，统一用 python3 / 已激活的 venv
```
