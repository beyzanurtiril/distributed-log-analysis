import argparse
import sys
import os
import time
import json
import logging
import subprocess

from kubernetes import client, config
from kubernetes.client.rest import ApiException

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from worker import estimate_p95, BUCKET_LABELS
except ImportError:
    estimate_p95 = None
    BUCKET_LABELS = ["0.01", "0.05", "0.1", "0.25", "0.5", "1.0", "2.5", "5.0", "+Inf"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("orchestrate")

NAMESPACE = "log-analysis"
CONFIGMAP_NAME = "worker-config"
JOB_NAME = "log-worker-job"
DEPLOYMENT_LABEL = "app=log-worker"
IMAGE = "log-worker:v1"
PVC_NAME = "log-data-pvc"

RETRY_DELAYS = [2, 4, 8]


def with_retries(description, func, *args, **kwargs):
    """Gecici hatalarda en fazla 3 kez, artan bekleme suresiyle (2s,4s,8s) tekrar
    dener. 409/404/403 gibi kalici hatalari retry yapmadan cagirana firlatir."""
    last_exc = None
    for attempt, delay in enumerate([0] + RETRY_DELAYS):
        if delay:
            log.warning(f"{description}: {delay}s sonra tekrar denenecek (deneme {attempt}/3)")
            time.sleep(delay)
        try:
            return func(*args, **kwargs)
        except ApiException as e:
            if e.status in (409, 404, 403):
                raise
            last_exc = e
            log.error(f"{description}: gecici hata (HTTP {e.status}): {e.reason}")
    log.error(f"{description}: {len(RETRY_DELAYS)} deneme sonunda basarisiz oldu")
    raise last_exc


def apply_namespace(core_v1):
    """Namespace'i idempotent sekilde olusturur: zaten varsa hata vermez."""
    body = client.V1Namespace(metadata=client.V1ObjectMeta(name=NAMESPACE))
    try:
        with_retries("Namespace olusturma", core_v1.create_namespace, body)
        log.info(f"Namespace '{NAMESPACE}' olusturuldu")
    except ApiException as e:
        if e.status == 409:
            log.info(f"Namespace '{NAMESPACE}' zaten var, atlaniyor")
        elif e.status == 403:
            log.error(f"Namespace olusturma icin yetki yok (403): {e.reason}")
            raise
        else:
            raise


def apply_configmap(core_v1, shard_total):
    """ConfigMap'i idempotent sekilde olusturur/gunceller."""
    body = client.V1ConfigMap(
        metadata=client.V1ObjectMeta(name=CONFIGMAP_NAME, namespace=NAMESPACE),
        data={"SHARD_TOTAL": str(shard_total)},
    )
    try:
        with_retries("ConfigMap olusturma", core_v1.create_namespaced_config_map, NAMESPACE, body)
        log.info(f"ConfigMap '{CONFIGMAP_NAME}' olusturuldu (SHARD_TOTAL={shard_total})")
    except ApiException as e:
        if e.status == 409:
            log.info(f"ConfigMap '{CONFIGMAP_NAME}' zaten var, guncelleniyor")
            with_retries(
                "ConfigMap guncelleme",
                core_v1.patch_namespaced_config_map,
                CONFIGMAP_NAME, NAMESPACE, body,
            )
        elif e.status == 403:
            log.error(f"ConfigMap olusturma icin yetki yok (403): {e.reason}")
            raise
        else:
            raise


def create_indexed_job(batch_v1, worker_count):
    """Indexed Job'i olusturur (completions=shards, parallelism=worker_count).
    Spec alanlari degistirilemedigi icin ayni isimde eski Job varsa once siler."""
    try:
        batch_v1.delete_namespaced_job(
            JOB_NAME, NAMESPACE,
            body=client.V1DeleteOptions(propagation_policy="Foreground"),
        )
        log.info(f"Eski Job '{JOB_NAME}' silindi, temizlenmesi bekleniyor...")
        for _ in range(30):
            try:
                batch_v1.read_namespaced_job(JOB_NAME, NAMESPACE)
                time.sleep(1)
            except ApiException as e:
                if e.status == 404:
                    break
                raise
    except ApiException as e:
        if e.status != 404:
            raise

    container = client.V1Container(
        name="worker",
        image=IMAGE,
        image_pull_policy="IfNotPresent",
        command=["/bin/sh", "-c",
                 "mkdir -p /data/logs/job_output && "
                 "SHARD_INDEX=$JOB_COMPLETION_INDEX SHARD_TOTAL=%d "
                 "python3 /app/worker.py /data/logs/ > /data/logs/job_output/shard_$JOB_COMPLETION_INDEX.json"
                 % worker_count],
        volume_mounts=[client.V1VolumeMount(name="log-data", mount_path="/data/logs")],
    )
    pod_spec = client.V1PodSpec(
        restart_policy="OnFailure",
        containers=[container],
        volumes=[client.V1Volume(
            name="log-data",
            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(claim_name=PVC_NAME),
        )],
    )
    job = client.V1Job(
        metadata=client.V1ObjectMeta(name=JOB_NAME, namespace=NAMESPACE),
        spec=client.V1JobSpec(
            completions=worker_count,
            parallelism=worker_count,
            completion_mode="Indexed",
            backoff_limit=4,
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(labels={"app": "log-worker-job"}),
                spec=pod_spec,
            ),
        ),
    )
    try:
        with_retries("Job olusturma", batch_v1.create_namespaced_job, NAMESPACE, job)
        log.info(f"Job '{JOB_NAME}' olusturuldu (completions={worker_count}, parallelism={worker_count})")
    except ApiException as e:
        if e.status == 403:
            log.error(f"Job olusturma icin yetki yok (403): {e.reason}")
        raise


