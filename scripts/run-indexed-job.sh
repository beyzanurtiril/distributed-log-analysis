#!/bin/bash
set -e
N="$1"
if [[ -z "$N" ]]; then
    echo "Kullanim: ./run-indexed-job.sh <worker_sayisi>"
    exit 1
fi

kubectl delete job log-worker-job -n log-analysis --ignore-not-found=true
sleep 2

cat > /tmp/worker-job-filled.yaml << EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: log-worker-job
  namespace: log-analysis
spec:
  completions: $N
  parallelism: $N
  completionMode: Indexed
  backoffLimit: 4
  template:
    metadata:
      labels:
        app: log-worker-job
    spec:
      restartPolicy: OnFailure
      containers:
        - name: worker
          image: log-worker:v1
          imagePullPolicy: IfNotPresent
          command: ["/bin/sh", "-c", "mkdir -p /data/logs/job_output && SHARD_INDEX=\$JOB_COMPLETION_INDEX SHARD_TOTAL=$N /app/worker.sh /data/logs/ > /data/logs/job_output/shard_\$JOB_COMPLETION_INDEX.json"]
          volumeMounts:
            - name: log-data
              mountPath: /data/logs
      volumes:
        - name: log-data
          persistentVolumeClaim:
            claimName: log-data-pvc
EOF

echo "Job olusturuluyor (N=$N)..."
time kubectl apply -f /tmp/worker-job-filled.yaml
kubectl wait --for=condition=Complete job/log-worker-job -n log-analysis --timeout=180s
echo "Job tamamlandi."
kubectl get pods -n log-analysis -l app=log-worker-job
