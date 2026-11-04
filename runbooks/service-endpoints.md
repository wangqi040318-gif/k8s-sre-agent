# Runbook：Service 无 Endpoints（选择器 / DNS）

场景 id：`connectivity`。关键词：Endpoints is empty、matches 0 pods、no ready addresses、selector、demo-web、FailedToUpdateEndpoint。

## 现象
- 调用方说服务不通，DNS 能解析 `demo-web.default.svc.cluster.local`，地址是 ClusterIP
- Pod `demo-connectivity` 自己是 Running 且 Ready，标签 `app=demo`
- Service `demo-web` 的 selector 仍是 `app=frontend`，`Endpoints: <none>`
- Events：`selector app=frontend matches 0 pods; Endpoints is empty`
- 本演示命名空间没有拒绝 80 端口的 NetworkPolicy

## 排查步骤
1. `kubectl get pods -n <ns> --show-labels` 与 `kubectl get svc demo-web -o yaml` 对标签。选择器是与，不是或。
2. `kubectl get endpoints demo-web`。为空就先不要查 NetworkPolicy，策略还没机会丢包。
3. 标签一致但 Endpoints 仍为空：看 Pod 是不是 Ready。未就绪的 Pod 不会进 Endpoints。那是探针问题，不是选择器问题。
4. Endpoints 已经有地址，客户端仍超时或被拒绝：再看 NetworkPolicy 的 ingress、命名空间选择器、端口是否写成了 80 以外。然后查 CoreDNS 的 `kubectl get pods -n kube-system -l k8s-app=kube-dns`。
5. 不要用 `hostNetwork` 或删掉所有 NetworkPolicy 来“先通”。那会把别的流量也放开。

## 常见根因
- Service 选择器和 Deployment 模板标签在某次改名后只改了一边
- 多了一个选不中的标签，例如 `version=v2`
- Pod 没有 Ready，Endpoints 控制器故意不收它
- 集群开了 NetworkPolicy，默认拒绝。这是下一条排查，不是本演示的证据

## 修复建议（需人工确认）
- 动作 id：`fix-selector`。把 Service `demo-web` 的 selector 整段替换为 `app=demo`。
- 只改 selector，不改端口、ClusterIP 和 Service 类型，也不新增或放宽 NetworkPolicy。
- 执行前会核对目标 Pod 的标签确实包含这个 selector。对不上就停止，避免把 Service 指到空集。
- 诊断不会自动执行，需 `--confirm YES` 或输入 `CONFIRM`。
- Endpoints 非空仍不通时，不要重复这个动作。按上面的第 4 步查策略和 DNS，单独开变更。

## 风险
改选择器会立刻把流量切到新的 Pod 集合。旧的 `app=frontend` 可以再写回去，回滚同样要人工确认。
