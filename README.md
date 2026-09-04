# Dağıtık Log Analiz Sistemi

Büyük hacimli web sunucusu loglarını birden fazla worker pod'una dağıtarak paralel analiz eden bir sistem. Tek komutla worker'ları ayağa kaldırır, analizi çalıştırır ve sonuçları birleştirir.

---

## 1. Sistem Genel Bakışı

### Ne yapıyor

Elinizde büyük bir nginx access log dosyası (ya da birden fazla dosya) olduğunda, bu veriyi **N tane worker'a bölerek paralel işler** ve sonuçta tek bir birleşik rapor üretir: toplam istek sayısı, status code dağılımı, en çok istek atan IP'ler, endpoint bazında istek sayısı/ortalama gecikme/p95, saatlik yoğunluk.

### Mimari

```
┌──────────────────┐  iş parçalarını dağıtır   ┌──────────────────┐
│   Orchestrator    │ ────────────────────────> │  Worker Pod x N  │
│  (Python script)  │                           │  (Indexed Job)   │
│                    │ <──────────────────────── │                  │
└──────────────────┘   kısmi sonuçları toplar    └──────────────────┘
        │                                                  │
        └──────────────────── log verisi ───────────────────┘
                          (PVC / local-path)
```

- **Orchestrator** (`src/orchestrate.py`): Namespace/ConfigMap'i idempotent şekilde uygular, N tane worker içeren bir Indexed Job oluşturur, pod loglarını canlı izler, Job bitince her worker'ın kısmi JSON çıktısını toplayıp tek bir rapora birleştirir.
- **Worker** (`scripts/worker.sh` — bash, ve `src/worker.py` — Python): Kendisine ayrılan log satırlarını okuyup analiz eden, JSON çıktısı üreten script. İki versiyonu da repoda duruyor (bkz. Bölüm 3).
- **PVC (`log-data-pvc`, `local-path` storage class)**: Tüm worker'ların erişebildiği paylaşımlı disk alanı — log dosyaları burada, worker'ların kısmi çıktıları da (`job_output/`) burada tutulur.

### Kullanılan teknolojiler

k3s (hafif Kubernetes), Docker (Alpine tabanlı image), bash + Python, Kustomize (base/overlay), GitHub Actions (CI/CD, GHCR'a otomatik build+push).

---

## 2. İş Dağıtım Algoritması

### Statik sharding nasıl çalışıyor

Her worker'a iki ortam değişkeni veriliyor: `SHARD_INDEX` (kendi sırası) ve `SHARD_TOTAL` (toplam worker sayısı). Worker, tüm log dosyalarını **birleşik tek bir akış** gibi düşünüp, her satırın sırasına bakıyor:

```
satır_no % SHARD_TOTAL == SHARD_INDEX  →  bu satırı ben işlerim
```

Bu **satır bazlı** bir sharding — dosya bazlı değil (yani "worker 1, dosya_1'i alsın" gibi bir mantık **değil**). Bunun iki önemli sonucu var:

1. Dosya boyutları eşit olmasa bile (kasıtlı olarak `generate_logs.sh` eşitsiz üretiyor), her worker'a düşen toplam satır sayısı neredeyse birebir eşit çıkıyor — testte her worker tam 100.000 satır aldı, kusursuz denge.
2. Worker'lar dosyaları **aynı sırada** görmek zorunda (bash'te `sort`, Python'da `sorted()` ile alfabetik sıralanıyor) — aksi halde iki worker aynı satırı iki kere işleyebilir ya da hiç işlemeyebilir.

### Neden Indexed Job'a geçildi

İlk aşamada (Hafta 2) worker'lar bir **Deployment** ile açılıp `sleep infinity` ile boşta bekletiliyor, dışarıdan `kubectl exec` ile "sen SHARD_INDEX=2 ol, çalış" deniyordu. Bu yaklaşımın gerçek bir zayıflığını **worker çökme testinde** kanıtladık (bkz. Bölüm 6): bir worker iş ortasında öldürülünce, o shard'ın verisi **sessizce kayboldu** — Kubernetes yeni bir pod açtı ama o pod'a "eksik kalan shard'ı sen devral" diyen bir mekanizma yoktu, çünkü shard ataması tamamen dışarıdan, elle yapılıyordu.

**Indexed Job** bunu kökten çözüyor:
- Her pod'a Kubernetes'in kendisi otomatik bir numara (`JOB_COMPLETION_INDEX`) veriyor — dışarıdan atama yok.
- `backoffLimit` sayesinde bir pod çökerse, **aynı index'le** otomatik olarak yeniden deneniyor.
- Pod'lar kendi işini bitirip `Completed` oluyor, sonsuza kadar beklemiyor.

Tek dezavantajı: her çalıştırmada **sıfırdan yeni pod'lar** açılıyor, bu da (özellikle küçük/mütevazı bir cluster'da) belirgin bir başlangıç maliyeti getiriyor — bkz. Bölüm 4'teki Deployment vs Indexed Job karşılaştırması.

