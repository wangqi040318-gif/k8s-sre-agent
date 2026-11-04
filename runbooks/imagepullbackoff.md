# Runbook：ImagePullBackOff

## 现象
- Pod 长期 Pending / 容器 Waiting
- `kubectl describe pod` 可见 `ErrImagePull` / `ImagePullBackOff`
- Events 提示 Failed to pull image / NotFound / unauthorized

## 排查步骤
1. 确认镜像名与 Tag 是否拼写正确、是否存在于仓库。
2. 节点能否访问镜像仓库（网络策略、DNS、代理）。
3. 私有仓库是否配置了正确的 `imagePullSecrets`。
4. 对照 Deployment/Pod YAML 中的 `image:` 字段。

## 常见根因
- Tag 不存在或已被删除
- 仓库鉴权失败
- 镜像仓库地址写错（例如少了 registry 前缀）

## 修复建议（需人工确认）
- 更正镜像引用后滚动发布
- 修复 Secret / ServiceAccount 拉取凭证
- 勿在未审批情况下向生产直接 `kubectl apply` 未知清单

## 风险
错误修改 Secret 或镜像可能扩大故障面；优先只读验证。
