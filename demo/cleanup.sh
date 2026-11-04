#!/usr/bin/env bash
# 清理演示故障 Pod。无集群时跳过。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NS="${K8S_NAMESPACE:-default}"

echo "[demo] cleanup.sh — namespace=${NS}"

if ! command -v kubectl >/dev/null 2>&1; then
  echo "[demo] 未找到 kubectl，跳过清理。"
  exit 0
fi

if ! kubectl cluster-info >/dev/null 2>&1; then
  echo "[demo] 无法连接集群，跳过清理。"
  exit 0
fi

kubectl delete -n "$NS" -f "$ROOT/demo/manifests/app-healthy.yaml" --ignore-not-found
kubectl delete -n "$NS" -f "$ROOT/demo/manifests/fault-imagepull.yaml" --ignore-not-found
kubectl delete -n "$NS" -f "$ROOT/demo/manifests/fault-oom.yaml" --ignore-not-found
kubectl delete -n "$NS" -f "$ROOT/demo/manifests/fault-crashloop.yaml" --ignore-not-found
echo "[demo] 清理完成。"
