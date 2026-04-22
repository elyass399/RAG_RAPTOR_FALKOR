# 🔍 Cypher Query Reference — FalkorDB ZucchettiGraph

Tutti i comandi da eseguire nella console FalkorDB (porta 6381).
Seleziona prima il grafo corretto: `Zucchetti_{DOMINIO}` (es. `Zucchetti_LE17REGOLEDELCALCIO`)

---

## 1. ESPLORAZIONE BASE

### Vedere tutto il grafo (limitato)
```cypher
MATCH (n)-[e]->(m) RETURN n, e, m LIMIT 1000
```

### Contare tutti i nodi
```cypher
MATCH (n) RETURN count(n) AS totale_nodi
```

### Contare per tipo di nodo
```cypher
MATCH (n:Concept) RETURN count(n) AS concetti
MATCH (n:Chunk)   RETURN count(n) AS chunk
```

### Contare tutti gli archi
```cypher
MATCH ()-[r]->() RETURN count(r) AS totale_relazioni
```

### Vedere tutti i tipi di relazione presenti
```cypher
MATCH ()-[r]->() RETURN DISTINCT type(r) AS tipo_relazione, count(r) AS occorrenze
ORDER BY occorrenze DESC
```

---

## 2. NAVIGAZIONE CONCETTI

### Trovare un concetto per nome (esatto)
```cypher
MATCH (c:Concept {name: 'ANNIBALE'}) RETURN c
```

### Cercare concetti che contengono una parola
```cypher
MATCH (c:Concept) WHERE c.name CONTAINS 'CAMPO' RETURN c.name LIMIT 20
```

### Vedere tutte le relazioni di un concetto (uscenti)
```cypher
MATCH (c:Concept {name: 'ANNIBALE'})-[r]->(m) RETURN c.name, type(r), m.name
```

### Vedere tutte le relazioni di un concetto (entranti)
```cypher
MATCH (m)-[r]->(c:Concept {name: 'ANNIBALE'}) RETURN m.name, type(r), c.name
```

### Vedere tutte le relazioni di un concetto (entranti + uscenti)
```cypher
MATCH (c:Concept {name: 'ANNIBALE'})-[r]-(m) RETURN c.name, type(r), m.name
```

### Top 20 concetti piu connessi (hub del grafo)
```cypher
MATCH (c:Concept)-[r]-()
RETURN c.name, count(r) AS connessioni
ORDER BY connessioni DESC
LIMIT 20
```

---

## 3. NAVIGAZIONE CHUNK

### Vedere tutti i chunk con il loro testo
```cypher
MATCH (ch:Chunk) RETURN ch.id, ch.source, ch.content LIMIT 20
```

### Trovare chunk che contengono una parola chiave
```cypher
MATCH (ch:Chunk) WHERE ch.content CONTAINS 'Annibale'
RETURN ch.id, ch.content
```

### Vedere i concetti estratti da un chunk specifico
```cypher
MATCH (ch:Chunk {id: 'storia_001'})-[:CONTIENE]->(c:Concept)
RETURN ch.id, c.name
```

### Trovare tutti i chunk di un manuale specifico
```cypher
MATCH (ch:Chunk) WHERE ch.source = 'le17regoledelcalcio.md'
RETURN ch.id, ch.content
ORDER BY ch.id
```

---

## 4. PERCORSI E RELAZIONI

### Trovare il percorso tra due concetti (fino a 3 salti)
```cypher
MATCH path = (a:Concept {name: 'ANNIBALE'})-[*1..3]->(b:Concept {name: 'ROMA'})
RETURN path
```

### Trovare tutti i concetti a 2 salti da un nodo
```cypher
MATCH (c:Concept {name: 'CARTAGINE'})-[*1..2]->(m:Concept)
RETURN DISTINCT m.name
```

### Vedere solo relazioni di un tipo specifico
```cypher
MATCH (c)-[r:SCONFIGGE]->(m) RETURN c.name, m.name
```

### Trovare concetti collegati attraverso un chunk comune
```cypher
MATCH (c1:Concept)<-[:CONTIENE]-(ch:Chunk)-[:CONTIENE]->(c2:Concept)
WHERE c1.name <> c2.name
RETURN c1.name, ch.id, c2.name
LIMIT 30
```

---

## 5. STATISTICHE E ANALISI

### Distribuzione relazioni per tipo
```cypher
MATCH ()-[r]->()
RETURN type(r) AS relazione, count(r) AS totale
ORDER BY totale DESC
```

### Concetti presenti in piu chunk (molto citati)
```cypher
MATCH (ch:Chunk)-[:CONTIENE]->(c:Concept)
RETURN c.name, count(ch) AS presente_in_chunk
ORDER BY presente_in_chunk DESC
LIMIT 15
```

