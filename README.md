```markdown
# 🧠 Zucchetti RAG System — GraphRAG + RAPTOR

Sistema di Retrieval-Augmented Generation (RAG) ibrido per la consultazione intelligente
di manuali tecnici aziendali. Combina due architetture complementari:
**FalkorDB GraphRAG** per le relazioni logiche e **RAPTOR** per la comprensione gerarchica.

---

## 📐 Architettura Generale

```
Manuali (.md)
      │
      ▼
┌─────────────────────┐
│  Semantic Chunking  │  ← Programmazione dinamica + embedding
│  (output_smntc/)    │
└─────────┬───────────┘
          │
    ┌─────┴──────┐
    ▼            ▼
┌────────┐  ┌─────────────┐
│ FALKOR │  │   RAPTOR    │
│  PATH  │  │    PATH     │
└────────┘  └─────────────┘
    │            │
    ▼            ▼
Estrazione   Clustering
Triplette    GMM L1/L2
(gemma 27b)  + Summary
    │            │
    ▼            ▼
FalkorDB     Qdrant
(per grafo)  (collezione)
    │            │
    └─────┬──────┘
          ▼
     Chat Engine
```

---

## 🗂️ Struttura del Progetto

```
├── manuals/                        # Manuali sorgente in formato .md
├── output_smntc/                   # Chunk semantici con vettori (input condiviso)
├── output_raptor/                  # Nodi RAPTOR (L0 foglie, L1 cluster, L2 root)
│
├── semantic_chunking_context.py    # Chunking con programmazione dinamica
│
├── # ── FALKOR PIPELINE ──
├── ingest.py                       # Estrazione triplette → FalkorDB (grafo per manuale)
├── chat.py                         # Chat GraphRAG multi-grafo
│
├── # ── RAPTOR PIPELINE ──
├── raptor_chunking.py              # Clustering GMM + riassunti gerarchici
├── enrich_vectors.py               # Aggiunge embedding ai nodi L1/L2
├── raptor_ingest.py                # Caricamento nodi su Qdrant
└── raptor_chat.py                  # Chat RAPTOR con ricerca ibrida BM25 + semantica
```

---

## ⚙️ Requisiti

### Servizi (Docker consigliato)
| Servizio   | Porta   | Uso                        |
|------------|---------|----------------------------|
| FalkorDB   | 6381    | Grafi GraphRAG per dominio |
| Qdrant     | 6333    | Vector store RAPTOR        |
| LiteLLM    | (env)   | Gateway LLM aziendale      |

### Python
```bash
pip install falkordb qdrant-client pydantic python-dotenv \
            scikit-learn numpy requests rank-bm25
```

### `.env`
```env
LITELLM_BASE_URL=http://...
LITELLM_API_KEY=...
EMBEDDING_MODEL=nomic-embed-text:latest
MODEL_SUMMARY=gemma3:27b
PROXY_USER=utente@azienda.it
PROXY_PASS=password
PROXY_HOST=172.16.x.x:8080
```

---

## 🚀 Pipeline Completa

### Step 1 — Chunking Semantico (comune a entrambi i path)
```bash
python semantic_chunking_context.py
```
Legge i `.md` da `manuals/`, applica programmazione dinamica sui vettori
e salva i chunk con embedding in `output_smntc/`.

---

### 🔷 Path A — FalkorDB GraphRAG

#### Step 2A — Ingestione Grafi
```bash
python ingest.py
```
Per ogni manuale crea un grafo isolato `Zucchetti_{MANUALE}` su FalkorDB,
estrae triplette `(Soggetto)-[Relazione]->(Oggetto)` via LLM 27B
e le salva come nodi `Concept` collegati ai `Chunk`.

#### Step 3A — Chat GraphRAG
```bash
python chat.py
```
Il sistema fa discovery automatico dei grafi disponibili, seleziona
il manuale pertinente via LLM, recupera chunk e relazioni logiche
e genera la risposta contestuale.

---

### 🔶 Path B — RAPTOR

#### Step 2B — Costruzione Piramide RAPTOR
```bash
python raptor_chunking.py
```
Raggruppa i chunk con Gaussian Mixture Model, genera riassunti
gerarchici (L1=cluster, L2=root) via LLM e salva tutto in `output_raptor/`.

#### Step 3B — Arricchimento Vettori L1/L2
```bash
python enrich_vectors.py
```
Calcola e aggiunge gli embedding ai nodi L1/L2 che ne sono privi.

#### Step 4B — Ingestione Qdrant
```bash
python raptor_ingest.py
```
Carica tutti i nodi (L0+L1+L2) su Qdrant nella collezione `zucchetti_raptor_kb`.

#### Step 5B — Chat RAPTOR
```bash
python raptor_chat.py
```
Ricerca ibrida BM25 + semantica sui nodi L0, risale la gerarchia
per recuperare i riassunti L1/L2 e costruisce un contesto
dal generale al particolare per la risposta finale.

---

## 🔍 Confronto tra i due sistemi

| Caratteristica        | FalkorDB GraphRAG         | RAPTOR                        |
|-----------------------|---------------------------|-------------------------------|
| Tipo di conoscenza    | Relazioni logiche         | Comprensione gerarchica       |
| Punto di forza        | "Come è collegato X a Y?" | "Spiegami X in dettaglio"     |
| Retrieval             | Keyword + Concept graph   | BM25 + Semantica + Gerarchia  |
| LLM ingestion         | gemma3:27b (triplette)    | gemma3:27b (riassunti)        |
| LLM chat              | gemma3:4b                 | gemma3:4b                     |
| Storage               | FalkorDB (grafo)          | Qdrant (vettori)              |
| Isolamento manuali    | Grafo per dominio         | Collezione unica con metadati |

---

## 📊 Risultati Osservati

- ✅ Risposte precise su domande fattuali dirette
- ✅ Risposte contestualizzate su domande complesse multi-paragrafo  
- ✅ Routing automatico sul manuale corretto (GraphRAG)
- ✅ Recupero gerarchico dal dettaglio alla panoramica (RAPTOR)
- ⚠️ Possibili timeout intermittenti del proxy aziendale (retry automatico)
- ⚠️ Abbassare `MIN_RELEVANCE` a `0.05` in `raptor_chat.py` se compaiono falsi "info non esiste"

---

## 🔒 Note di Sicurezza

- Tutte le query su FalkorDB usano parametri `$param` (no Cypher injection)
- Le credenziali sono gestite esclusivamente via `.env` (mai hardcoded)
- Il proxy aziendale viene bypassato per `localhost` tramite `no_proxy`
- I certificati SSL interni vengono gestiti con `verify=False` + soppressione warning

---

## 👤 Autore
Progetto sviluppato internamente per Zucchetti S.p.A.
```