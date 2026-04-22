import os, requests, re
from dotenv import load_dotenv
from falkordb import FalkorDB

load_dotenv()
user = os.getenv('PROXY_USER', '').replace('@', '%40')
password = os.getenv('PROXY_PASS', '')
host = os.getenv('PROXY_HOST', '')
proxy_string = f"http://{user}:{password}@{host}"
PROXIES = {"http": proxy_string, "https": proxy_string}

BASE_URL = os.getenv("LITELLM_BASE_URL", "").rstrip("/")
HEADERS = {
    "Authorization": f"Bearer {os.getenv('LITELLM_API_KEY')}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0"
}

db = FalkorDB(host='localhost', port=6381)
LLM_MODEL = "gemma3:4b"

# --- DISCOVERY GRAFI ---
def get_available_domains() -> list[str]:
    try:
        return [g for g in db.list_graphs() if g.startswith("Zucchetti_")]
    except Exception as e:
        print(f"⚠️ Impossibile listare i grafi: {e}")
        return []

# --- API ---
def call_api(payload, timeout=120):
    url = f"{BASE_URL}/chat/completions"
    try:
        resp = requests.post(url, json=payload, headers=HEADERS, proxies=PROXIES, timeout=timeout, verify=False)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"⚠️ Errore API: {e}")
    return None

# --- SELEZIONE DOMINIO ---
def detect_domain(query: str, domains: list[str]) -> str | None:
    domain_list = "\n".join(f"- {d}" for d in domains)
    prompt = (
        f"Hai questi grafi di conoscenza disponibili:\n{domain_list}\n\n"
        f"Domanda utente: '{query}'\n\n"
        "Rispondi SOLO con il nome esatto del grafo più rilevante (es. Zucchetti_PAGHE). "
        "Se nessuno è rilevante, rispondi NONE."
    )
    res = call_api({"model": LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "temperature": 0})
    if res:
        answer = res['choices'][0]['message']['content'].strip()
        for d in domains:
            if d in answer:
                return d
    return None

# --- CONTESTO DAL GRAFO ---
def get_graph_context(query: str, graph_name: str):
    graph = db.select_graph(graph_name)

    # Estrazione keyword dalla query
    extracted = re.findall(r'\b[A-Z0-9]{2,}\b|\b[a-zA-Z]{4,}\b', query)
    raw_words = [w.upper() for w in extracted]
    ignore = {"DOMANDA", "QUESTO", "QUALE", "QUANDO", "SISTEMA", "INFORMAZIONI",
              "PERIODO", "DURATA", "COSA", "COME", "DOVE", "PERCHE", "CHI"}
    keywords = [w for w in raw_words if w not in ignore]

    if not keywords:
        keywords = [w.upper() for w in re.findall(r'\w{2,}', query) if w.upper() not in ignore]

    # Costruisci varianti di ricerca: maiuscolo, minuscolo, capitalize
    search_variants: set[str] = set()
    for kw in keywords:
        search_variants.add(kw.upper())
        search_variants.add(kw.lower())
        search_variants.add(kw.capitalize())

    # Aggiungi bigram dalla query originale (es. "fuori gioco", "calcio angolo")
    words = re.findall(r'\w+', query.lower())
    for i in range(len(words) - 1):
        bigram = words[i] + " " + words[i + 1]
        search_variants.add(bigram)
        search_variants.add(bigram.upper())
        search_variants.add(bigram.capitalize())

    # Aggiungi trigram per frasi più lunghe
    for i in range(len(words) - 2):
        trigram = words[i] + " " + words[i + 1] + " " + words[i + 2]
        search_variants.add(trigram)
        search_variants.add(trigram.upper())

    # Scoring chunk sul testo completo
    candidate_chunks: dict[str, int] = {}
    for variant in search_variants:
        try:
            res = graph.query(
                "MATCH (ch:Chunk) WHERE ch.content CONTAINS $kw RETURN ch.content LIMIT 10",
                {"kw": variant}
            )
            for row in res.result_set:
                text = row[0]
                # Peso maggiore per match su frasi composte
                weight = 2 if " " in variant else 1
                candidate_chunks[text] = candidate_chunks.get(text, 0) + weight
        except Exception:
            pass

    # Fallback: cerca tramite nodi Concept collegati ai Chunk
    if not candidate_chunks:
        for kw in keywords:
            for variant in [kw.upper(), kw.lower(), kw.capitalize()]:
                try:
                    res = graph.query(
                        "MATCH (c:Concept)<-[:CONTIENE]-(ch:Chunk) "
                        "WHERE c.name CONTAINS $kw RETURN ch.content LIMIT 5",
                        {"kw": variant}
                    )
                    for row in res.result_set:
                        text = row[0]
                        candidate_chunks[text] = candidate_chunks.get(text, 0) + 1
                except Exception:
                    pass

    best_chunks = [c for c, _ in sorted(candidate_chunks.items(), key=lambda x: x[1], reverse=True)[:5]]

    # Relazioni logiche dai nodi Concept
    facts = []
    seen_facts: set[str] = set()
    for kw in keywords:
        for variant in [kw.upper(), kw.lower(), kw.capitalize()]:
            try:
                res = graph.query(
                    "MATCH (c:Concept)-[r]->(m:Concept) WHERE c.name CONTAINS $kw "
                    "RETURN c.name, type(r), m.name LIMIT 5",
                    {"kw": variant}
                )
                for row in res.result_set:
                    f = f"{row[0]} {row[1].replace('_', ' ')} {row[2]}"
                    if f not in seen_facts:
                        facts.append(f)
                        seen_facts.add(f)
            except Exception:
                pass

    # Ricerca frase intera nei Concept (es. FUORI_GIOCO)
    phrase = re.sub(r'[^A-Z0-9]', '_', query.upper()).strip('_')
    phrase = re.sub(r'_+', '_', phrase)
    try:
        res = graph.query(
            "MATCH (c:Concept)-[r]->(m:Concept) WHERE c.name CONTAINS $phrase "
            "RETURN c.name, type(r), m.name LIMIT 10",
            {"phrase": phrase}
        )
        for row in res.result_set:
            f = f"{row[0]} {row[1].replace('_', ' ')} {row[2]}"
            if f not in seen_facts:
                facts.append(f)
                seen_facts.add(f)
    except Exception:
        pass

    return facts[:20], best_chunks