def stream_pod_logs(core_v1, seen_pods=None):
    """Job'a ait pod'lardan henuz gosterilmemis loglari canli olarak yazdirir."""
    if seen_pods is None:
        seen_pods = set()
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app=log-worker-job")
    for pod in pods.items:
        name = pod.metadata.name
        if name in seen_pods or pod.status.phase not in ("Running", "Succeeded", "Failed"):
            continue
        try:
            logs = core_v1.read_namespaced_pod_log(name, NAMESPACE)
            if logs.strip():
                log.info(f"--- {name} logu ---\n{logs.strip()}")
                seen_pods.add(name)
        except ApiException:
            pass
    return seen_pods


def wait_for_job(batch_v1, core_v1, worker_count, timeout=180):
    """Job'un tamamlanmasini bekler; timeout'ta hangi pod ne durumda, kac saniyedir
    o durumda oldugunu raporlar."""
    start = time.time()
    seen_pods = set()
    while time.time() - start < timeout:
        seen_pods = stream_pod_logs(core_v1, seen_pods)
        job = batch_v1.read_namespaced_job_status(JOB_NAME, NAMESPACE)
        if job.status.succeeded == worker_count:
            log.info("Job basariyla tamamlandi.")
            return True
        if job.status.failed and job.status.failed > 0:
            log.warning(f"{job.status.failed} pod basarisiz oldu, Job hala deniyor olabilir (backoffLimit).")
        time.sleep(3)

    log.error(f"TIMEOUT ({timeout}s): Job zamaninda tamamlanmadi. Pod durumlari:")
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector="app=log-worker-job")
    now = time.time()
    for pod in pods.items:
        phase = pod.status.phase
        start_time = pod.status.start_time
        if start_time:
            elapsed = now - start_time.timestamp()
            log.error(f"  {pod.metadata.name}: {phase} durumunda, {elapsed:.0f} saniyedir")
        else:
            log.error(f"  {pod.metadata.name}: {phase} durumunda, henuz baslamadi")
    return False


def exec_cat(pod_name, filepath):
    """Calisan bir pod'un icinde 'cat' calistirip dosya icerigini doner."""
    result = subprocess.run(
        ["kubectl", "exec", pod_name, "-n", NAMESPACE, "--", "cat", filepath],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def merge_results(core_v1, worker_count):
    """Job bittiginde her shard'in kismi JSON ciktisini toplar, birlestirir ve
    tek bir birlesik rapor uretir."""
    pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector=DEPLOYMENT_LABEL)
    running = [p.metadata.name for p in pods.items if p.status.phase == "Running"]
    if not running:
        log.error("Sonuclari okumak icin calisan bir Deployment pod'u bulunamadi")
        return None
    reader_pod = running[0]

    total_lines = 0
    status_distribution = {}
    endpoints = {}
    hourly_density = {}

    for i in range(worker_count):
        path = f"/data/logs/job_output/shard_{i}.json"
        try:
            raw = exec_cat(reader_pod, path)
            shard = json.loads(raw)
        except Exception as e:
            log.warning(f"shard_{i}.json okunamadi/parse edilemedi: {e}")
            continue

        total_lines += shard.get("total_lines", 0)
        for status, count in shard.get("status_distribution", {}).items():
            status_distribution[status] = status_distribution.get(status, 0) + count
        for hour, count in shard.get("hourly_density", {}).items():
            hourly_density[hour] = hourly_density.get(hour, 0) + count

        for ep, data in shard.get("endpoints", {}).items():
            if ep not in endpoints:
                endpoints[ep] = {"count": 0, "latency_sum_approx": 0.0, "latency_buckets": {}}
            endpoints[ep]["count"] += data["count"]
            endpoints[ep]["latency_sum_approx"] += data["avg_latency"] * data["count"]
            for label, c in data.get("latency_buckets", {}).items():
                endpoints[ep]["latency_buckets"][label] = endpoints[ep]["latency_buckets"].get(label, 0) + c

    for ep, data in endpoints.items():
        data["avg_latency"] = round(data["latency_sum_approx"] / data["count"], 3) if data["count"] else 0
        del data["latency_sum_approx"]
        if estimate_p95:
            data["p95_estimate"] = estimate_p95(data["latency_buckets"], data["count"])

    report = {
        "worker_count": worker_count,
        "total_lines": total_lines,
        "status_distribution": status_distribution,
        "endpoints": endpoints,
        "hourly_density": dict(sorted(hourly_density.items())),
    }
    log.info(f"Sonuclar birlestirildi: {total_lines} toplam satir, {len(endpoints)} endpoint")
    return report


def main():
    parser = argparse.ArgumentParser(description="Log analiz Job orchestratoru")
    parser.add_argument("--workers", type=int, default=3, help="Worker (shard) sayisi")
    parser.add_argument("--timeout", type=int, default=180, help="Job icin timeout (saniye)")
    args = parser.parse_args()

    config.load_kube_config()
    core_v1 = client.CoreV1Api()
    batch_v1 = client.BatchV1Api()

    try:
        apply_namespace(core_v1)
        apply_configmap(core_v1, args.workers)
        create_indexed_job(batch_v1, args.workers)
        success = wait_for_job(batch_v1, core_v1, args.workers, timeout=args.timeout)
        if not success:
            sys.exit(1)

        report = merge_results(core_v1, args.workers)
        if report:
            print(json.dumps(report, indent=2))
    except ApiException as e:
        if e.status == 404:
            log.error(f"Kaynak bulunamadi (404): {e.reason}")
        elif e.status == 403:
            log.error(f"Yetki hatasi (403): {e.reason}")
        else:
            log.error(f"Beklenmeyen API hatasi (HTTP {e.status}): {e.reason}")
        sys.exit(2)


if __name__ == "__main__":
    main()
