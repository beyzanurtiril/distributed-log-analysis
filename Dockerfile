FROM alpine:3.20

RUN apk add --no-cache bash gawk coreutils grep

RUN addgroup -S worker && adduser -S -G worker worker

WORKDIR /app

COPY scripts/worker.sh /app/worker.sh
RUN chmod +x /app/worker.sh

USER worker

ENTRYPOINT ["/app/worker.sh"]
