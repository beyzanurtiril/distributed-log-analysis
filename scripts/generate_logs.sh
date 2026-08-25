#!/bin/bash
SIZE=""
FILES=1
while [[ $# -gt 0 ]]; do
    case $1 in
        --size) SIZE="$2"; shift 2 ;;
        --files) FILES="$2"; shift 2 ;;
        *) echo "Bilinmeyen parametre: $1"; exit 1 ;;
    esac
done
if [[ -z "$SIZE" ]]; then
    echo "Hata: --size parametresi zorunlu (ornek: --size 1000)"
    exit 1
fi
mkdir -p logs
ENDPOINTS=("/api/users" "/api/products" "/api/orders" "/login" "/home" "/api/cart" "/static/style.css" "/api/search")
METHODS=("GET" "GET" "GET" "GET" "POST" "PUT" "DELETE")
random_ip() {
    echo "$((RANDOM % 223 + 1)).$((RANDOM % 255)).$((RANDOM % 255)).$((RANDOM % 255))"
}
random_status() {
    local r=$((RANDOM % 100))
    if [[ $r -lt 85 ]]; then echo 200
    elif [[ $r -lt 93 ]]; then echo 404
    elif [[ $r -lt 98 ]]; then echo 500
    else
        local others=(301 302 403)
        echo "${others[$((RANDOM % 3))]}"
    fi
}
random_latency() {
    local r=$((RANDOM % 100))
    local ms
    if [[ $r -lt 80 ]]; then
        ms=$((RANDOM % 290 + 10))
    elif [[ $r -lt 95 ]]; then
        ms=$((RANDOM % 1200 + 300))
    else
        ms=$((RANDOM % 3500 + 1500))
    fi
    printf "%d.%03d" $((ms / 1000)) $((ms % 1000))
}
random_bytes() {
    echo $((RANDOM % 9000 + 200))
}
generate_file() {
    local filename=$1
    local line_count=$2
    > "$filename"
    local base_epoch=$(date -d "2026-03-15 08:00:00" +%s)
    local current_epoch=$base_epoch
    local j
    for ((j=0; j<line_count; j++)); do
        local ip=$(random_ip)
        local method=${METHODS[$((RANDOM % ${#METHODS[@]}))]}
        local endpoint=${ENDPOINTS[$((RANDOM % ${#ENDPOINTS[@]}))]}
        local status=$(random_status)
        local bytes=$(random_bytes)
        local latency=$(random_latency)
        current_epoch=$((current_epoch + RANDOM % 5 + 1))
        local ts=$(date -d "@$current_epoch" "+%d/%b/%Y:%H:%M:%S +0300")
        echo "${ip} - - [${ts}] \"${method} ${endpoint} HTTP/1.1\" ${status} ${bytes} ${latency}" >> "$filename"
    done
}
echo "Toplam $FILES dosya uretiliyor, hedef toplam satir sayisi: $SIZE"
declare -a FILE_SIZES
total_weight=0
for ((k=0; k<FILES; k++)); do
    weight=$((RANDOM % 3 + 1))
    FILE_SIZES[$k]=$weight
    total_weight=$((total_weight + weight))
done
remaining=$SIZE
for ((k=0; k<FILES; k++)); do
    if [[ $k -eq $((FILES - 1)) ]]; then
        lines=$remaining
    else
        lines=$((SIZE * FILE_SIZES[k] / total_weight))
        remaining=$((remaining - lines))
    fi
    filename="logs/access_$((k+1)).log"
    echo "Uretiliyor: $filename ($lines satir)"
    generate_file "$filename" "$lines"
done
echo "Tamamlandi. Dosyalar logs/ klasorunde."
ls -lh logs/
