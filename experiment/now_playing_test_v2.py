import requests
import time

STREAM_URL = "http://stream.radiomost.hu:8200/live.mp3"

session = requests.Session()
session.headers.update({
    "Icy-MetaData": "1",
    "User-Agent": "RadioMetadataExtractor/1.0",
})

print("Watching the StreamTitle changes (Ctrl+C stops)...")
last_seen = None

while True:
    try:
        r = session.get(STREAM_URL, stream=True, timeout=5)
        metaint = int(r.headers.get("icy-metaint", "0"))
        if not metaint:
            print("No icy-metaint header: the stream sends no ICY metadata.")
            break

        r.raw.read(metaint)
        length_byte = r.raw.read(1)
        meta_len = ord(length_byte) * 16 if length_byte else 0
        raw_meta = r.raw.read(meta_len).decode("iso-8859-2", "ignore") if meta_len else ""

        if raw_meta and raw_meta != last_seen:
            last_seen = raw_meta
            ts = time.strftime("%H:%M:%S")
            print(f"[{ts}] RAW: {raw_meta}")

        r.close()
    except Exception as e:
        print(f"[ERROR] {e}")

    time.sleep(5)