---

## 3. Bash vs Python Karşılaştırması

Aynı analiz mantığı hem `worker.sh` (bash + awk) hem `worker.py` (Python) ile yazıldı; bash versiyonu silinmedi, ikisi de repoda duruyor.

| Kriter | Bash (`worker.sh`) | Python (`worker.py`) |
|---|---|---|
| Çalışma süresi (300.000 satır, tek worker) | 6.66s | **3.06s** |
| Image boyutuna etkisi | Ekstra paket gerekmiyor | +python3 paketi (~55MB) |
| Kod yapısı | Zincirleme `awk`/`sort`/`grep` komutları | Regex + `dict`, daha yapısal |
| Latency histogram / p95 desteği | Zor (manuel bucket mantığı gerekir) | Doğal (`dict` ile kolay) |
| Hata yönetimi | Sınırlı (`set -e`, exit code) | Zengin (`try/except`, spesifik hata tipleri) |
| Ne zaman tercih edilir | Basit, hafif, tek seferlik görevler | Karmaşık mantık, büyüyecek/sürdürülecek kod |

**Neden Python daha hızlı:** Bash worker'ı `awk`, `sort`, `grep` gibi ayrı programları zincirleme çağırıp aralarında veri akıtıyor — her biri kendi başlatma maliyetine sahip. Python ise tek bir process içinde, dışarı çıkmadan, regex ile ayrıştırıp kendi belleğinde (`defaultdict`) topluyor.

`orchestrate.py`, Indexed Job'ı Python worker'ı (`python3 /app/worker.py`) çalıştıracak şekilde yapılandırılmış durumda — production akışı Python üzerinden ilerliyor, bash versiyonu referans/karşılaştırma amaçlı repoda kalıyor.

---

## 4. Ölçüm Sonuçları

### Temel ölçüm (1, 2, 3, 5 worker, 300.000 satırlık veri)

| Worker sayısı | Toplam süre | En hızlı worker | En yavaş worker | Fark |
|---|---|---|---|---|
| 1 | 7.815s | 7.729s | 7.729s | 0.000s |
| 2 | 6.260s | 4.568s | 6.121s | 1.553s |
| 3 | 4.945s | 4.734s | 4.844s | 0.110s |
| 5 | 5.350s | 4.226s | 5.223s | 0.997s |

**Hızlanma grafiği**, gerçek hızlanmanın ideal (doğrusal) hızlanmanın çok altında kaldığını gösteriyor — 5 worker'da gerçek hızlanma sadece **~1.46x**, ideal 5x'in çok altında. Toplam süre grafiğinde N=3'e kadar düşüş var, N=5'te hafifçe geri yükseliyor — 2 çekirdekli bu VM için "tatlı nokta" 3 worker civarında duruyor.

