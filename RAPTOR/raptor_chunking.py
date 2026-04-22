import os, json, time, re, subprocess, logging, shutil
from pathlib import Path
from sklearn.mixture import GaussianMixture
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from dotenv import load_dotenv
import numpy as np

# ──────────────────────────────────────────────────────────────────────────
# 1. SETUP E LOGGING
# ──────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
os.environ["no_proxy"] = "localhost,127.0.0.1,padova.zucchettitest.it"

# Connessione al server Padova per LLM
BASE_URL    = os.getenv("LITELLM_BASE_URL", "").rstrip("/")
API_KEY     = os.getenv("LITELLM_API_KEY")
MODEL_LLM   = os.getenv("MODEL_SUMMARY", "gemma4:26b")
MODEL_EMB   = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")

# ──────────────────────────────────────────────────────────────────────────
# 2. BRIDGE POWERSHELL
# ──────────────────────────────────────────────────────────────────────────
def _ps_invoke_sync(url_suffix, payload):
    url = f"{BASE_URL}/{url_suffix}"
    stamp = f"{int(time.time() * 1000)}_{os.getpid()}"
    req_f = Path(f"req_{stamp}.json").absolute()
    res_f = Path(f"res_{stamp}.bin").absolute()
    
    with open(req_f, "w", encoding="utf-8") as f: json.dump(payload, f, ensure_ascii=False)
    
    ps_cmd = (f'$u="{url}"; $h=@{{"Authorization"="Bearer {API_KEY}";"Content-Type"="application/json"}}; '
              f'$b=[System.IO.File]::ReadAllBytes("{req_f}"); try{{$r=Invoke-WebRequest -Uri $u -Method Post '
              f'-Headers $h -Body $b -Proxy $null -UseBasicParsing -TimeoutSec 240; '
              f'[System.IO.File]::WriteAllBytes("{res_f}", $r.RawContentStream.ToArray())}} catch{{}}')
    
    subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True)
    
    data = None
    if res_f.exists():
        with open(res_f, "rb") as f: 
            try: data = json.loads(f.read().decode("utf-8-sig"))
            except: data = None
        for f in [req_f, res_f]: f.unlink()
    return data

# ──────────────────────────────────────────────────────────────────────────
# 3. MOTORE RAPTOR (Clustering e Riassunto)
# ──────────────────────────────────────────────────────────────────────────
def cluster_semantic_vectors(vectors, max_chunk_per_cluster=5):
    n_samples = len(vectors)
    if n_samples <= max_chunk_per_cluster: return {0: list(range(n_samples))}
    n_clusters = max(1, n_samples // max_chunk_per_cluster)
    gmm = GaussianMixture(n_components=n_clusters, random_state=42).fit_predict(vectors)
    clusters = {}
    for idx, label in enumerate(gmm):
        if label not in clusters: clusters[label] = []
        clusters[label].append(idx)
    return clusters

def summarize_nodes(texts, level_name):
    """
    Riassunto intelligente: se il testo è breve lo riassume subito, 
    se è lungo lo divide in parti, riassume le parti e poi sintetizza il tutto.
    """
    combined = "\n\n---\n\n".join(texts)
    LIMIT = 15000

    # CASO 1: Testo contenuto nei limiti (Standard)
    if len(combined) <= LIMIT:
        prompt = f"Riassumi questi testi tecnici mantenendo numeri e date, sii dettagliato. TESTI:\n{combined}"
        res = _ps_invoke_sync("chat/completions", {"model": MODEL_LLM, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1})
        return res['choices'][0]['message']['content'].strip() if res and 'choices' in res else ""

    # CASO 2: Testo troppo lungo (Doppia elaborazione)
    else:
        print(f"   ⚠️ Testo troppo lungo ({len(combined)} char) per {level_name}. Eseguo riassunto a blocchi...")
        mid = len(combined) // 2
        
        # Troviamo un punto di taglio sicuro (punto fermo) vicino alla metà
        split_point = combined.rfind('.', 0, mid)
        if split_point == -1: split_point = mid
        
        part1 = combined[:split_point+1]
        part2 = combined[split_point+1:]
        
        # Riassumiamo le due parti separatamente
        sum1 = summarize_nodes([part1], f"{level_name}_part1")
        sum2 = summarize_nodes([part2], f"{level_name}_part2")
        
        # Uniamo i due riassunti e facciamo la sintesi finale
        final_prompt = f"Unisci e sintetizza questi due riassunti tecnici mantenendo numeri e date:\n1) {sum1}\n2) {sum2}"
        res = _ps_invoke_sync("chat/completions", {"model": MODEL_LLM, "messages": [{"role": "user", "content": final_prompt}], "temperature": 0.1})
        
        return res['choices'][0]['message']['content'].strip() if res and 'choices' in res else f"{sum1}\n{sum2}"

# ──────────────────────────────────────────────────────────────────────────
# 4. ORCHESTRATORE
# ──────────────────────────────────────────────────────────────────────────
def run_raptor():
    print(f"\n🚀 AVVIO RAPTOR su: output_smntc")
    
    # Percorso ESATTO dove sono i tuoi dati
    CHUNKS_DIR = Path("output_smntc")
    OUTPUT_DIR = Path("output_raptor")
    OUTPUT_DIR.mkdir(exist_ok=True)
    
    if not CHUNKS_DIR.exists():
        print(f"❌ Errore: La cartella '{CHUNKS_DIR}' non esiste!"); return

    manuals_data = {}
    for f_path in CHUNKS_DIR.rglob("*.json"):
        with open(f_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            src = data.get("source", "Sconosciuto")
            if src not in manuals_data: manuals_data[src] = []
            manuals_data[src].append(data)

    if not manuals_data:
        print("❌ Nessun file JSON trovato!"); return

    for manual_name, chunks in manuals_data.items():
        print(f"\n🏗️ PIRAMIDE PER: {manual_name}")
        manual_folder = OUTPUT_DIR / Path(manual_name).stem
        manual_folder.mkdir(exist_ok=True)

#gmm sa solo leggere array numpy, quindi convertiamo i vettori in un formato compatibile
        l0_vectors = np.array([c['vector'] for c in chunks])
#elenco di tutti chunk originali in ordine, necessario per il clustering e il riassunto
        l0_texts = [c['text'] for c in chunks]

        # LIVELLO 0 (FOGLIE)
        for idx, chunk in enumerate(chunks):
            (manual_folder / f"L0_chunk_{idx:03d}.json").write_text(json.dumps(chunk, indent=4, ensure_ascii=False), encoding="utf-8")

        # LIVELLO 1 (CLUSTERS)
        l1_texts = []
        clusters = cluster_semantic_vectors(l0_vectors)
        for cluster_num, indices in clusters.items():
            cluster_texts = [l0_texts[i] for i in indices]
            summary = summarize_nodes(cluster_texts, f"L1_Cluster_{cluster_num}")
            if summary:
                l1_texts.append(summary)
                (manual_folder / f"L1_summary_{cluster_num:03d}.json").write_text(json.dumps({"level": 1, "text": summary}, indent=4, ensure_ascii=False), encoding="utf-8")

        # LIVELLO 2 (ROOT)
        if len(l1_texts) > 1:
            root = summarize_nodes(l1_texts, "L2_ROOT")
            (manual_folder / "L2_ROOT.json").write_text(json.dumps({"level": 2, "text": root}, indent=4, ensure_ascii=False), encoding="utf-8")

        print(f"✅ Piramide creata per {manual_name}")

if __name__ == "__main__":
    run_raptor()