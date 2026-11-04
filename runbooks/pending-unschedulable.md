# Runbook：Pending / Unschedulable（FailedScheduling）

场景 id：`pending`。关键词：FailedScheduling、Unschedulable、Insufficient cpu、Insufficient memory、node affinity、nodeSelector。

## 现象
- Pod 一直 Pending，`Node: <none>`
- Condition `PodScheduled=False`，reason `Unschedulable`
- Events 反复出现 `FailedScheduling`
- 本演示的原文是：`0/3 nodes are available: 1 Insufficient cpu, 1 Insufficient memory, 2 node(s) didn't match Pod's node affinity/selector.`

## 排查步骤
1. `kubectl describe pod` 看 Events 里调度器给出的每一条原因，不要只看第一条。
2. 对照 `resources.requests`。调度看的是 request，不是 limit。本演示 request 为 cpu `8`、memory `16Gi`。
3. 看 `nodeSelector` 和 `requiredDuringSchedulingIgnoredDuringExecution`。本演示要求 `demo.sre/pool=oncall-pool-missing`，以及主机名 `node-that-does-not-exist`。
4. 再看 tolerations 和节点污点。本演示带的是默认的 `not-ready` 容忍，不是这次排不上的原因，不要删。
5. `kubectl get node -o wide` 与 `kubectl describe node` 只用来核对可分配资源和标签。不要 cordon、drain 或删除节点。

## 常见根因
- request 大于节点可分配资源（笔记本、kind、小规格节点上很常见）
- nodeSelector / nodeAffinity 写了集群里不存在的标签
- 节点有污点而 Pod 没有对应 toleration
- 资源配额 ResourceQuota 拒绝了创建。那种情况 Events 是 `FailedCreate`，不是 FailedScheduling

## 修复建议（需人工确认）
- 两条原因都在时，只改 request 或只删亲和性，Pod 仍然 Pending。
- 本仓库可确认执行的动作 id：`relax-scheduling`。它把容器 requests 降到 cpu `100m`、memory `64Mi`，limits 降到 cpu `200m`、memory `128Mi`，并移除 nodeSelector 与 affinity。tolerations 保持不动。
- 诊断不会自动执行，需 `--confirm YES` 或输入 `CONFIRM`。
- 生产上先确认这个工作负载是否真的必须固定节点。亲和性是有意为之时，应该加节点标签，而不是把约束删掉。

## 风险
把 requests 降得过低会在节点上超卖。删掉亲和性可能把 Pod 调度到不该去的池子。这个动作是可逆的：可以再把资源和亲和性写回去，回滚同样要人工确认。