**Hızlanmanın neden doğrusal olmadığı:** VM'in sadece **2 fiziksel çekirdeği** var — 5 worker aynı anda "çalışsa" bile gerçekte yalnızca 2'si paralel işlenebiliyor. Buna ek olarak her `kubectl exec` çağrısının kendi maliyeti var ve k3s'in kendi arka plan servisleri (API server, containerd) de aynı kısıtlı CPU'yu paylaşıyor.

### CPU limit testi (limitli vs limitsiz)

| Worker | Limitli (500m CPU) | Limitsiz |
|---|---|---|
| 1 | 20.178s | 7.815s |
| 2 | 9.894s | 6.260s |
| 3 | 7.984s | 4.945s |
| 5 | 7.034s | 5.350s |

Limitli hâl her N'de belirgin şekilde daha yavaş. En çarpıcı fark N=1'de: tek worker tüm işi tek başına yaparken yarım çekirdeğe (500m) sıkışınca **~2.6 kat** yavaşladı — buna **CPU throttling** deniyor, Kubernetes limiti aşan container'ı zorla yavaşlatıyor.

### Indexed Job vs Deployment (aynı veriyle, limitsiz)

| Worker | Deployment | Indexed Job |
|---|---|---|
| 1 | 7.815s | 9.265s |
| 2 | 6.260s | 8.585s |
| 3 | 4.945s | 20.318s |
| 5 | 5.350s | 22.711s |

Indexed Job her N'de daha yavaş, fark N arttıkça katlanarak büyüyor. Sebep: Deployment'ta pod'lar zaten açık bekliyordu (sıfır başlangıç maliyeti); Job'da her defasında **sıfırdan yeni pod'lar** açılıyor ve bu maliyet, mütevazı/2 çekirdekli bir VM'de N ile birlikte katlanıyor. Yani "hangisi daha iyi" sorusunun cevabı duruma göre değişir: güvenilirlik önceliğiyse Job, ham hız önceliğiyse (ve pod'lar zaten hazır tutulabiliyorsa) Deployment.

### p95 nedir, neden worker.sh'ta (v1) eksikti

p95, "isteklerin %95'inin bu süreden daha hızlı tamamlandığı" değeri — ortalamanın aksine, en yavaş %5'lik "kuyruğu" gizlemiyor. `worker.sh` sadece `avg_latency` hesaplıyordu, p95 yoktu. Asıl teknik problem sharding ile birleşince ortaya çıkıyor: her worker kendi payı için ayrı bir p95 hesaplasa bile, **bu p95'lerin ortalamasını almak matematiksel olarak yanlış** bir global p95 verir (percentile değerleri doğrusal birleştirilemez). Çözüm: her endpoint için **latency histogram bucket'ları** (10ms, 50ms, 100ms, 250ms, 500ms, 1s, 2.5s, 5s, +Inf) tutmak — bucket'lar birer sayaç oldukları için worker'lar arası **toplanabiliyor**, birleşmiş bucket'lardan doğrusal interpolasyonla global bir p95 tahmini çıkarılabiliyor. Bu, `worker.py`'de uygulandı, `orchestrate.py`'nin `merge_results` adımında da global olarak yeniden hesaplanıyor.

### Yedi soru

**1. Neden 5 worker 5 kat hızlanma sağlamadı?**
1 worker 7.8s, 5 worker 5.4s sürdü (~1.46x, 5x değil). VM'in sadece 2 fiziksel çekirdeği olması, `kubectl exec`'in kendi maliyeti ve k3s'in arka plan servislerinin aynı CPU'yu paylaşması bunun başlıca sebepleri. Bu aslında **Amdahl Yasası**'nın klasik bir örneği: bir işin bir kısmı (dosya bulma, JSON birleştirme, orkestrasyon overhead'i) ne kadar worker eklersen ekle **paralelleştirilemiyor** — bu seri kısım, toplam hızlanmanın teorik bir üst sınırını dayatıyor, worker sayısını artırmak bu sınırın ötesine geçemiyor.

