#!/usr/bin/env python3
import json
import sys

def main():
    files = sys.argv[1:]
    if not files:
        print("Kullanim: merge_results.py shard_0.json shard_1.json ...", file=sys.stderr)
        sys.exit(1)

    total_lines = 0
    status_dist = {}
    endpoint_sum_count = {}
    endpoint_sum_latency = {}
    hourly = {}
    ip_counts = {}

    for f in files:
        with open(f) as fh:
            data = json.load(fh)

        total_lines += data.get("total_lines", 0)

        for status, count in data.get("status_distribution", {}).items():
            status_dist[status] = status_dist.get(status, 0) + count

        for endpoint, info in data.get("endpoints", {}).items():
            count = info.get("count", 0)
            avg_latency = info.get("avg_latency", 0)
            endpoint_sum_count[endpoint] = endpoint_sum_count.get(endpoint, 0) + count
            endpoint_sum_latency[endpoint] = endpoint_sum_latency.get(endpoint, 0) + (avg_latency * count)

        for hour, count in data.get("hourly_density", {}).items():
            hourly[hour] = hourly.get(hour, 0) + count

        for entry in data.get("top_10_ips", []):
            ip = entry.get("ip")
            count = entry.get("count", 0)
            ip_counts[ip] = ip_counts.get(ip, 0) + count

    endpoints_merged = {}
    for endpoint, count in endpoint_sum_count.items():
        avg = endpoint_sum_latency[endpoint] / count if count else 0
        endpoints_merged[endpoint] = {"count": count, "avg_latency": round(avg, 3)}

    top_ips_sorted = sorted(ip_counts.items(), key=lambda x: x[1], reverse=True)[:10]
    top_ips_merged = [{"ip": ip, "count": count} for ip, count in top_ips_sorted]

    result = {
        "shard_count": len(files),
        "total_lines": total_lines,
        "status_distribution": status_dist,
        "top_10_ips": top_ips_merged,
        "top_10_ips_note": "Sadece her shard'in kendi top10'unda gorunen IP'ler birlestirildi; bir shard'da top10'a girmeyen bir IP'nin gercek toplami burada eksik olabilir.",
        "endpoints": endpoints_merged,
        "hourly_density": hourly
    }

    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
