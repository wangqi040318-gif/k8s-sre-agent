# Runbook：CreateContainerConfigError / FailedMount

场景 id：`configmount`。关键词：CreateContainerConfigError、FailedMount、configmap "app-config" not found。

## 现象
- Pod 已调度，但容器停在 Waiting，reason 为 `CreateContainerConfigError`
- 同时有 `FailedMount`：`MountVolume.SetUp failed for volume "config"`
- 两条消息都指向 `configmap "app-config" not found`
- 容器没有进程日志。这还没到 CrashLoopBackOff

## 排查步骤
1. `kubectl describe pod` 看 Environment 和 Volumes，确认引用的名字、键、是否 `optional: false`。
2. `kubectl get configmap app-config -n <ns>`；Secret 缺失时消息会是 `secret "..." not found`，用同样的步骤核对，但不要把 Secret 内容打到终端。
3. 名字存在时再核对键。`configMapKeyRef` 要的键不在 data 里，同样会 CreateContainerConfigError。
4. 卷是 `subPath` 或只读挂载时，确认挂载路径没有写错。不要为了“先起来”把 `optional: true` 打开来掩盖缺配置。
5. 和 CrashLoop 区分：进程已经启动、退出码非 0，才是应用自己读不到文件。这里是 kubelet 还没创建容器。

## 常见根因
- 清单先于 ConfigMap / Secret 发布，或发布到了另一个命名空间
- 重命名后工作负载没改引用
- Helm / Kustomize 把对象渲染丢了
- RBAC 很少是这个报错的原因；kubelet 读不到对象时，消息通常不是 not found

## 修复建议（需人工确认）
- 本演示的环境变量 `LOG_LEVEL` 和卷 `config` 引用同一个 ConfigMap `app-config`。补上这一个对象，两类报错一起消失。
- 动作 id：`create-config`。创建非敏感数据：`LOG_LEVEL=info`，`config.yaml` 内容为 `mode: demo`。
- ConfigMap 已存在时只补缺失的键，不覆盖已有值。不创建 Secret，不删除 Pod。kubelet 会重试挂载。
- 诊断不会自动执行，需 `--confirm YES` 或输入 `CONFIRM`。
- 真实 Secret 走密钥管理系统或审批过的清单，不要让 Agent 生成密码。

## 风险
补错名字会留下一份没人用的 ConfigMap。覆盖已有键可能改掉别的工作负载正在读的配置，所以这个动作拒绝覆盖。删除 ConfigMap 即可回滚，回滚同样要人工确认。
