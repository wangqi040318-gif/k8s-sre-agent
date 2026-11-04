# Runbook：探针未就绪（Running 但 not Ready）

场景 id：`probe`。关键词：Readiness probe failed、Liveness probe failed、Unhealthy、httpGet、8080、/healthz。

## 现象
- Pod phase 是 Running，Ready 为 False
- Events：`Readiness probe failed: Get "http://10.0.0.15:8080/healthz": dial tcp 10.0.0.15:8080: connect: connection refused`
- 存活探针再失败几次后容器重启。lastState 消息是 `Liveness probe failed`，不要先当成 OOM（本演示退出码不是 137）
- 容器日志显示进程监听 `0.0.0.0:80`，只提供 `/`

## 排查步骤
1. `kubectl describe pod` 看探针的 path、port、periodSeconds、failureThreshold，以及容器 `ports`。
2. 对照进程实际监听的端口。本演示容器端口是 80，探针却访问 8080 的 `/healthz`。
3. 端口对了仍 404 时，再对路径。用只读的 `kubectl exec` 在容器里访问探针 URL 之前，先确认这是演示环境。
4. 重启次数升高不自动等于 CrashLoop 的启动命令错误。先看 Events 里是不是 probe failed。
5. 不要把探针删掉让 Pod 变 Ready。没了就绪探针，Service 会把流量打进还没起来的容器。

## 常见根因
- 探针端口沿用了模板，应用实际监听另一个端口
- 路径写成了编排系统的默认 `/healthz`，应用只有 `/` 或 `/ready`
- `initialDelaySeconds` 太短只会导致启动期误杀；本演示是端口根本没人听，加长延迟没用
- 探针打到了需要鉴权的路径

## 修复建议（需人工确认）
- 动作 id：`fix-probe`。把 readiness 和 liveness 的 httpGet 改成 `path=/`、`port=80`。
- 保留原来的 `periodSeconds=5` 和 `failureThreshold=3`，不删除探针，也不改成 exec。
- 探针不是 httpGet 时，这个动作会停止，避免把 tcp 或 exec 探针改成另一种检查。
- 诊断不会自动执行，需 `--confirm YES` 或输入 `CONFIRM`。

## 风险
改错端口会让未就绪的 Pod 接流量，或让存活探针继续重启容器。改回去即可回滚，回滚同样要人工确认。