# --- CHAT LOOP ---
def start_chat():
    print("\n🚀 ZUCCHETTI MULTIGRAPH RAG")
    domains = get_available_domains()

    if not domains:
        print("❌ Nessun grafo Zucchetti trovato. Esegui prima l'ingestione.")
        return

    print(f"📚 Grafi disponibili: {[d.replace('Zucchetti_', '') for d in domains]}")

    while True:
        user_input = input("\ndomanda : ").strip()
        if user_input.lower() in ["esci", "quit"]:
            break
        if not user_input:
            continue

        # 1. Selezione automatica del dominio
        selected = detect_domain(user_input, domains)
        if not selected:
            print("risposta ai : Non ho trovato un manuale pertinente per questa domanda.")
            continue

        print(f"   📂 Grafo selezionato: {selected}")

        # 2. Estrazione contesto
        facts, chunks = get_graph_context(user_input, selected)
        if not chunks:
            print("risposta ai : Informazione non presente nei manuali.")
            continue

        # 3. Costruzione prompt
        graph_txt = "\n".join(f"- {f}" for f in facts)
        docs_txt = "\n\n".join(chunks)
        sys_prompt = (
            "Sei l'assistente GraphRAG Zucchetti. "
            "Rispondi usando SOLO le informazioni nel CONTESTO e nelle RELAZIONI fornite. "
            "Se l'informazione non è presente, dillo chiaramente senza inventare."
        )
        user_prompt = (
            f"CONTESTO TESTUALE:\n{docs_txt}\n\n"
            f"RELAZIONI LOGICHE:\n{graph_txt}\n\n"
            f"DOMANDA: {user_input}"
        )

        payload = {
            "model": LLM_MODEL,
            "messages": [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0
        }
        res = call_api(payload)

        if res and 'choices' in res:
            answer = re.sub(r'</?end_of_turn>', '', res['choices'][0]['message']['content']).strip()
            print(f"risposta ai : {answer}")
        else:
            print("risposta ai : Errore di connessione al server.")

if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    start_chat()