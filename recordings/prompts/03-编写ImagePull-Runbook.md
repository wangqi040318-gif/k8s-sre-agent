# Prompt 快照模板 03：编写 ImagePull Runbook

> **模板，不是真实对话导出。** 说明见 `01-脚手架诊断Agent.md` 顶部。替换时保留你修改 Runbook 的真实来回，删掉密钥和仓库账号令牌。

## 这次协作要解决的问题

检索只能返回仓库里的 Markdown。作者要一份 ImagePullBackOff Runbook：现象、只读排查、常见根因、必须人工确认的修复建议、风险。文案要和 Mock 证据、动作 id `fix-image` 对得上，避免 Runbook 写一套、程序执行另一套。

## 人类提示（示例）

```text
写 runbooks/imagepullbackoff.md，给本地 TF-IDF 检索用。

要求：
- 中文，标题用「Runbook：ImagePullBackOff」
- 现象要能对上这些证据词：ImagePullBackOff、ErrImagePull、Failed to pull image、Pending
- 排查先只读：镜像名和 Tag、仓库是否可达、imagePullSecrets、YAML 里的 image 字段
- 常见根因：Tag 不存在、鉴权失败、仓库地址写错
- 修复建议单独成节，标题里保留「需人工确认」
- 写明本仓库唯一可执行动作是 fix-image，把镜像改为 nginx:1.27
- 不要写真实的 registry 密码、不要写生产集群名称
- 风险：改错 Secret 或镜像会扩大故障面
```

## 助手回复要点（示例）

文件按「现象 / 排查步骤 / 常见根因 / 修复建议（需人工确认）/ 风险」五段写。修复建议里同时保留通用做法（更正镜像后滚动发布、修复拉取凭证）和本演示的动作 id，避免读者以为 `nginx:1.27` 是所有 ImagePull 的唯一解法。

检索侧不需要改代码：`src/rag/ingest.py` 会读取 `runbooks/*.md` 的全文做分词。诊断查询会带上 Events 里的 reason 和 message，因此 Runbook 里保留 `ImagePullBackOff`、`Failed to pull image` 这些英文标识。

## 和当前文件的对应关系

落地文件是 `runbooks/imagepullbackoff.md`。同类文件还有：

- `runbooks/oomkilled.md`，对应动作 `bump-memory`，limit `256Mi`
- `runbooks/crashloopbackoff.md`，对应动作 `fix-command`
- `runbooks/general-triage.md`，证据不足时的通用顺序

真实快照可以是「你贴出初稿、助手改一节、你再删掉一条过于绝对的命令」。请保留这种修改，评委要看的是你在改知识库，而不是一次性粘贴。
