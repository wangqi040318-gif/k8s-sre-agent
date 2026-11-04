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
- CrashLoopBackOff + 进程非 0 退出 → 应用、启动命令或配置
- Pending + FailedScheduling / Unschedulable → requests 过大，或 node affinity / selector / 污点
- CreateContainerConfigError / FailedMount → 缺失的 ConfigMap 或 Secret、错误的卷
- Running 但 Ready=False，Events 为 probe failed → 探针路径或端口。先改探针，不要删探针
- Pod Ready 但 Service Endpoints 为空 → 选择器与 Pod 标签不一致
- Endpoints 非空仍不通 → 再查 NetworkPolicy 和 CoreDNS。不要在 Endpoints 为空时先改策略

## 安全
所有写操作标注「需人工确认」；生产禁止 Agent 自动执行破坏性命令。
