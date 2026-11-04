# Runbook：CrashLoopBackOff

## 现象
- 容器 `state.waiting.reason=CrashLoopBackOff`
- restartCount 持续增加
- 日志中常见配置错误、依赖连不上、启动命令失败

## 排查步骤
1. `kubectl logs <pod> --previous` 查看崩溃前输出。
2. 检查 command/args、环境变量、ConfigMap/Secret 挂载。
3. 检查 liveness/readiness 探针是否过于激进。
4. 确认依赖服务（DB、缓存）是否就绪。

## 常见根因
- 缺少配置文件或错误的入口命令
- 探针失败导致反复杀进程
- 权限 / 只读文件系统导致写入失败

## 修复建议（需人工确认）
- 修复配置后滚动更新
- 调整探针参数
- 避免在未定位日志前反复删 Pod
- 本仓库可确认执行的动作 id：`fix-command`（改为健康休眠命令，并移除过激探针）。诊断不会自动执行，需 `--confirm YES` 或输入 `CONFIRM`

## 风险
错误改 ConfigMap 会影响同命名空间多个工作负载。