**2. En yavaş worker toplam süreyi nasıl belirliyor?**
Script `wait` ile hepsinin bitmesini bekliyor — toplam süre pratikte en yavaş worker'ın süresine eşit çıkıyor (N=1'de 7.815s toplam, 7.729s en yavaş — neredeyse birebir).

**3. Dosya boyutları eşit olsaydı sonuç nasıl değişirdi?**
Sharding satır bazlı olduğu için (dosya bazlı değil) dosya boyutlarının eşitsizliği zaten sonucu etkilemiyor — her worker'a düşen satır sayısı testte kusursuz eşit çıktı. Eğer sharding dosya bazlı olsaydı, büyük dosyayı alan worker diğerlerini bekletirdi.

**4. 2 vCPU'lu bu makinede 5 worker çalıştırmanın anlamı var mı?**
(Dokümanda örnek 4 vCPU deniyor ama gerçek VM 2 vCPU.) Çekirdek sayısını aşan worker sayısı gerçek paralel çalışamıyor, time-slicing'e giriyor — ölçtüğümüz "1.46x hızlanma, 5x değil" sonucu bunu doğruluyor; 3 worker civarı bu makine için daha optimal.

**5. CPU limitini kaldırırsanız ne olur?**
Ölçtük: limitli (500m) hâl her N'de daha yavaş, N=1'de ~2.6 kat — CPU throttling nedeniyle.

**6. Bir worker çalışma ortasında çökerse o shard'ın işi ne oluyor?**
(Deployment yaklaşımında) test ettik: worker'ı iş ortasında öldürünce, o shard'ın çıktı dosyası tamamen boş kaldı (exit code 137) — veri sessizce kayboldu, kimse tekrar üstlenmedi. Bu, Indexed Job'a geçişin en güçlü gerekçesi.

**7. Çalışma sırasında pod sayısını artırırsanız ne olur?**
Test ettik: deployment'ı 2'den 5'e büyütünce yeni 3 pod sağlıklı açıldı ama hiçbir işe yaramadı — zaten dağıtılmış işten habersiz, boşta beklediler. Mevcut işi büyütmek için sistemi baştan (yeni `SHARD_TOTAL` ile) tetiklemek gerekiyor.

---

## 5. Kurulum Adımları

Aşağıdaki adımlar, k3s + Docker kurulu bir Ubuntu Server VM üzerinde, sıfırdan çalışır.

```bash
# 1) Repoyu klonla
git clone https://github.com/beyzanurtiril/distributed-log-analysis.git
cd distributed-log-analysis

# 2) kubectl'in kubeconfig'i okuyabildiğinden emin ol
sudo chmod 644 /etc/rancher/k3s/k3s.yaml
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
# (kalıcı olması için ~/.bashrc'ye de ekleyin)
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $USER:$USER ~/.kube/config
chmod 600 ~/.kube/config

# 3) Docker image'ını build et
docker build -t log-worker:v1 .

# 4) Image'ı k3s'in kendi imaj deposuna (containerd) aktar
#    (Docker ve k3s AYRI depolar kullanır, bu adım atlanamaz)
docker save log-worker:v1 -o /tmp/log-worker.tar
sudo k3s ctr images import /tmp/log-worker.tar

# 5) Test verisi üret (300.000 satır, 5 dosya, kasıtlı eşitsiz boyutlarda)
./scripts/generate_logs.sh --size 300000 --files 5

# 6) k8s kaynaklarını uygula (Kustomize ile, "dev" senaryosu: az worker/küçük veri)
kubectl apply -k k8s/overlays/dev
#    ya da "test" senaryosu: çok worker/büyük veri
#    kubectl apply -k k8s/overlays/test

# 7) Log verisini PVC'ye kopyala (bir worker pod'u üzerinden)
kubectl scale deployment log-worker -n log-analysis --replicas=1
kubectl wait --for=condition=Ready pod -l app=log-worker -n log-analysis --timeout=60s
POD=$(kubectl get pods -n log-analysis -l app=log-worker -o jsonpath='{.items[0].metadata.name}')
kubectl cp logs/. log-analysis/$POD:/data/logs/

# 8) Orchestrator'ı çalıştır (Indexed Job'ı oluşturur, izler, sonuçları birleştirir)
pip install kubernetes --break-system-packages
cd src
python3 orchestrate.py --workers 3
```

