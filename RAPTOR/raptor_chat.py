import os, json, requests, re, numpy as np
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny
from rank_bm25 import BM25Okapi
from sklearn.preprocessing import MinMaxScaler

# ─────────────────────────────────────────────
# 1. CONFIGURAZIONE
# ─────────────────────────────────────────────
load_dotenv()

# Proxy aziendale (codifica @ come %40)
user = os.getenv('PROXY_USER', '').replace('@', '%40')
password = os.getenv('PROXY_PASS', '')
host = os.getenv('PROXY_HOST', '')
proxy_string = f"http://{user}:{password}@{host}"
PROXIES = {"http": proxy_string, "https": proxy_string}

QDRANT_URL      = "http://localhost:6333"
COLLECTION_NAME = "zucchetti_raptor_kb"
BASE_URL        = os.getenv("LITELLM_BASE_URL", "").rstrip("/")
API_KEY         = os.getenv("LITELLM_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text:latest")
LLM_MODEL       = "gemma3:4b"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type":  "application/json",
    "User-Agent":    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
}

client = QdrantClient(url=QDRANT_URL)

# Pesi ricerca ibrida
SEMANTIC_WEIGHT = 0.6 
BM25_WEIGHT     = 0.4
CANDIDATES_L0   = 50
TOP_L0          = 5
MIN_RELEVANCE   = 0.15

# ─────────────────────────────────────────────
# 2. COMUNICAZIONE ROBUSTA (API)
# ─────────────────────────────────────────────
def call_api(endpoint, payload, timeout=60):
    url = f"{BASE_URL}/{endpoint}"
    try:
        resp = requests.post(url, json=payload, headers=HEADERS, proxies=PROXIES, timeout=timeout, verify=False)
        if resp.status_code == 200: return resp.json()
    except Exception: pass
    return None

def call_llm(context, question):
    system_instruction = (
        "Sei l'assistente Zucchetti. Rispondi in modo diretto, tecnico e sintetico basandoti ESCLUSIVAMENTE "
        "sul CONTESTO fornito. Se l'informazione non è nel contesto, rispondi esattamente: 'info non esiste'."
    )
    
    # Pulizia tag tecnici per il modello
    clean_context = context.replace("---[ Livello 0 ]---", "DETTAGLIO:").replace("---[ Livello 1 ]---", "RIASSUNTO:").replace("---[ Livello 2 ]---", "PANORAMICA:")

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"CONTESTO:\n{clean_context}\n\nDOMANDA: {question}"}
        ],
        "temperature": 0.1
    }
    data = call_api("chat/completions", payload, timeout=120)
    return re.sub(r'</?end_of_turn>', '', data['choices'][0]['message']['content']).strip() if data and 'choices' in data else "Errore di rete"

# ─────────────────────────────────────────────
# 3. LOGICA RAG (RICERCA E RECUPERO GERARCHICO)
# ─────────────────────────────────────────────
def search_l0(query_vec, query_text):
    # Ricerca vettoriale L0
    candidates = client.query_points(
        collection_name=COLLECTION_NAME, 
        query=query_vec, 
        query_filter=Filter(must=[FieldCondition(key="level", match=MatchValue(value=0))]), 
        limit=CANDIDATES_L0
    ).points
    
    if not candidates: return []

    # BM25 Reranking
    bm25 = BM25Okapi([d.payload.get("text", "").lower().split() for d in candidates])
    bm25_scores = bm25.get_scores(query_text.lower().split())
    
    # Normalizzazione 0-1
    scaler = MinMaxScaler()
    sem_norm = scaler.fit_transform(np.array([r.score for r in candidates]).reshape(-1, 1)).flatten()
    bm25_norm = scaler.fit_transform(np.array(bm25_scores).reshape(-1, 1)).flatten()
    
    # Fusione ibrida
    final_scores = SEMANTIC_WEIGHT * sem_norm + BM25_WEIGHT * bm25_norm 
    
    ranked = sorted(zip(final_scores, candidates), key=lambda x: x[0], reverse=True)
    return [d for s, d in ranked if s >= MIN_RELEVANCE][:TOP_L0]

def build_context(l0_nodes):
    """Recupera padri (L1/L2), deduplica e ordina dal generale al particolare."""
    p_ids = {n.payload.get(k) for n in l0_nodes for k in ("L1ID", "L2ID") if n.payload.get(k)}
    parents = []
    if p_ids:
        parents, _ = client.scroll(COLLECTION_NAME, scroll_filter=Filter(must=[FieldCondition(key="node_id", match=MatchAny(any=list(p_ids)))]), limit=20, with_payload=True)
    
    all_nodes = l0_nodes + parents
    seen, unique_nodes = set(), []
    for n in sorted(all_nodes, key=lambda x: x.payload.get("level", 0), reverse=True):
        if n.payload.get("node_id") not in seen:
            seen.add(n.payload.get("node_id"))
            unique_nodes.append(n)
            
    return "\n\n".join([f"---[ Livello {n.payload.get('level')} ]---\n{n.payload.get('text', '').strip()}" for n in unique_nodes[:4]])

# ─────────────────────────────────────────────
# 4. CHAT LOOP
# ─────────────────────────────────────────────
def start_chat():
    print("\n🚀 ZUCCHETTI RAG RAPTOR - Ready")
    while True:
        q = input("\ndomanda : ").strip()
        if q.lower() in ("esci", "quit"): break
        if not q: continue
        
        emb_data = call_api("embeddings", {"model": EMBEDDING_MODEL, "input": q}, timeout=30)
        if not emb_data or 'data' not in emb_data:
            print("risposta ai : Errore embedding.")
            continue
            
        vec = emb_data['data'][0]['embedding']
        l0 = search_l0(vec, q)
        
        if l0:
            context = build_context(l0)
            answer = call_llm(context, q)
            print(f"risposta ai : {answer}")
        else:
            print("risposta ai : info non esiste")

if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    start_chat()