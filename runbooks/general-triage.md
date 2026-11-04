# Runbook：通用 Pod 排障顺序

## 原则
先只读取证，再假设验证，最后有审批的变更。

## 标准顺序
1. `kubectl get pod -o wide` — 节点、Ready、RESTARTS、AGE
2. `kubectl describe pod` — Conditions、Events、探针、资源
3. `kubectl get events --sort-by=.lastTimestamp`
4. `kubectl logs` / `--previous`
5. 对照近期发布、配置变更、节点压力

## 分类提示
- Pending + 镜像相关 Events → ImagePull
- OOMKilled / 137 → 内存
- CrashLoopBackOff + Error exit → 应用/配置
- Pending + FailedScheduling → 资源/亲和性（本 Demo 未覆盖）

## 安全
所有写操作标注「需人工确认」；生产禁止 Agent 自动执行破坏性命令。