Çıktı, birleşmiş JSON raporu olarak ekrana basılır: toplam satır sayısı, status dağılımı, endpoint bazlı istek/gecikme/p95, saatlik yoğunluk.

**CI/CD:** `main` branch'ine her push'ta GitHub Actions otomatik olarak image'ı build edip GHCR'a (`ghcr.io/beyzanurtiril/distributed-log-analysis`) `latest` ve commit SHA etiketleriyle gönderiyor, Kustomize'ın image tag'ını günceller. k3s cluster'ına gerçek deploy hâlâ elle yapılıyor — cluster yerel bir VM'de olduğu için GitHub'ın erişimi yok, bu bilinçli bir sınırlama.

---

## 6. Karşılaşılan Sorunlar ve Çözümleri

**1) `bc` ile latency üretiminde hesap hatası.** `generate_logs.sh`'ta `bc` komutu `scale=3` ile latency değeri üretirken modulo işlemi yanlış sonuç veriyordu — üretilen değerlerin ~%81'i tam olarak `0.010` çıkıyordu, "log-normal dağılım" isteniyordu ama sonuç sabit bir değere sıkışmıştı. **Çözüm:** `bc`'yi tamamen bırakıp saf bash tamsayı aritmetiğine geçtik (`ms=$((RANDOM % 290 + 10))`, sonra `printf "%d.%03d"` ile formatladık).

**2) Docker ve k3s ayrı imaj depoları kullanıyor.** `docker build` ile üretilen image k3s pod'ları tarafından bulunamadı (`ImagePullBackOff`/`ErrImagePull`) çünkü k3s, Docker'ın deposunu değil kendi `containerd` deposunu kullanıyor. **Çözüm:** `docker save ... -o image.tar` ile image'ı taşınabilir bir dosyaya çıkarıp `sudo k3s ctr images import image.tar` ile k3s'in kendi deposuna aktarmak — her Dockerfile/kod değişikliğinde bu iki komutu tekrarlamak gerekiyor.

**3) Non-root container, PVC'de dosya silme izni vermiyor.** `kubectl exec ... -- rm -rf /data/logs` komutu `Permission denied` verdi çünkü container non-root (`worker`) kullanıcıyla çalışıyor, PVC'nin ilk sahibi ise root. **Çözüm:** Klasörü silmeye çalışmak yerine, aynı isimli dosyaların üzerine `kubectl cp` ile yazmak (silme yetkisi gerekmiyor, sadece yazma yetkisi yetiyor).

**4) Küçük test verisiyle "ters" ölçüm sonucu.** İlk ölçüm turunda worker sayısı arttıkça toplam süre **azalmak yerine arttı** — beklenenin tam tersi. Sebep: log dosyaları çok küçüktü (birkaç yüz satır), her `kubectl exec` çağrısının kendi başlatma maliyeti gerçek işin süresinden daha uzundu, yani "paralelliğin faydasını" değil "orkestrasyon maliyetinin toplamını" ölçmüştük. **Çözüm:** veri setini 300.000 satıra büyütmek — gerçek iş yükü, orkestrasyon overhead'ini bastıracak kadar büyük hale geldi.

