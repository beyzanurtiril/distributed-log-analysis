#!/bin/bash
LOG_DIR="$1"
SHARD_INDEX="${SHARD_INDEX:-0}"
SHARD_TOTAL="${SHARD_TOTAL:-1}"

if [[ -z "$LOG_DIR" ]]; then
    echo "Kullanim: SHARD_INDEX=x SHARD_TOTAL=y ./worker.sh /log/klasoru/" >&2
    exit 1
fi

if [[ ! -d "$LOG_DIR" ]]; then
    echo "Hata: $LOG_DIR bir klasor degil ya da bulunamadi" >&2
    exit 1
fi

TMPFILE=$(mktemp)
trap "rm -f $TMPFILE" EXIT

awk -v idx="$SHARD_INDEX" -v total="$SHARD_TOTAL" '
{
    line_no = NR - 1
    if (line_no % total != idx) next
    ip = $1
    status = $9
    endpoint = $7
    latency = $NF
    ts = $4
    gsub(/^\[/, "", ts)
    split(ts, parts, ":")
    hour = parts[2]
    print ip "\t" status "\t" endpoint "\t" latency "\t" hour
}
' "$LOG_DIR"/*.log > "$TMPFILE"

TOTAL_LINES=$(wc -l < "$TMPFILE" | tr -d ' ')

STATUS_JSON=$(cut -f2 "$TMPFILE" | sort | uniq -c | sort -rn | \
    awk '{printf "%s\"%s\": %d", (NR>1?",":""), $2, $1}')

TOP_IPS_JSON=$(cut -f1 "$TMPFILE" | sort | uniq -c | sort -rn | head -10 | \
    awk '{printf "%s{\"ip\": \"%s\", \"count\": %d}", (NR>1?",":""), $2, $1}')

ENDPOINT_JSON=$(awk -F'\t' '
{
    count[$3]++
    sum[$3] += $4
}
END {
    first=1
    for (e in count) {
        avg = sum[e] / count[e]
        printf "%s\"%s\": {\"count\": %d, \"avg_latency\": %.3f}", (first?"":","), e, count[e], avg
        first=0
    }
}' "$TMPFILE")

HOURLY_JSON=$(cut -f5 "$TMPFILE" | sort | uniq -c | sort -k2 | \
    awk '{printf "%s\"%s\": %d", (NR>1?",":""), $2, $1}')

cat << JSON_EOF
{
  "shard_index": $SHARD_INDEX,
  "shard_total": $SHARD_TOTAL,
  "total_lines": $TOTAL_LINES,
  "status_distribution": {$STATUS_JSON},
  "top_10_ips": [$TOP_IPS_JSON],
  "endpoints": {$ENDPOINT_JSON},
  "hourly_density": {$HOURLY_JSON}
}
JSON_EOF
