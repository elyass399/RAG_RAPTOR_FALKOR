import os, json, time, subprocess
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
os.environ["no_proxy"] = "localhost,127.0.0.1,padova.zucchettitest.it"

BASE_URL  = os.getenv("LITELLM_BASE_URL", "").rstrip("/")
API_KEY   = os.getenv("LITELLM_API_KEY")
MODEL_EMB = os.getenv("EMBEDDING_MODEL", "nomic-embed-text:latest")

def _ps_invoke_sync(url_suffix, payload):
    url = f"{BASE_URL}/{url_suffix}"
    stamp = f"{int(time.time() * 1000)}_{os.getpid()}"
    req_f = Path(f"req_{stamp}.json").absolute()
    res_f = Path(f"res_{stamp}.bin").absolute()

    with open(req_f, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    ps_cmd = (f'$u="{url}"; $h=@{{"Authorization"="Bearer {API_KEY}";"Content-Type"="application/json"}}; '
              f'$b=[System.IO.File]::ReadAllBytes("{req_f}"); try{{$r=Invoke-WebRequest -Uri $u -Method Post '
              f'-Headers $h -Body $b -Proxy $null -UseBasicParsing -TimeoutSec 60; '
              f'[System.IO.File]::WriteAllBytes("{res_f}", $r.RawContentStream.ToArray())}} catch{{}}')

    subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True)

    data = None
    if res_f.exists():
        with open(res_f, "rb") as f:
            try: data = json.loads(f.read().decode("utf-8-sig"))
            except: data = None
        for fp in [req_f, res_f]:
            try: fp.unlink()
            except: pass
    return data

def get_embedding(text: str) -> list | None:
    res = _ps_invoke_sync("embeddings", {"model": MODEL_EMB, "input": text})
    if res and 'data' in res:
        return res['data'][0]['embedding']
    return None

def enrich_vectors():
    OUTPUT_DIR = Path("output_raptor")
    targets = [f for f in OUTPUT_DIR.glob("L*.json")]

    print(f"🔍 Trovati {len(targets)} file L1/L2 da controllare...")

    enriched = 0
    skipped  = 0
    errors   = 0

    for f_path in targets:
        data = json.loads(f_path.read_text(encoding="utf-8"))

        # Salta se il vettore esiste già ed è valido
        if data.get("vector") and isinstance(data["vector"], list) and len(data["vector"]) > 0:
            print(f"   ⏭️  {f_path.name} — vettore già presente, salto.")
            skipped += 1
            continue

        text = data.get("text", "").strip()
        if not text:
            print(f"   ⚠️  {f_path.name} — testo vuoto, salto.")
            errors += 1
            continue

        print(f"   🧠 Calcolo embedding per {f_path.name}...")
        vector = get_embedding(text)

        if vector:
            data["vector"] = vector
            f_path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
            print(f"   ✅ {f_path.name} — vettore aggiunto ({len(vector)} dim).")
            enriched += 1
        else:
            print(f"   ❌ {f_path.name} — errore embedding, salto.")
            errors += 1

        time.sleep(0.3)  # pausa cortesia verso il server

    print(f"\n🏆 Completato: {enriched} arricchiti | {skipped} già ok | {errors} errori")

if __name__ == "__main__":
    enrich_vectors()