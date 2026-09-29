#!/usr/bin/env bash
set -euo pipefail

manifest_dir="infrastruture/kubernetes/phase1"

if ! kubectl get secret ecommerce-secrets -n ecommerce >/dev/null 2>&1; then
  echo "Missing ecommerce/ecommerce-secrets. Copy ${manifest_dir}/secret.example.yaml, replace every placeholder, and apply it first." >&2
  exit 1
fi

kubectl apply -f "${manifest_dir}/app.yaml"
kubectl wait --for=condition=complete job/ecommerce-schema-init -n ecommerce --timeout=300s
kubectl rollout status deployment/backend-api -n ecommerce --timeout=300s
kubectl rollout status deployment/order-service -n ecommerce --timeout=300s
kubectl rollout status deployment/frontend -n ecommerce --timeout=300s

echo "Phase 1 application is ready."
