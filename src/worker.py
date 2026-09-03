import os
import sys
import json
import re
from collections import defaultdict

LATENCY_BUCKETS = [0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, float("inf")]

LINE_PATTERN = re.compile(
    r'^(\S+) \S+ \S+ \[(\d+)/(\w+)/\d+:(\d+):\d+:\d+ [+-]\d+\] '
    r'"(\S+) (\S+) \S+" (\d+) (\d+) (\S+)$'
)

def bucket_label(latency):
    for b in LATENCY_BUCKETS:
        if latency <= b:
            if b == float("inf"):
                return "+Inf"
            return str(b)
    return "+Inf"

def main():
    shard_index = int(os.environ.get("SHARD_INDEX", "0"))
    shard_total = int(os.environ.get("SHARD_TOTAL", "1"))
    log_dir = sys.argv[1] if len(sys.argv) > 1 else "/data/logs/"

    total_lines = 0
    status_distribution = defaultdict(int)
    ip_counts = defaultdict(int)
    endpoint_counts = defaultdict(int)
    endpoint_latency_sum = defaultdict(float)
    endpoint_latency_buckets = defaultdict(lambda: defaultdict(int))
    hourly_counts = defaultdict(int)

    log_files = sorted(
        os.path.join(log_dir, f) for f in os.listdir(log_dir) if f.endswith(".log")
    )

    for filepath in log_files:
        with open(filepath, "r") as f:
            for line_no, line in enumerate(f):
                if line_no % shard_total != shard_index:
                    continue

                match = LINE_PATTERN.match(line)
                if not match:
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

    top_10_ips = sorted(ip_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    endpoints = {}
    for ep, count in endpoint_counts.items():
        endpoints[ep] = {
            "count": count,
            "avg_latency": round(endpoint_latency_sum[ep] / count, 3),
            "latency_buckets": dict(endpoint_latency_buckets[ep]),
        }

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
