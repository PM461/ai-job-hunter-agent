# AI Job Hunter & Auto-Apply Agent

Sistema autonomo di ricerca offerte di lavoro, analisi semantica del curriculum vitae tramite modelli linguistici di grandi dimensioni (Google Gemini), monitoraggio multi-portale su oltre 50 aziende e multinazionali, ricerca X-Ray su ATS esterni e gestione interattiva completa tramite Bot Telegram e Dashboard Web.

---

## Indice

1. [Panoramica del Progetto](#1-panoramica-del-progetto)
2. [Architettura e Funzionalita Principali](#2-architettura-e-funzionalita-principali)
3. [Tecnologie Utilizzate](#3-tecnologie-utilizzate)
4. [Requisiti di Sistema](#4-requisiti-di-sistema)
5. [Installazione Passo-Passo](#5-installazione-passo-passo)
6. [Configurazione Iniziale](#6-configurazione-iniziale)
7. [Avvio del Sistema e Primo Accesso LinkedIn](#7-avvio-del-sistema-e-primo-accesso-linkedin)
8. [Guida Completa ai Comandi del Bot Telegram](#8-guida-completa-ai-comandi-del-bot-telegram)
9. [Elenco Aziende e Portali Configurati](#9-elenco-aziende-e-portali-configurati)
10. [Consigli per un Utilizzo Ottimale](#10-consigli-per-un-utilizzo-ottimale)
11. [Limiti Tecnici e Operativi](#11-limiti-tecnici-e-operativi)
12. [Esclusione e Limitazione di Responsabilita (Disclaimer)](#12-esclusione-e-limitazione-di-responsabilita-disclaimer)

---

## 1. Panoramica del Progetto

AI Job Hunter & Auto-Apply Agent e un'applicazione backend asincrona progettata per automatizzare il processo di scouting, filtraggio, valutazione di compatibilita e candidatura alle offerte di lavoro.

A differenza dei normali aggregatori basati su semplici parole chiave, il sistema:
- Estrae il testo integrale del Curriculum Vitae in formato PDF del candidato.
- Esegue scansioni periodiche o su richiesta su LinkedIn, su oltre 50 portali carriere ufficiali di aziende tech/engineering/consulting e su motori di ricerca esterni (X-Ray Search e Signal Scouting).
- Filtra preventivamente gli annunci in base alle posizioni cercate, al range geografico (singola citta, multi-citta o intera nazione) e alla modalita di lavoro desiderata (remoto, ibrido, in sede).
- Valuta ogni singola offerta tramite Google Gemini AI producendo un punteggio di compatibilita (0-100%), l'elenco delle competenze possedute e mancanti, la stima della RAL e una nota di accompagnamento (cover note) personalizzata per l'Hiring Manager.
- Notifica in tempo reale l'utente su Telegram, permettendo di approvare o scartare l'invio della candidatura con un tocco e di riconfigurare dinamicamente ruoli, zone geografiche e filtri senza toccare il codice.

---

## 2. Architettura e Funzionalita Principali

Il sistema e suddiviso nei seguenti moduli:

- **Motore di Scraping Multi-Portale (`app/scrapers/multinationals_scraper.py`)**:
  Interroga oltre 50 portali aziendali utilizzando connettori API diretti (Amazon Jobs API, Stellantis/Comau API, AshbyHQ API, Allibo ATS, ADP API) e sessioni browser headless Playwright con espansione automatica dei pulsanti "Mostra altri / Load More" e navigazione automatica della paginazione numerata (`1, 2, 3, Next`). Supporta nativamente Workday, SAP SuccessFactors, Oracle Cloud HCM, Oracle Taleo, Eightfold AI, Avature, Cornerstone, In-recruiting, Factorial e portali custom.

- **Scraper LinkedIn Booleano (`app/scrapers/linkedin_scraper.py`)**:
  Costruisce query booleane dinamiche combinando i ruoli configurati dall'utente, le citta target e le modalita di lavoro, oltre a interrogare direttamente le pagine offerte delle aziende monitorate tramite sessione persistente autenticata.

- **Modulo X-Ray ATS & Signal Scouting (`app/scrapers/xray_signal_scraper.py`)**:
  - *X-Ray ATS*: individua posizioni pubblicate su board Greenhouse, Lever, Workday e AshbyHQ o in post informali pubblicati dagli Hiring Manager sul feed di LinkedIn prima della sponsorizzazione ufficiale.
  - *Signal Scouting*: monitora Google News RSS per intercettare segnali di espansione aziendale (round di finanziamento, apertura nuove sedi, acquisizioni, nuovi appalti) e suggerire candidature spontanee mirate (Reverse Headhunting).

- **Valutatore Semantico AI (`app/ai_matcher.py`)**:
  Applica un pre-filtro deterministico rapido (escludendo posizioni fuori area geografica, fuori modalita lavorativa o con seniority incompatibile) e invia gli annunci pertinenti a Google Gemini con schema JSON strutturato (`JobEvaluationSchema`).

- **Orchestratore e Database (`app/orchestrator.py`, `app/database.py`)**:
  Coordina l'esecuzione concorrente degli scraper, gestisce l'interruzione immediata (`stop_requested`), deduplica gli annunci tramite database locale SQLite asincrono (`aiosqlite`) e smista le candidature automatiche o in attesa di revisione.

- **Server FastAPI, Scheduler e Bot Telegram (`app/main.py`, `app/notifier.py`)**:
  Espone una dashboard web locale sulla porta `8000`, pianifica le scansioni automatiche tramite `APScheduler` e gestisce il polling continuo dei comandi e dei pulsanti inline su Telegram.

---

## 3. Tecnologie Utilizzate

- **Linguaggio**: Python 3.11+ (programmazione asincrona basata su `asyncio`)
- **Framework Web e API**: FastAPI, Uvicorn
- **Intelligenza Artificiale**: Google GenAI SDK (`google-genai`), modelli `gemini-2.5-flash` / `gemini-2.5-pro` con Structured Outputs (Pydantic)
- **Browser Automation & Web Scraping**: Playwright (Chromium), `playwright-stealth`, HTTPX (client HTTP/2 asincrono), BeautifulSoup4
- **Database**: SQLite3 con driver asincrono `aiosqlite`
- **Task Scheduling**: APScheduler (`AsyncIOScheduler`)
- **Elaborazione Documenti**: `pypdf` per l'estrazione automatica del testo dal CV in PDF
- **Validazione Dati e Configurazione**: Pydantic v2, `pydantic-settings`, `python-dotenv`
- **Containerizzazione**: Docker e Docker Compose basati sull'immagine ufficiale Microsoft Playwright

---

## 4. Requisiti di Sistema

- **Sistema Operativo**: Windows 10/11, Linux (Ubuntu 22.04+ consigliato) o macOS
- **Python**: versione 3.11 o superiore
- **Browser**: Google Chrome installato localmente (consigliato per il setup iniziale della sessione LinkedIn) oltre ai binari Chromium di Playwright
- **Chiave API Google Gemini**: ottenibile gratuitamente o con piano pay-as-you-go da Google AI Studio
- **Bot Telegram**: Token API generato tramite `@BotFather` su Telegram e il proprio `Chat ID` numerico (ottenibile tramite `@userinfobot`)

---

## 5. Installazione Passo-Passo

### 5.1 Clonazione del Repository e Ambiente Virtuale

```bash
git clone https://github.com/PM461/ai-job-hunter-agent.git
cd ai-job-hunter-agent

# Creazione ambiente virtuale Python
python -m venv venv

# Attivazione su Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# Attivazione su Linux / macOS
source venv/bin/activate
```

### 5.2 Installazione delle Dipendenze e dei Browser Playwright

```bash
pip install --upgrade pip
pip install -r requirements.txt

# Installazione del browser Chromium per Playwright
playwright install chromium
```

### 5.3 Installazione tramite Docker (Alternativa)

Se si preferisce eseguire l'agente in un container isolato:

```bash
docker compose up --build -d
```

---

## 6. Configurazione Iniziale

### 6.1 File delle Variabili d'Ambiente (`.env`)

Copiare il file di esempio `.env.example` rinominandolo in `.env`:

```bash
cp .env.example .env
```

Aprire il file `.env` e compilare i parametri:

- `GEMINI_API_KEY`: inserire la propria chiave API di Google AI Studio.
- `GEMINI_MODEL`: modello Gemini da utilizzare (es. `gemini-2.5-flash`).
- `TELEGRAM_BOT_TOKEN`: il token fornito da `@BotFather`.
- `TELEGRAM_CHAT_ID`: il proprio ID utente Telegram numerico.
- `AUTO_APPLY_THRESHOLD`: punteggio minimo (es. `85`) sopra il quale l'agente tenta l'invio automatico della candidatura.
- `REVIEW_THRESHOLD`: punteggio minimo (es. `65`) per ricevere la notifica su Telegram con i pulsanti di approvazione manuale.
- `CHECK_INTERVAL_MINUTES`: intervallo in minuti tra le scansioni automatiche pianificate (es. `60`).
- `HEADLESS_BROWSER`: impostare `true` per l'esecuzione silenziosa in background, `false` per visualizzare le finestre del browser durante il debug.

### 6.2 Profilo del Candidato (`data/candidate_profile.json`) e Curriculum Vitae

1. Copiare il proprio Curriculum Vitae in formato PDF all'interno della cartella `data/` (ad esempio `data/cv.pdf`).
2. Aprire `data/candidate_profile.json` e aggiornare la sezione `candidate_profile` con i propri dati anagrafici, recapiti, URL dei profili professionali, formazione, competenze principali (`core_skills`), RAL minima attesa (`expected_salary_ral`), anni minimi di esperienza (`min_years_experience`) e percorso del file PDF (`cv_pdf_path`).
3. Nella sezione `search_preferences` e possibile definire i valori iniziali di:
   - `keywords`: lista delle posizioni cercate (modificabile in qualsiasi momento da Telegram).
   - `target_cities`: lista delle citta o aree geografiche di interesse (modificabile da Telegram).
   - `work_modes`: modalita di lavoro accettate tra `remoto`, `ibrido`, `in_sede` (modificabili da Telegram).

---

## 7. Avvio del Sistema e Primo Accesso LinkedIn

### 7.1 Salvataggio della Sessione LinkedIn (Opzionale ma Consigliato)

Per permettere allo scraper LinkedIn di accedere alle offerte complete senza incorrere in schermate di blocco login (authwall) o verifiche continue:

```bash
python login_setup.py
```

Si aprira una finestra di Google Chrome reale. Effettuare l'accesso al proprio account LinkedIn e, una volta visualizzato il feed principale, tornare nel terminale e premere `INVIO`. I cookie di sessione verranno salvati localmente in `data/browser_state/linkedin_state.json` (escluso da Git).

### 7.2 Avvio del Server Principale

```bash
python run_server.py
```

All'avvio:
- Il server FastAPI rispondera all'indirizzo `http://localhost:8000` mostrando la Dashboard Web con tutte le offerte analizzate.
- Il Bot Telegram registrera automaticamente i suggerimenti dei comandi nel menu `/` e inviera in chat il messaggio di benvenuto con la guida completa (`/help`) e il pannello interattivo (`/config`).

---

## 8. Guida Completa ai Comandi del Bot Telegram

Tutte le impostazioni di ricerca (posizioni lavorative, citta singole o multiple, modalita remoto/ibrido/in sede, RAL, esperienza, orizzonte temporale) possono essere modificate in tempo reale direttamente da Telegram, sia tramite pulsanti interattivi sia tramite comandi testuali.

### 8.1 Guida e Pannello Principale
- `/help` oppure `/start` : Mostra la spiegazione completa di tutti i comandi disponibili e apre la tastiera di controllo.
- `/config` : Apre il pannello di controllo interattivo con il riepilogo della configurazione attiva e i pulsanti rapidi.
- `/status` : Mostra lo stato dell'agente (Idle o Scansione attiva), la configurazione corrente e le statistiche del database.

### 8.2 Modifica delle Posizioni / Ruoli Cercati
- `/ruoli` : Apre il menu interattivo con pulsanti toggle per attivare o disattivare con un tocco le posizioni predefinite (`AI Engineer`, `Machine Learning Engineer`, `Data Scientist`, `Python Developer`, `Software Engineer`, `Data Engineer`, `Computer Vision`, `NLP Engineer`, `Backend Developer`, `Full Stack Developer`, `DevOps Engineer`, `Cybersecurity`) o rimuovere ruoli personalizzati.
- `/add_ruolo <nome>` : Aggiunge una o piu posizioni personalizzate alla ricerca (es. `/add_ruolo Data Analyst` oppure `/add_ruolo Cloud Architect, Platform Engineer`).
- `/del_ruolo <nome>` : Rimuove una posizione dalla lista attiva (es. `/del_ruolo Data Analyst`).
- `/set_ruoli <r1, r2, ...>` : Sostituisce l'intera lista delle posizioni cercate con quelle indicate separate da virgola (es. `/set_ruoli AI Engineer, Python Developer, Data Scientist`).

### 8.3 Modifica dell'Area di Ricerca e Modalita Lavoro (Multi-Range)
- `/zone` oppure `/modalita` : Apre il menu interattivo multi-selezione che consente di combinare liberamente piu citta (`Milano`, `Roma`, `Torino`, `Bologna`, `Firenze`, `Napoli`, `Padova`, `Genova`, `Bari`, `Italia`) e piu modalita di lavoro (`Remoto`, `Ibrido`, `In Sede`).
- `/add_zona <citta>` : Aggiunge una o piu citta al range geografico (es. `/add_zona Verona` oppure `/add_zona Milano, Bologna`).
- `/del_zona <citta>` : Rimuove una citta dal range attivo (es. `/del_zona Roma`).
- `/set_zone <c1, c2, ...>` : Imposta da zero il range di citta desiderate (es. `/set_zone Milano, Torino, Roma` oppure `/set_zone Italia` per cercare su tutto il territorio nazionale).
- `/set_modalita <m1, m2, ...>` : Imposta le modalita di lavoro accettate scegliendo una o piu opzioni tra `remoto`, `ibrido`, `in_sede` (es. `/set_modalita remoto, ibrido`).

### 8.4 Avvio e Arresto delle Scansioni
- `/scan` : Avvia immediatamente una scansione completa (LinkedIn + X-Ray + tutti i 50 portali aziendali mappati).
- `/scan_li` : Avvia la scansione booleana e diretta solo su LinkedIn.
- `/xray` : Avvia la scansione X-Ray sugli ATS esterni (Workday, Greenhouse, Lever, Ashby) e sui post degli Hiring Manager.
- `/scout` : Avvia il Signal Scouting sulle notizie di espansione aziendale, nuovi round di investimento e aperture sedi.
- `/stop` : Interrompe in modo sicuro qualsiasi scansione in corso ed emette il riepilogo parziale.

### 8.5 Filtri di Compatibilita, RAL ed Esperienza
- `/set_ral <cifra>` : Aggiorna la RAL minima desiderata in Euro (es. `/set_ral 35000`).
- `/set_exp <anni>` : Aggiorna gli anni minimi di esperienza richiesti (es. `/set_exp 0` per includere posizioni entry-level/junior, `/set_exp 2` per profili mid).
- `/set_min <10-95>` : Modifica la percentuale minima di compatibilita CV richiesta per ricevere una notifica su Telegram (es. `/set_min 70`).

---

## 9. Elenco Aziende e Portali Configurati

Il file `data/career_sites_mapping.json` include la configurazione pronta all'uso per 50 portali carriere ufficiali:

- **Banche, Assicurazioni e Fintech**: Intesa Sanpaolo, Banca Sella, Centrico, Satispay, Reale Mutua Assicurazioni, Prima Assicurazioni, UnipolTech.
- **Consulenza IT, System Integration e Big4**: Reply Group, Accenture, Deloitte, PwC, EY, KPMG, Capgemini Engineering, NTT DATA, Almaviva, BIP, Lutech, Alten Italia, Akkodis, Amaris Consulting, Engineering, Zucchetti & Tilby, aizoOn, Blue BI, CGM Consulting, F2 Informatica, AGM Solutions, Proxima Group, Sirma, Bit Software Solutions, Gecal Informatica.
- **Aerospazio, Difesa, Automotive e Industria**: Leonardo S.p.A., Thales Alenia Space, Altec, Stellantis & Comau, CNH Industrial, Marelli, Dumarey, AVL, SKF, Dana Incorporated, Michelin, SPEA,Ferrero, Gruppo Iren.
- **Tech Company, Software Product e Centri di Ricerca**: Amazon, Arduino (Qualcomm), Docebo, Altair Engineering, EnginSoft, Synesthesia, Visualitics, MSC Technology Italia, CSI Piemonte, TIM, TIM Retail, IIT (Istituto Italiano di Tecnologia), Fondazione LINKS, Fondazione Bruno Kessler (FBK), AI4I.

Per aggiungere ulteriori aziende e possibile modificare direttamente `data/career_sites_mapping.json` oppure eseguire l'utility interattiva `python site_mapper.py`.

---

## 10. Consigli per un Utilizzo Ottimale

1. **Qualita del Curriculum Vitae PDF**: Assicurarsi che il file PDF del CV contenga testo selezionabile (non un'immagine scansionata priva di OCR) e riporti chiaramente tecnologie, linguaggi, framework, progetti e livello di istruzione. La qualita del matching di Gemini dipende direttamente dalla chiarezza del testo estratto dal PDF.
2. **Bilanciamento delle Soglie (`REVIEW_THRESHOLD` e `AUTO_APPLY_THRESHOLD`)**: Si consiglia di mantenere `REVIEW_THRESHOLD` intorno a `65-75%` e `AUTO_APPLY_THRESHOLD` a un valore conservativo (`85-90%`), privilegiando l'approvazione manuale tramite il pulsante Telegram per verificare sempre l'offerta prima dell'invio.
3. **Configurazione Multi-Range Geografica**: Se si cercano opportunita in presenza o ibride su piu poli tecnologici (es. Milano e Torino) insieme a posizioni full-remote da tutta Italia, attivare dal menu `/zone` sia le citta interessate sia le modalita `Remoto`, `Ibrido` e `In Sede`. Se invece si desidera esclusivamente lavoro da remoto, selezionare solo `Remoto` nel menu `/modalita`.
4. **Intervallo di Scansione Ragionevole**: Impostare `CHECK_INTERVAL_MINUTES` su valori non inferiori a `60` minuti (o superiori, es. `120-240` minuti) per ridurre il consumo di banda, di quota API Gemini e il rischio di rate-limiting da parte dei portali.
5. **Networking Diretto (Reverse Headhunting)**: Ogni notifica Telegram include link preconfigurati per cercare su LinkedIn gli Hiring Manager, i CTO e i membri del team dell'azienda che ha pubblicato l'annuncio. Contattare direttamente il responsabile allegando la cover note generata dall'AI aumenta sensibilmente il tasso di risposta rispetto al solo invio tramite ATS.

---

## 11. Limiti Tecnici e Operativi

- **Cambiamenti nel DOM dei Portali**: I siti web aziendali e le piattaforme ATS aggiornano periodicamente la propria struttura HTML/CSS. Sebbene lo scraper includa logiche di fallback e connettori API, modifiche strutturali lato server sui siti target possono richiedere l'aggiornamento dei selettori.
- **Sistemi Anti-Bot e Protezioni Captcha**: Alcuni portali utilizzano sistemi di protezione (Cloudflare, reCAPTCHA, Akamai) o impongono limiti sul numero di richieste per indirizzo IP. Il progetto **non** risolve ne aggira sistemi CAPTCHA o barriere di autenticazione protette.
- **Invio Automatico delle Candidature (Auto-Apply)**: La compilazione automatica funziona esclusivamente su moduli standard ("Easy Apply" o form ATS basilari con campi nome, cognome, email, telefono e upload CV). I portali che richiedono la creazione manuale di un account dedicato, test psico-attitudinali, codici OTP via email o questionari multi-pagina complessi richiedono il completamento manuale da parte dell'utente tramite il link fornito nella notifica Telegram.
- **Quote API e Costi di Terze Parti**: L'utilizzo intensivo del modello Google Gemini comporta il consumo dei limiti di richieste (RPM/RPD) previsti dal proprio piano su Google AI Studio. In caso di esaurimento della quota giornaliera, l'agente passa automaticamente a un algoritmo di fallback locale basato sulle keyword fino al ripristino della quota.
- **Accuratezza delle Valutazioni AI**: I punteggi di compatibilita, le stime di RAL e le cover note sono generati da un modello statistico-probabilistico (LLM) e possono occasionalmente contenere imprecisioni o interpretazioni non perfette del testo dell'annuncio.

---

## 12. Esclusione e Limitazione di Responsabilita (Disclaimer)

Il presente software e fornito esclusivamente a scopo educativo, di studio, di ricerca personale e di supporto alla produttivita individuale, secondo la formula "COSI COM'E" ("AS IS"), senza alcuna garanzia espressa o implicita di commerciabilita, idoneita per uno scopo particolare o assenza di violazioni.

Utilizzando questo codice, l'utente accetta integralmente le seguenti condizioni:

1. **Rispetto dei Termini di Servizio (ToS) di Terze Parti**: L'accesso automatizzato a portali di lavoro (inclusi, a titolo esemplificativo e non esaustivo, LinkedIn e i portali carriere aziendali) potrebbe essere soggetto a restrizioni nei Termini di Servizio delle rispettive piattaforme. L'utente finale e l'unico responsabile della verifica e del rispetto dei Termini di Servizio dei siti interrogati e utilizza il software a proprio esclusivo rischio.
2. **Sicurezza degli Account e Blocchi IP**: Gli autori e i contributori del progetto declinano ogni responsabilita per eventuali limitazioni temporanee o permanenti, sospensioni o chiusure di account personali (es. account LinkedIn) o blocchi dell'indirizzo IP derivanti da un uso improprio, eccessivamente frequente o non conforme delle funzionalita di scraping e automazione.
3. **Veridicita delle Candidature e Dati Personali**: L'utente e l'unico responsabile della veridicita dei dati inseriti nel proprio profilo e nel proprio Curriculum Vitae, nonche del contenuto delle candidature e dei messaggi inviati tramite il sistema. Tutto il trattamento dei dati personali avviene localmente sulla macchina dell'utente e tramite le API (Google Gemini, Telegram) configurate direttamente dall'utente stesso con le proprie chiavi personali.
4. **Esclusione di Responsabilita per Danni**: In nessun caso gli autori o i titolari del copyright potranno essere ritenuti responsabili per danni diretti, indiretti, incidentali, speciali, esemplari o consequenziali (inclusi, ma non limitati a, mancata assunzione, perdita di opportunita lavorative, perdita di dati o interruzione di servizio) derivanti dall'uso o dall'impossibilita di usare il presente software.
