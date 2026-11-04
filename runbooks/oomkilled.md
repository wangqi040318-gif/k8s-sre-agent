# Runbook：OOMKilled

## 现象
- 容器反复重启，`lastState.terminated.reason=OOMKilled`
- exitCode 常为 137
- Events 可能出现 OOMKilling

## 排查步骤
1. `kubectl describe pod` 查看 Limits/Requests 与 Last State。
2. 查看应用日志，确认是否内存泄漏或突发分配。
3. 有 metrics-server 时用 `kubectl top pod` 观察趋势。
4. 对照 JVM/Go/Node 等运行时堆配置是否超过 limit。

## 常见根因
- `resources.limits.memory` 过小
- 应用泄漏或缓存无限增长
- 流量突增导致瞬时内存峰值

## 修复建议（需人工确认）
- 合理上调 memory limit / 优化应用
- 增加 HPA 或限流，避免单 Pod 过载
- 变更资源必须走评审，避免节点资源被挤爆

## 风险
盲目提高 limit 可能掩盖泄漏；删除 Pod 会导致短暂服务中断。
