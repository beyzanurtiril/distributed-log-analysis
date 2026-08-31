#!/bin/bash
set -e

NAMESPACE="log-analysis"
DEPLOYMENT="log-worker"
CONFIGMAP="worker-config"

mkdir -p bench_output
echo "workers,total_time_sec,fastest_worker_sec,slowest_worker_sec,diff_sec" > bench_output/summary.csv

for N in 1 2 3 5; do
    echo ""
    echo "=========================================="
    echo "N=$N worker ile olcum basliyor"
    echo "=========================================="

    kubectl patch configmap "$CONFIGMAP" -n "$NAMESPACE" --type merge -p "{\"data\":{\"SHARD_TOTAL\":\"$N\"}}"
    kubectl scale deployment "$DEPLOYMENT" -n "$NAMESPACE" --replicas="$N"

    echo "Pod sayisinin tam olarak $N olmasi bekleniyor (eski pod'larin sonlanmasi icin)..."
    for attempt in $(seq 1 60); do
        current_count=$(kubectl get pods -n "$NAMESPACE" -l app=log-worker --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l)
        if [[ "$current_count" -eq "$N" ]]; then
            break
        fi
        sleep 1
    done

    kubectl wait --for=condition=Ready pod -l app=log-worker -n "$NAMESPACE" --timeout=120s

    PODS=($(kubectl get pods -n "$NAMESPACE" -l app=log-worker --field-selector=status.phase=Running -o jsonpath='{.items[*].metadata.name}'))
    echo "Calisacak pod sayisi: ${#PODS[@]} (beklenen: $N)"

    RUN_DIR="bench_output/N${N}"
    mkdir -p "$RUN_DIR"
    rm -f "$RUN_DIR"/timings.txt

    start_total=$(date +%s.%N)

    i=0
    for pod in "${PODS[@]}"; do
        (
            t0=$(date +%s.%N)
            kubectl exec "$pod" -n "$NAMESPACE" -- env SHARD_INDEX="$i" SHARD_TOTAL="$N" /app/worker.sh /data/logs/ > "$RUN_DIR/shard_$i.json"
            t1=$(date +%s.%N)
            dur=$(awk "BEGIN{printf \"%.3f\", $t1-$t0}")
            echo "$i $dur" >> "$RUN_DIR/timings.txt"
        ) &
        i=$((i+1))
    done
    wait

    end_total=$(date +%s.%N)
    total=$(awk "BEGIN{printf \"%.3f\", $end_total-$start_total}")

    fastest=$(sort -k2 -n "$RUN_DIR/timings.txt" | head -1 | awk '{print $2}')
    slowest=$(sort -k2 -n "$RUN_DIR/timings.txt" | tail -1 | awk '{print $2}')
    diff=$(awk "BEGIN{printf \"%.3f\", $slowest-$fastest}")

    echo "N=$N sonuclari:"
    cat "$RUN_DIR/timings.txt"
    echo "Toplam sure: ${total}s | En hizli: ${fastest}s | En yavas: ${slowest}s | Fark: ${diff}s"

    echo "$N,$total,$fastest,$slowest,$diff" >> bench_output/summary.csv
done

echo ""
echo "Tum olcumler bitti. Ozet: bench_output/summary.csv"
cat bench_output/summary.csv
