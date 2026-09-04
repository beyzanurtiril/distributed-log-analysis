import os
import sys
import json
import re
import logging
from collections import defaultdict

LATENCY_BUCKETS = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, float("inf")]
BUCKET_LABELS = ["0.01", "0.05", "0.1", "0.25", "0.5", "1.0", "2.5", "5.0", "+Inf"]

LINE_PATTERN = re.compile(
    r'^(\S+) \S+ \S+ \[(\d+)/(\w+)/\d+:(\d+):\d+:\d+ [+-]\d+\] '
    r'"(\S+) (\S+) \S+" (\d+) (\d+) (\S+)$'
)


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "level": record.levelname,
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "message": record.getMessage(),
        }
        return json.dumps(payload)


def setup_logging():
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("worker")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger


log = setup_logging()


def bucket_label(latency):
    for label, threshold in zip(BUCKET_LABELS, LATENCY_BUCKETS):
        if latency <= threshold:
            return label
    return "+Inf"


def estimate_p95(bucket_counts, total_count):
    """Kumulatif bucket sayaclarindan p95'i yaklasik olarak tahmin eder
    (Prometheus histogram_quantile mantigina benzer, dogrusal interpolasyon)."""
    if total_count == 0:
        return None
    target = total_count * 0.95
    cumulative = 0
    prev_threshold = 0.0
    for label, threshold in zip(BUCKET_LABELS, LATENCY_BUCKETS):
        count_in_bucket = bucket_counts.get(label, 0)
        cumulative += count_in_bucket
        if cumulative >= target:
            if threshold == float("inf"):
                return round(prev_threshold, 3)
            if count_in_bucket == 0:
                return round(threshold, 3)
            fraction_needed = (target - (cumulative - count_in_bucket)) / count_in_bucket
            estimate = prev_threshold + fraction_needed * (threshold - prev_threshold)
            return round(estimate, 3)
        prev_threshold = threshold
    return round(prev_threshold, 3)


def main():
    shard_index = int(os.environ.get("SHARD_INDEX", "0"))
    shard_total = int(os.environ.get("SHARD_TOTAL", "1"))
    log_dir = sys.argv[1] if len(sys.argv) > 1 else "/data/logs/"

    log.info(f"Shard {shard_index}/{shard_total} basliyor, log dizini: {log_dir}")

    total_lines = 0
    skipped_lines = 0
    status_distribution = defaultdict(int)
    ip_counts = defaultdict(int)
    endpoint_counts = defaultdict(int)
    endpoint_latency_sum = defaultdict(float)
    endpoint_latency_buckets = defaultdict(lambda: defaultdict(int))
    hourly_counts = defaultdict(int)

    try:
        log_files = sorted(
            os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".log")
        )
    except FileNotFoundError:
        log.error(f"Log dizini bulunamadi: {log_dir}")
        sys.exit(1)

    log.info(f"{len(log_files)} log dosyasi bulundu")

    for filepath in log_files:
        log.info(f"Isleniyor: {filepath}")
        with open(filepath, "r") as f:
            for line_no, line in enumerate(f):
                if line_no % shard_total != shard_index:
                    continue

                match = LINE_PATTERN.match(line)
                if not match:
                    skipped_lines += 1
                    continue

                ip, day, month, hour, method, endpoint, status, bytes_, latency = match.groups()
                latency = float(latency)

                total_lines += 1
                status_distribution[status] += 1
                ip_counts[ip] += 1
                endpoint_counts[endpoint] += 1
                endpoint_latency_sum[endpoint] += latency
                endpoint_latency_buckets[endpoint][bucket_label(latency)] += 1
                hourly_counts[hour] += 1

    if skipped_lines:
        log.warning(f"{skipped_lines} satir format uyusmadigi icin atlandi")

    top_10_ips = sorted(ip_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    endpoints = {}
    for ep, count in endpoint_counts.items():
        buckets = dict(endpoint_latency_buckets[ep])
        endpoints[ep] = {
            "count": count,
            "avg_latency": round(endpoint_latency_sum[ep] / count, 3),
            "latency_buckets": buckets,
            "p95_estimate": estimate_p95(buckets, count),
        }

    log.info(f"Tamamlandi: {total_lines} satir islendi, {len(endpoints)} endpoint bulundu")

    result = {
        "shard_index": shard_index,
        "shard_total": shard_total,
        "total_lines": total_lines,
        "status_distribution": dict(status_distribution),
        "top_10_ips": [{"ip": ip, "count": c} for ip, c in top_10_ips],
        "endpoints": endpoints,
        "hourly_density": dict(sorted(hourly_counts.items())),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
