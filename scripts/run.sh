#!/bin/bash
set -e

NAMESPACE="log-analysis"
DEPLOYMENT="log-worker"
CONFIGMAP="worker-config"
N="$1"

if [[ -z "$N" ]]; then
    echo "Kullanim: ./run.sh <worker_sayisi>"
    exit 1
fi

echo "1) ConfigMap guncelleniyor: SHARD_TOTAL=$N"
kubectl patch configmap "$CONFIGMAP" -n "$NAMESPACE" --type merge -p "{\"data\":{\"SHARD_TOTAL\":\"$N\"}}"

echo "2) Deployment $N replica'ya olcekleniyor"
kubectl scale deployment "$DEPLOYMENT" -n "$NAMESPACE" --replicas="$N"

echo "3) Pod'larin Ready olmasi bekleniyor"
kubectl wait --for=condition=Ready pod -l app=log-worker -n "$NAMESPACE" --timeout=120s

echo "4) Pod isimleri aliniyor"
PODS=($(kubectl get pods -n "$NAMESPACE" -l app=log-worker -o jsonpath='{.items[*].metadata.name}'))

mkdir -p run_output
rm -f run_output/shard_*.json

echo "5) Her pod'a kendi shard index'i veriliyor, worker calistiriliyor"
i=0
for pod in "${PODS[@]}"; do
    echo "   -> $pod : SHARD_INDEX=$i"
    kubectl exec "$pod" -n "$NAMESPACE" -- env SHARD_INDEX="$i" SHARD_TOTAL="$N" /app/worker.sh /data/logs/ > "run_output/shard_$i.json"
    i=$((i+1))
done

echo "6) Sonuclar birlestiriliyor"
python3 merge_results.py run_output/shard_*.json > run_output/merged.json

echo "Tamamlandi. Birlesmis rapor: run_output/merged.json"
cat run_output/merged.json
