import argparse
import requests
import logging
import time
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

def fetch_with_retry(url, max_retries):
    wait_times = [2, 4, 8]

    for attempt in range(1, max_retries + 1):
        try:
            logging.info(f"Deneme {attempt}/{max_retries} - {url} adresine istek atiliyor")
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            data = response.json()
            logging.info("Istek basarili")
            return data

        except requests.exceptions.RequestException as e:
            logging.warning(f"Deneme {attempt} basarisiz oldu. Hata: {e}")
            if attempt < max_retries:
                bekleme = wait_times[attempt - 1]
                logging.info(f"{bekleme} saniye bekleniyor...")
                time.sleep(bekleme)
            else:
                logging.error("Tum denemeler basarisiz oldu, cikiliyor")
                sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="HTTP istegi atan CLI")
    parser.add_argument("--url", required=True, help="Istek atilacak adres")
    parser.add_argument("--retries", type=int, default=3, help="Kac kez denensin")
    args = parser.parse_args()

    data = fetch_with_retry(args.url, args.retries)
    logging.info(f"Gelen veri: {data}")

if __name__ == "__main__":
    main()