### Chunk con piu concetti estratti (piu densi)
```cypher
MATCH (ch:Chunk)-[:CONTIENE]->(c:Concept)
RETURN ch.id, ch.source, count(c) AS num_concetti
ORDER BY num_concetti DESC
LIMIT 10
```

### Concetti isolati (nessuna relazione ad altri concetti)
```cypher
MATCH (c:Concept)
WHERE NOT (c)-[]-()
RETURN c.name
```

### Nodi senza relazioni CONTIENE (orfani)
```cypher
MATCH (c:Concept)
WHERE NOT ()-[:CONTIENE]->(c)
RETURN c.name LIMIT 20
```

---

## 6. RICERCA FULL-TEXT AVANZATA

### Ricerca case-insensitive su concetti
```cypher
MATCH (c:Concept)
WHERE toLower(c.name) CONTAINS toLower('guerra')
RETURN c.name
```

### Trovare tutti i fatti su un argomento (soggetto O oggetto)
```cypher
MATCH (c:Concept)-[r]->(m:Concept)
WHERE c.name CONTAINS 'ROMA' OR m.name CONTAINS 'ROMA'
RETURN c.name, type(r), m.name
LIMIT 30
```

### Trovare catene causali (relazioni CAUSA)
```cypher
MATCH (c)-[r:CAUSA]->(m)
RETURN c.name, m.name
```

---

## 7. MANUTENZIONE

### Vedere quanti grafi esistono
```cypher
-- Dalla shell FalkorDB o via client:
-- db.list_graphs()
```

### Eliminare un singolo nodo (senza relazioni)
```cypher
MATCH (c:Concept {name: 'NOME_DA_ELIMINARE'})
DELETE c
```

### Eliminare un nodo e tutte le sue relazioni
```cypher
MATCH (c:Concept {name: 'NOME_DA_ELIMINARE'})
DETACH DELETE c
```

### Reset completo del grafo corrente
```cypher
MATCH (n) DETACH DELETE n
```

### Aggiungere manualmente un concetto
```cypher
CREATE (:Concept {name: 'NUOVO_CONCETTO', manual: 'NOME_MANUALE'})
```

### Aggiungere manualmente una relazione
```cypher
MATCH (a:Concept {name: 'SOGGETTO'}), (b:Concept {name: 'OGGETTO'})
CREATE (a)-[:TIPO_RELAZIONE {source: 'manuale.md'}]->(b)
```

---

## 8. QUERY UTILI PER IL DEBUGGING RAG

### Verificare se una keyword viene trovata nei chunk
```cypher
MATCH (ch:Chunk) WHERE ch.content CONTAINS 'fuori gioco'
RETURN ch.id, ch.content LIMIT 5
```

### Simulare il retrieval del chat engine (keyword scoring)
```cypher
MATCH (ch:Chunk) WHERE ch.content CONTAINS 'Annibale'
RETURN ch.id, ch.content
UNION
MATCH (c:Concept)<-[:CONTIENE]-(ch:Chunk)
WHERE c.name CONTAINS 'ANNIBALE'
RETURN ch.id, ch.content
```

### Vedere il sotto-grafo completo attorno a un concetto
```cypher
MATCH (c:Concept {name: 'ANNIBALE'})-[r1]-(m:Concept)-[r2]-(k:Concept)
RETURN c, r1, m, r2, k
LIMIT 50
```

### Trovare relazioni duplicate (stesso S-R-O)
```cypher
MATCH (c1:Concept)-[r]->(c2:Concept)
RETURN c1.name, type(r), c2.name, count(*) AS duplicati
ORDER BY duplicati DESC
LIMIT 10
```

---

## 9. CHEAT SHEET RAPIDO

| Obiettivo | Query |
|-----------|-------|
| Tutto il grafo | `MATCH (n)-[e]->(m) RETURN n,e,m LIMIT 1000` |
| Cerca concetto | `MATCH (c:Concept) WHERE c.name CONTAINS 'X' RETURN c.name` |
| Relazioni di X | `MATCH (c {name:'X'})-[r]-(m) RETURN type(r), m.name` |
| Percorso A->B | `MATCH p=(a {name:'A'})-[*1..3]->(b {name:'B'}) RETURN p` |
| Cerca nel testo | `MATCH (ch:Chunk) WHERE ch.content CONTAINS 'parola' RETURN ch` |
| Top hub | `MATCH (c)-[r]-() RETURN c.name, count(r) ORDER BY count(r) DESC LIMIT 10` |
| Reset grafo | `MATCH (n) DETACH DELETE n` |
