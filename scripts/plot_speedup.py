#!/usr/bin/env python3
import csv
import matplotlib.pyplot as plt

workers = []
total_times = []

with open("bench_output/summary.csv") as f:
    reader = csv.DictReader(f)
    for row in reader:
        workers.append(int(row["workers"]))
        total_times.append(float(row["total_time_sec"]))

t1 = total_times[0]
speedup = [t1 / t for t in total_times]
ideal = workers

plt.figure(figsize=(8, 6))
plt.plot(workers, speedup, marker="o", linewidth=2, label="Gercek hizlanma", color="#2563eb")
plt.plot(workers, ideal, linestyle="--", color="gray", label="Ideal (dogrusal) hizlanma")
plt.xlabel("Worker sayisi")
plt.ylabel("Hizlanma (Speedup = T(1) / T(N))")
plt.title("Worker Sayisina Gore Hizlanma")
plt.legend()
plt.grid(True, alpha=0.3)
plt.savefig("bench_output/speedup.png", dpi=150, bbox_inches="tight")
print("Grafik kaydedildi: bench_output/speedup.png")

plt.figure(figsize=(8, 6))
plt.plot(workers, total_times, marker="o", linewidth=2, color="#dc2626")
plt.xlabel("Worker sayisi")
plt.ylabel("Toplam sure (saniye)")
plt.title("Worker Sayisina Gore Toplam Sure")
plt.grid(True, alpha=0.3)
plt.savefig("bench_output/total_time.png", dpi=150, bbox_inches="tight")
print("Grafik kaydedildi: bench_output/total_time.png")