**5) `kubernetes` Python kütüphanesinin `stream()` fonksiyonu içeriği bozuyor.** `orchestrate.py`'de pod içinden dosya okurken kullanılan `stream()` fonksiyonu, JSON içeriğini garip bir şekilde (çift tırnak yerine tek tırnaklı, Python dict benzeri) döndürüyordu — bu da `json.loads()`'ta parse hatasına yol açıyordu. Aynı dosyayı düz `kubectl exec` ile okuyunca içerik doğru çıktı. **Çözüm:** `stream()`'i tamamen bırakıp, `subprocess` ile doğrudan `kubectl exec` komutunu çağırmak.

**6) `worker.py`'nin log satırları ile asıl JSON sonucu aynı yere (stdout) karışıyordu.** Job komutu stdout'u doğrudan bir dosyaya yönlendirdiği (`> shard_N.json`) için, hem işlem logları hem asıl sonuç aynı dosyaya yazılıyor, dosya geçersiz JSON haline geliyordu. **Çözüm:** logging çıktısını `stderr`'e, asıl JSON sonucunu `stdout`'ta bırakmak — bu ayrıca Job pod'larının canlı log akışının (`orchestrate.py`'deki `stream_pod_logs`) da düzgün çalışmasını sağladı.

**7) Kubernetes Job spec'i oluşturulduktan sonra değiştirilemiyor.** Aynı isimli bir Job'ı farklı `completions`/`parallelism` değerleriyle tekrar `apply` etmeye çalışınca "field is immutable" hatası alındı. **Çözüm:** her farklı worker sayısı denemesinden önce eski Job'ı `kubectl delete job ... --ignore-not-found=true` ile silmek, sonra yeniden oluşturmak.

---

## 7. Neyi Farklı Yapardım

- **Ölçümlere en başta büyük bir veri setiyle başlardım.** Küçük veriyle aldığımız ilk tur tamamen yanıltıcı çıktı (worker sayısı arttıkça süre arttı) ve bu, zaman kaybına yol açtı. Veri boyutunu baştan yeterince büyük seçmek, bu tuzağı en başından önlerdi.
- **Deployment yerine en baştan Indexed Job'la başlardım.** Deployment + manuel `kubectl exec` yaklaşımının "worker çökerse veri sessizce kaybolur" zayıflığını ancak Hafta 3'te, özel bir testle keşfettik. Job'ın bu sorunu yapısal olarak çözdüğünü baştan bilseydik, Hafta 2'deki k8s manifestlerini doğrudan Job olarak tasarlayabilirdik.
- **stdout/stderr ayrımını en baştan tasarlardım.** worker.py'yi ilk yazarken loglama ile veri çıktısını aynı akışa (stdout) koymak, ilerleyen aşamada (orchestrate.py'nin sonuçları birleştirmesi) ciddi bir debug sürecine yol açtı. Bu ayrımı en baştan (ilk satırdan itibaren) yapmak, sonradan düzeltmekten çok daha ucuza gelirdi.
- **`random_ip` fonksiyonunu ağırlıklı bir havuzdan seçim yapacak şekilde tasarlardım.** Şu anki tamamen rastgele IP üretimi (~milyarlarca olası kombinasyon), "top 10 en çok istek atan IP" metriğini anlamsızlaştırıyor — neredeyse her IP sadece bir kez görünüyor. Sabit, küçük bir IP havuzundan ağırlıklı seçim yapmak, gerçek dünyadaki "birkaç IP çok sık istek atar" davranışını daha gerçekçi taklit ederdi.
- **Terminal oturum notlarımı Word yerine, repoya commit'lenen bir markdown dosyasında tutardım.** Word dosyasının bir kez kapanınca içeriğinin kaybolması (dosyanın kaydedilmeden kapatılması), o günün notlarının önemli bir kısmının yeniden üretilmesini gerektirdi — git ile versiyonlanan bir `.md` dosyası bu riski taşımazdı.
- **k3s ile Docker'ın ayrı imaj depoları kullandığını baştan bilseydim**, `docker save` + `k3s ctr images import` adımlarını bir script'e (ya da CI/CD pipeline'ına) gömüp, her build sonrası elle tekrarlamak zorunda kalmazdım.
