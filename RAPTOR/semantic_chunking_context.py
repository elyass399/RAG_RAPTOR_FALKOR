import os, json, requests, re, time
import numpy as np
from pathlib import Path
from dotenv import load_dotenv
from sklearn.metrics.pairwise import cosine_similarity


# ──────────────────────────────────────────────────────────────────────────
# BLOCCO 1: SETUP E CONFIGURAZIONE (L'infrastruttura)
# ──────────────────────────────────────────────────────────────────────────
load_dotenv()
# Proxy per la rete aziendale Zucchetti
proxy_string = f"http://{os.getenv('PROXY_USER', '').replace('@', '%40')}:{os.getenv('PROXY_PASS', '')}@{os.getenv('PROXY_HOST', '')}"
PROXIES = {"http": proxy_string, "https": proxy_string}
HEADERS = {"Authorization": f"Bearer {os.getenv('LITELLM_API_KEY')}", "Content-Type": "application/json"}
BASE_URL = os.getenv("LITELLM_BASE_URL", "").rstrip("/")

# ──────────────────────────────────────────────────────────────────────────
# BLOCCO 2: "SET DI ATTREZZI" (Funzioni di supporto)
# ──────────────────────────────────────────────────────────────────────────
def call_api(endpoint, payload, timeout=60):
    """Bridge di comunicazione verso il server LiteLLM."""
    try:
        resp = requests.post(f"{BASE_URL}/{endpoint}", json=payload, headers=HEADERS, proxies=PROXIES, timeout=timeout, verify=False)
        return resp.json() if resp.status_code == 200 else None
    except Exception: return None

def get_embedding(text):
    """Trasforma un testo in un vettore numerico."""
    data = call_api("embeddings", {"model": os.getenv("EMBEDDING_MODEL", "nomic-embed-text:latest"), "input": text}, timeout=30)
    return np.array(data['data'][0]['embedding']) if data else None

def get_clean_sentences(text):
    """Fase 1: Pulizia e protezione (Tabelle e Abbreviazioni)."""
    # Protegge le tabelle nascondendo i newline
    text = re.sub(r'((?:\|.*\n)+)', lambda m: m.group(0).replace('\n', ' [TAB_NL] '), text)
    # Protegge le abbreviazioni
    text = re.sub(r'\b([a-zA-Z0-9]{1,3})\.(?!\s+[A-Z])', r'\1#DOT#', text)
    # Pulizia spazi e split frasi
    text = re.sub(r'(?<!\n)\n(?!\n)', ' ', text)
    segments = re.split(r'\.\s+(?=[A-Z])', text)
    # Ripristino e filtraggio
    return [s.replace('#DOT#', '.').replace(' [TAB_NL] ', '\n').strip() + "." for s in segments if len(s.strip()) > 15]

def global_optimal_chunking(matrix, min_sents=2, max_sents=8):
    """Fase 2: Programmazione Dinamica per il taglio ottimale."""
    n = len(matrix)
    dp = [-float('inf')] * (n + 1); dp[0] = 0; path = [0] * (n + 1)
    for i in range(1, n + 1):
        for size in range(min_sents, max_sents + 1):
            j = i - size
            if j < 0: continue
            reward = np.mean(matrix[j:i, j:i])
            if dp[j] + reward > dp[i]:
                dp[i] = dp[j] + reward; path[i] = j
    splits = []; curr = n
    while curr > 0:
        splits.append((path[curr], curr)); curr = path[curr]
    return sorted(splits)

# ──────────────────────────────────────────────────────────────────────────
# BLOCCO 3: ORCHESTRATORE (Il flusso di elaborazione)
# ──────────────────────────────────────────────────────────────────────────
def run_late_chunking_ingestion():
    INPUT_DIR = Path("./manuals")
    OUTPUT_DIR = Path("./output_smntc")
    OUTPUT_DIR.mkdir(exist_ok=True)

    for manual in INPUT_DIR.glob("*.md"):
        print(f"\n🚀 Elaborazione: {manual.name}")
        
        # 1. LETTURA E PULIZIA
        content = manual.read_text(encoding="utf-8", errors="replace")
        sentences = get_clean_sentences(content)
        
        # 2. EMBEDDING DELLE FRASI
        print(f"   🧠 Vettorizzazione di {len(sentences)} frasi...")
        embs = [get_embedding(s) for s in sentences]
        embs = [e for e in embs if e is not None]
        
        # 3. MATRICE E OTTIMIZZAZIONE (Programmazione Dinamica)
        matrix = cosine_similarity(np.array(embs))
        optimal_splits = global_optimal_chunking(matrix)

        # 4. SALVATAGGIO (Confezionamento finale)
        manual_out = OUTPUT_DIR / manual.stem
        os.makedirs(manual_out, exist_ok=True)
        for idx, (start, end) in enumerate(optimal_splits, 1):
            chunk_text = " ".join(sentences[start:end])
            chunk_data = {
                "id": f"{manual.stem}_{idx:03d}",
                "vector": get_embedding(chunk_text).tolist(),
                "text": chunk_text,
                "source": manual.name
            }
            with open(manual_out / f"{manual.stem}_{idx:03d}.json", "w", encoding="utf-8") as f:
                json.dump(chunk_data, f, ensure_ascii=False, indent=4)
        print(f"   ✅ Chunk completati.")

if __name__ == "__main__":
    run_late_chunking_ingestion()