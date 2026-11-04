#!/usr/bin/env bash
# 向 kind / minikube / 任意可访问集群注入演示故障 Pod。
# 无集群时打印跳过说明并退出 0，避免打断演示脚本串联。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS="${K8S_NAMESPACE:-default}"

echo "[demo] inject_faults.sh — namespace=${NS}"

if ! command -v kubectl >/dev/null 2>&1; then
  echo "[demo] 未找到 kubectl，跳过真实注入。请使用 MOCK=1 跑 CLI/UI："
  echo "       MOCK=1 python -m src.cli --scenario imagepull"
  exit 0
fi

if ! kubectl cluster-info >/dev/null 2>&1; then
  echo "[demo] 无法连接集群（检查 KUBECONFIG / kind / minikube），跳过注入。"
  echo "       本地无集群演示：MOCK=1 python -m src.cli --scenario oom"
  exit 0
fi

# kind 示例：
#   kind create cluster --name sre-demo
# minikube 示例：
#   minikube start
# 然后：
#   export KUBECONFIG=... ; ./demo/inject_faults.sh

echo "[demo] 应用 manifests..."
kubectl apply -n "$NS" -f "$ROOT/demo/manifests/app-healthy.yaml"
kubectl apply -n "$NS" -f "$ROOT/demo/manifests/fault-imagepull.yaml"
kubectl apply -n "$NS" -f "$ROOT/demo/manifests/fault-oom.yaml"
kubectl apply -n "$NS" -f "$ROOT/demo/manifests/fault-crashloop.yaml"
kubectl apply -n "$NS" -f "$ROOT/demo/manifests/fault-pending.yaml"
kubectl apply -n "$NS" -f "$ROOT/demo/manifests/fault-configmount.yaml"
kubectl apply -n "$NS" -f "$ROOT/demo/manifests/fault-probe.yaml"
kubectl apply -n "$NS" -f "$ROOT/demo/manifests/fault-connectivity.yaml"
echo "[demo] 已提交。可观察："
echo "  kubectl get pods -n $NS -l app=demo -w"
echo "  kubectl get endpoints demo-web -n $NS"
echo "切真实模式诊断（只读，不会自动修复）："
echo "  MOCK=0 python -m src.cli --scenario imagepull --pod demo-imagepull --namespace $NS"
echo "  MOCK=0 python -m src.cli --scenario pending --pod demo-pending --namespace $NS"
echo "确认后才执行修复，例如："
echo "  MOCK=0 python -m src.cli --apply --confirm YES --action fix-image --scenario imagepull --pod demo-imagepull --namespace $NS"
