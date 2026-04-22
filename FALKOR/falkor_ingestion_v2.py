import os, json, requests, re, time
from pathlib import Path
from dotenv import load_dotenv
from falkordb import FalkorDB
from pydantic import BaseModel
from typing import List
from collections import defaultdict

# --- 1. CONFIGURAZIONE E RETE ---
load_dotenv()

user = os.getenv('PROXY_USER', '').replace('@', '%40')
password = os.getenv('PROXY_PASS', '')
host = os.getenv('PROXY_HOST', '')
proxy_string = f"http://{user}:{password}@{host}"
PROXIES = {"http": proxy_string, "https": proxy_string}

session = requests.Session()
session.proxies = PROXIES
session.verify = False

HEADERS = {
    "Authorization": f"Bearer {os.getenv('LITELLM_API_KEY')}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
}

db = FalkorDB(host='localhost', port=6381)
LLM_MODEL = "gemma3:27b"

# --- 2. MODELLI DATI ---
class Triplet(BaseModel):
    s: str
    r: str
    o: str

class TripletList(BaseModel):
    triplets: List[Triplet]

# --- 3. API ---
def call_api(payload, timeout=900):
    url = f"{os.getenv('LITELLM_BASE_URL').rstrip('/')}/chat/completions"
    for attempt in range(2):
        try:
            resp = session.post(url, json=payload, headers=HEADERS, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
            print(f"   ⚠️ Errore Server ({resp.status_code}): {resp.text}")
        except requests.exceptions.ReadTimeout:
            print(f"   ⏳ Timeout. Ritento ({attempt+1}/2)...")
        except Exception as e:
            print(f"   ⚠️ Errore API: {e}")
    return None

def extract_triplets(text):
    prompt = (
        "Estrai relazioni tecniche dal testo. Rispondi SOLO in JSON con questo schema esatto:\n"
        '{"triplets": [{"s": "Soggetto", "r": "Relazione", "o": "Oggetto"}]}\n'
        "Nessun testo prima o dopo il JSON.\n\n"
        f"Testo:\n{text[:3000]}"
    )
    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0
    }
    data = call_api(payload)
    if data:
        try:
            content = data['choices'][0]['message']['content']
            content = re.sub(r'^```(?:json)?', '', content.strip(), flags=re.MULTILINE)
            content = re.sub(r'```$', '', content.strip(), flags=re.MULTILINE).strip()
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                return TripletList(**json.loads(match.group())).triplets
        except Exception as e:
            print(f"   ⚠️ Errore parsing triplets: {e}")
    return []

# --- 4. HELPERS DI PULIZIA ---
def clean_node_name(text: str) -> str:
    cleaned = re.sub(r'[^A-Z0-9]', '_', str(text).upper().strip())
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    return cleaned

def clean_label(text: str) -> str:
    cleaned = re.sub(r'[^A-Z0-9_]', '', str(text).upper().replace(" ", "_"))
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    return cleaned or "CORRELATO_A"

# --- 5. SCRITTURA SICURA ---
def write_triplet_to_graph(graph, chunk_id: str, source: str, s_name: str, o_name: str, r_type: str):
    query = f"""
        MERGE (c1:Concept {{name: $s_name}})
        MERGE (c2:Concept {{name: $o_name}})
        MERGE (c1)-[:{r_type} {{source: $source}}]->(c2)
        WITH c1, c2
        MATCH (ch:Chunk {{id: $chunk_id}})
        MERGE (ch)-[:CONTIENE]->(c1)
        MERGE (ch)-[:CONTIENE]->(c2)
    """
    graph.query(query, {"s_name": s_name, "o_name": o_name, "source": source, "chunk_id": chunk_id})

# --- 6. INGESTIONE ---
def run_ingestion():
    INPUT_DIR = Path("./output_late_chunking")
    files = sorted(list(INPUT_DIR.rglob("*.json")))

    if not files:
        print("❌ Nessun file trovato in ./output_late_chunking")
        return

    manuals: dict = defaultdict(list)
    for f_path in files:
        with open(f_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        source = data.get('source', 'Unknown')
        domain = re.sub(r'[^A-Z0-9_]', '_', Path(source).stem.upper().replace("-", "_"))
        domain = re.sub(r'_+', '_', domain).strip('_')
        manuals[domain].append((f_path.stem, data))

    print(f"📚 Manuali individuati: {list(manuals.keys())}")

    for domain, chunk_list in manuals.items():
        graph_name = f"Zucchetti_{domain}"
        graph = db.select_graph(graph_name)

        print(f"\n🧨 [GRAFO: {graph_name}] Reset e ingestione di {len(chunk_list)} chunk...")
        graph.query("MATCH (n) DETACH DELETE n")

        for chunk_id, data in chunk_list:
            text   = data.get('text', '')
            source = data.get('source', 'Unknown')

            # Testo COMPLETO salvato nel chunk (fix critico)
            graph.query(
                "CREATE (:Chunk {id: $id, content: $content, source: $source})",
                {"id": chunk_id, "content": str(text), "source": source}
            )

            print(f"   🧠 Elaborazione chunk: {chunk_id}...")
            start_t = time.time()
            triplets = extract_triplets(text)
            print(f"   📄 {chunk_id} -> {len(triplets)} relazioni ({time.time() - start_t:.1f}s)")

            for t in triplets:
                s_name = clean_node_name(t.s)
                o_name = clean_node_name(t.o)
                r_type = clean_label(t.r)

                if not s_name or not o_name:
                    continue

                try:
                    write_triplet_to_graph(graph, chunk_id, source, s_name, o_name, r_type)
                except Exception as e:
                    print(f"      ❌ Errore Cypher [{s_name} -[{r_type}]-> {o_name}]: {e}")

            time.sleep(0.5)

        print(f"✅ Dominio [{domain}] completato.")

    print("\n🏆 TUTTI I GRAFI COMPLETATI!")

if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    run_ingestion()