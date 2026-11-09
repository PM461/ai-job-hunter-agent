import json
import logging
import asyncio
import re
from pathlib import Path
from typing import Dict, Any, List, Tuple
from pydantic import BaseModel, Field
from pypdf import PdfReader
from google import genai
from google.genai import types

from app.config import settings, BASE_DIR, load_candidate_config

logger = logging.getLogger("JobAgent.Gemini")

CITY_ALIASES: Dict[str, List[str]] = {
    "torino": [
        "torino", "turin", "piemonte", "caselle", "grugliasco", "rivoli",
        "orbassano", "collegno", "settimo torinese", "venaria", "moncalieri",
        "san mauro torinese", "ivrea", "chieri", "rivalta", "volpiano",
    ],
    "milano": [
        "milano", "milan", "lombardia", "assago", "segrate", "monza",
        "sesto san giovanni", "rozzano", "san donato milanese", "rho", "cinisello",
    ],
    "roma": ["roma", "rome", "lazio", "pomezia", "frascati"],
    "bologna": ["bologna", "emilia-romagna", "emilia romagna", "casalecchio", "modena", "imola"],
    "firenze": ["firenze", "florence", "toscana", "prato", "sesto fiorentino", "pisa"],
    "napoli": ["napoli", "naples", "campania", "caserta", "salerno"],
    "genova": ["genova", "genoa", "liguria"],
    "padova": ["padova", "padua", "veneto", "verona", "venezia", "treviso", "vicenza"],
    "bari": ["bari", "puglia", "lecce", "taranto"],
    "italia": [
        "italia", "italy", "torino", "turin", "milano", "milan", "roma", "rome",
        "bologna", "firenze", "florence", "napoli", "naples", "genova", "padova",
        "verona", "venezia", "bari", "bergamo", "brescia", "parma", "modena",
        "reggio emilia", "trieste", "trento", "cagliari", "catania", "palermo",
    ],
}

ALL_KNOWN_CITIES: List[str] = [
    "torino", "turin", "milano", "milan", "roma", "rome", "napoli", "naples",
    "bologna", "firenze", "florence", "verona", "padova", "genova", "bari",
    "venezia", "trieste", "brescia", "bergamo", "parma", "modena", "reggio emilia",
    "london", "paris", "munich", "berlin", "madrid", "barcelona", "luxembourg",
    "zurich", "amsterdam", "dublin", "brussels", "warsaw", "sofia", "bucharest",
]


def extract_ral_with_regex(text: str) -> str:
    """
    Cerca nel testo dell'annuncio pattern espliciti di stipendio / RAL.
    Restituisce la cifra/intervallo trovato, oppure 'Non specificata'.
    """
    if not text:
        return "Non specificata"

    patterns = [
        r'(?:ral|retribuzione|compenso|stipendio|salary)?[:\s]*(?:€\s*|\bEUR\s*)?(\d{2,3}(?:[.,]\d{3})?|\d{2,3})\s*(?:k|€|euro)?\s*(?:-|–|a|to)\s*(?:€\s*|\bEUR\s*)?(\d{2,3}(?:[.,]\d{3})?|\d{2,3})\s*(?:k|€|euro|\beur\b|\/anno|all\'anno)?',
        r'(?:ral|retribuzione(?:\s+annua\s+lorda)?|stipendio|salary)[:\s]*(?:€\s*|\bEUR\s*)?(\d{2,3}(?:[.,]\d{3})?)\s*(?:k|€|euro|\beur\b|\/anno)?',
        r'(?:€\s*|\bEUR\s*)?(\d{2,3}(?:[.,]\d{3}))\s*(?:€|euro|\beur\b)\s*(?:\/anno|all\'anno|lordi|annui)',
        r'(?:€\s*|\bEUR\s*)?(\d{1,2}(?:[.,]\d{3})|\d{3,4})\s*(?:€|euro|\beur\b)?\s*(?:\/mese|al mese|mensili|netti|lordi)',
    ]

    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            found = m.group(0).strip()
            found = re.sub(
                r'^(ral|retribuzione(\s+annua\s+lorda)?|compenso|stipendio|salary)[:\s]*',
                '',
                found,
                flags=re.IGNORECASE,
            ).strip()
            if len(found) >= 3 and any(char.isdigit() for char in found):
                return found

    return "Non specificata"


class JobMatchEvaluation(BaseModel):
    match_score: int = Field(
        description="Grado di fedeltà da 0 a 100 tra il CV del candidato e l'offerta di lavoro."
    )
    estimated_ral: str = Field(
        description="RAL / Compenso ESPLICITAMENTE indicato nell'annuncio (es. '30.000€ - 35.000€', '800€/mese'). Se nell'annuncio NON è indicata alcuna cifra o stipendio, scrivi ESATTAMENTE 'Non specificata'."
    )
    matching_skills: List[str] = Field(
        description="Competenze ed esperienze presenti nel CV che soddisfano l'offerta."
    )
    missing_skills: List[str] = Field(
        description="Requisiti richiesti nell'offerta che non compaiono esplicitamente nel CV."
    )
    is_dealbreaker_missing: bool = Field(
        description="True se il ruolo richiede seniority esclusa, sede non compatibile con le aree scelte, o tecnologie escluse."
    )
    ai_reasoning: str = Field(
        description="Spiegazione sintetica in italiano (2-3 frasi) del punteggio assegnato con verifica di sede, modalità e ruolo."
    )
    tailored_cover_note: str = Field(
        description="Breve messaggio di presentazione personalizzato (max 600 caratteri)."
    )


def extract_text_from_cv() -> str:
    config = load_candidate_config()
    cv_rel_path = config.get("candidate_profile", {}).get("cv_pdf_path", "data/cv.pdf")
    cv_path: Path = BASE_DIR / cv_rel_path

    if not cv_path.exists():
        logger.warning(f"File CV PDF non trovato in {cv_path}. Uso i dati del profilo JSON.")
        return json.dumps(config.get("candidate_profile", {}), indent=2, ensure_ascii=False)

    try:
        reader = PdfReader(str(cv_path))
        pages_text = [page.extract_text() or "" for page in reader.pages]
        full_text = "\n".join(pages_text).strip()
        if not full_text:
            return json.dumps(config.get("candidate_profile", {}), indent=2, ensure_ascii=False)
        return full_text
    except Exception as e:
        logger.error(f"Errore lettura CV PDF: {e}")
        return json.dumps(config.get("candidate_profile", {}), indent=2, ensure_ascii=False)


def _expand_role_keywords(keywords: List[str]) -> List[str]:
    """Espande le posizioni configurate dall'utente in token di filtro."""
    if not keywords:
        return [
            "ai", "artificial intelligence", "intelligenza artificiale",
            "machine learning", "ml", "data scientist", "data science",
            "computer vision", "nlp", "python", "deep learning", "mlops",
            "data engineer", "software engineer", "software developer",
        ]
    expanded = set()
    for kw in keywords:
        k_low = kw.lower().strip()
        if not k_low:
            continue
        expanded.add(k_low)
        # Rimuovi prefissi di seniority per ottenere il nucleo del ruolo
        core = re.sub(r"\b(junior|senior|mid|entry level|stage|intern|lead)\b", "", k_low).strip()
        if len(core) >= 2:
            expanded.add(core)
        # Aggiungi sinonimi comuni se l'utente ha selezionato macro-ruoli
        if "ai" in k_low or "artificial intelligence" in k_low or "intelligenza artificiale" in k_low:
            expanded.update(["ai", "artificial intelligence", "intelligenza artificiale", "generative ai", "llm", "genai"])
        if "machine learning" in k_low or "ml" in k_low:
            expanded.update(["machine learning", "ml", "deep learning", "mlops", "pytorch", "tensorflow"])
        if "data" in k_low:
            expanded.update(["data scientist", "data science", "data engineer", "data analyst", "big data"])
        if "python" in k_low:
            expanded.update(["python", "sviluppatore python", "python developer"])
        if "software" in k_low or "developer" in k_low or "sviluppatore" in k_low:
            expanded.update(["software engineer", "software developer", "ingegnere informatico", "sviluppatore software"])
        if "vision" in k_low:
            expanded.update(["computer vision", "image processing", "opencv"])
    return list(expanded)


def quick_prefilter(job: Dict[str, Any], candidate_config: dict) -> Tuple[bool, str]:
    """
    PRE-FILTRO DINAMICO BASATO SULLA CONFIGURAZIONE UTENTE (Telegram / JSON):
    1. Categorie Protette (L. 68/99): Scartate tassativamente.
    2. Candidature già inoltrate: Scartate.
    3. Area Geografica & Modalità di Lavoro (Città multiple + Remoto / Ibrido / In Sede):
       Valuta rigorosamente in base a `search_preferences.target_cities` e `search_preferences.work_modes`.
    4. Stack Escluso & Seniority.
    5. Ruoli Target: Valuta dinamicamente in base a `search_preferences.keywords`.
    """
    title = job.get("title", "").lower()
    desc = job.get("description", "").lower()
    full_text = f"{title} {desc} {job.get('location', '')}".lower()

    # --- 0. ESCLUSIONE CATEGORIE PROTETTE (L. 68/99 e simili) ---
    protected_cat_patterns = [
        "categoria protetta", "categorie protette", "l. 68/99", "l.68/99", "l 68/99",
        "legge 68/99", "legge 68", "art. 1 l. 68", "art. 18 l. 68", "l.68",
        "l68/99", "l. 68", "rif. 68/99", "rif. 2006-55", "ex l. 68"
    ]
    if any(p in full_text for p in protected_cat_patterns):
        return False, "Riservato a Categorie Protette (L. 68/99 o simili)"

    if re.search(r'\b(l\.?\s*68\s*/\s*99|legge\s*68|categorie?\s+protett[ae]|art\.?\s*18?\s+l\.?\s*68)\b', full_text, re.IGNORECASE):
        return False, "Riservato a Categorie Protette (L. 68/99)"

    # --- 0.1 CANDIDATURA GIÀ INOLTRATA ---
    if "candidatura inoltrata" in full_text or "application submitted" in full_text:
        return False, "Candidatura già inoltrata precedentemente"

    # --- 1. AREA GEOGRAFICA E MODALITÀ DI LAVORO DINAMICHE ---
    search_prefs = candidate_config.get("search_preferences", {})
    target_cities: List[str] = search_prefs.get("target_cities") or [
        candidate_config.get("candidate_profile", {}).get("city", "Italia")
    ]
    work_modes: List[str] = [m.lower() for m in (search_prefs.get("work_modes") or ["remoto", "ibrido", "in_sede"])]

    # Costruisci l'insieme di keyword geografiche accettate in base alle città scelte dall'utente
    accepted_city_terms = set()
    for city in target_cities:
        c_low = city.lower().strip()
        if not c_low:
            continue
        accepted_city_terms.add(c_low)
        for alias in CITY_ALIASES.get(c_low, []):
            accepted_city_terms.add(alias)

    matches_target_city = any(
        re.search(rf'\b{re.escape(term)}\b', full_text) for term in accepted_city_terms
    )
    is_remote_job = any(
        r in full_text
        for r in [
            "full remote", "full-remote", "completamente da remoto", "100% da remoto",
            "100% remote", "da remoto", "lavoro da remoto", "telelavoro", "remote italy", "remote italia",
        ]
    )

    # Città non selezionate dall'utente
    disallowed_cities = [
        c for c in ALL_KNOWN_CITIES if c not in accepted_city_terms and "italia" not in [tc.lower() for tc in target_cities]
    ]
    has_other_city = any(re.search(rf'\b{re.escape(c)}\b', full_text) for c in disallowed_cities)

    allow_remote = "remoto" in work_modes or "remote" in work_modes
    allow_onsite_or_hybrid = "ibrido" in work_modes or "in_sede" in work_modes or "insede" in work_modes

    # Caso A: L'utente cerca SOLO da remoto (né ibrido né in sede)
    if allow_remote and not allow_onsite_or_hybrid:
        if not is_remote_job:
            return False, "Modalità non compatibile (richiesto solo Full-Remote)"
    else:
        # Caso B: Se compare un'altra città e non compare nessuna delle città target (e non è full-remote ammesso)
        if has_other_city and not matches_target_city and not (allow_remote and is_remote_job and not has_other_city):
            if has_other_city and not matches_target_city:
                return False, f"Sede in altra città non inclusa nelle zone scelte ({', '.join(target_cities)})"

        # Se non matcha né le città scelte né il full-remote (quando il remoto è attivo)
        if not matches_target_city and not (allow_remote and is_remote_job):
            return False, f"Sede non compatibile (richiesto: {', '.join(target_cities)}{' o Full-Remote' if allow_remote else ''})"

    # Se l'utente ha disattivato 'in_sede' ma lasciato 'ibrido', scarta annunci esplicitamente '100% in sede / solo in sede'
    if "ibrido" in work_modes and "in_sede" not in work_modes and not is_remote_job:
        if any(w in full_text for w in ["100% in sede", "esclusivamente in sede", "full onsite"]):
            return False, "Modalità in sede esclusa (richiesto Ibrido o Remoto)"

    # --- 2. STACK ESCLUSO (completamente personalizzabile via `search_preferences.excluded_keywords`) ---
    excluded_tech = search_prefs.get("excluded_keywords") or []
    active_kw_lower = " ".join(search_prefs.get("keywords", [])).lower()
    for et in excluded_tech:
        # Non escludere una tecnologia se l'utente l'ha aggiunta esplicitamente tra i ruoli cercati!
        if et.lower() not in active_kw_lower and et.lower() in title:
            return False, f"Tecnologia esclusa dal profilo ({et})"

    # --- 3. SENIORITY ED ANNI ESPERIENZA ---
    target_seniority = candidate_config.get("candidate_profile", {}).get("target_seniority", {})
    max_years = target_seniority.get("max_years_experience", 3)
    min_years_pref = candidate_config.get("candidate_profile", {}).get("min_years_experience", 0)
    accepted_tiers = [t.lower() for t in target_seniority.get("accepted_tiers", ["stage", "junior", "mid"])]

    if "senior" not in accepted_tiers:
        reject_seniorities = [
            s.lower() for s in target_seniority.get(
                "reject_seniorities",
                ["senior", "sr.", "lead", "principal", "staff engineer", "head of", "director", "vp ", "manager", "architect"],
            )
        ]
        if any(st in title for st in reject_seniorities):
            return False, "Seniority esclusa (Senior / Lead / Manager)"

    if "stage" not in accepted_tiers:
        stage_terms = ["stage", "internship", "intern", "tirocinio", "tirocinante", "trainee", "tesi"]
        if any(re.search(rf'\b{t}\b', title) for t in stage_terms):
            return False, "Seniority esclusa (Stage / Internship)"

    if "junior" not in accepted_tiers:
        jr_terms = ["junior", "entry level", "neolaureat", "neodiplomat", "graduate"]
        if any(re.search(rf'\b{t}\b', title) for t in jr_terms):
            return False, "Seniority esclusa (Junior / Entry Level)"

    exp_matches = re.findall(
        r'(\d+)\s*(?:\+|\s*-\s*\d+)?\s*(?:anni|years|anno|year)\s*(?:di\s+esperienza|of\s+experience)',
        full_text,
    )
    for exp_str in exp_matches:
        try:
            req_years = int(exp_str)
            if req_years > max_years:
                return False, f"Esperienza richiesta troppo elevata ({req_years} anni vs max {max_years})"
            if min_years_pref > 0 and req_years < min_years_pref:
                return False, f"Esperienza richiesta inferiore al minimo desiderato ({req_years} anni vs min {min_years_pref})"
        except ValueError:
            pass

    # --- 4. RUOLI TARGET DINAMICI ---
    configured_keywords = search_prefs.get("keywords", [])
    target_roles = _expand_role_keywords(configured_keywords)
    head_text = f"{title} {desc[:350]}"
    if not any(re.search(rf'\b{re.escape(tr)}\b', head_text) for tr in target_roles):
        return False, f"Ruolo non in linea con le posizioni cercate ({', '.join(configured_keywords[:4])})"

    return True, "OK"


class GeminiMatcher:
    def __init__(self):
        self.client = (
            genai.Client(api_key=settings.GEMINI_API_KEY)
            if settings.GEMINI_API_KEY and settings.GEMINI_API_KEY != "inserisci_qui_la_tua_api_key_gemini"
            else None
        )
        self.model_name = settings.GEMINI_MODEL

    async def evaluate_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        config = load_candidate_config()

        passed, reason = quick_prefilter(job, config)
        if not passed:
            return {
                "match_score": 10,
                "estimated_ral": "Non valutata",
                "matching_skills": [],
                "missing_skills": [reason],
                "is_dealbreaker_missing": True,
                "ai_reasoning": f"Scartato: {reason}.",
                "tailored_cover_note": "",
            }

        full_announcement_text = f"{job.get('title', '')} {job.get('description', '')}"
        regex_ral = extract_ral_with_regex(full_announcement_text)

        if not self.client:
            return {
                "match_score": 75,
                "estimated_ral": regex_ral,
                "matching_skills": ["Competenze in linea con il profilo"],
                "missing_skills": ["Configura GEMINI_API_KEY"],
                "is_dealbreaker_missing": False,
                "ai_reasoning": "Valutazione preliminare superata (configura GEMINI_API_KEY per l'analisi approfondita).",
                "tailored_cover_note": f"Buongiorno, invio la mia candidatura per la posizione di {job.get('title')}.",
            }

        cv_text = extract_text_from_cv()
        profile_info = config.get("candidate_profile", {})
        search_prefs = config.get("search_preferences", {})

        candidate_name = f"{profile_info.get('first_name', '')} {profile_info.get('last_name', '')}".strip() or "il candidato"
        education_info = profile_info.get("education", "")
        custom_notes = profile_info.get("custom_Preferences_notes", "")
        target_cities = search_prefs.get("target_cities") or [profile_info.get("city", "Italia")]
        work_modes = search_prefs.get("work_modes") or ["remoto", "ibrido", "in_sede"]
        target_keywords = search_prefs.get("keywords") or ["Software Engineer", "AI Engineer"]
        max_exp = profile_info.get("target_seniority", {}).get("max_years_experience", 3)

        prompt = f"""
Sei un Technical Recruiter incaricato di valutare le offerte di lavoro per {candidate_name}.
Titolo di studio / Formazione: {education_info}
Note e preferenze del candidato: {custom_notes}

REGOLE VINCOLANTI DI VALUTAZIONE (impostate dall'utente):
1. CATEGORIE PROTETTE: Se l'offerta è riservata a categorie protette (L. 68/99 o simili), assegna match_score 0 e is_dealbreaker_missing: true.
2. CANDIDATURA GIÀ INOLTRATA: Se compare la dicitura 'candidatura inoltrata' o simile, assegna match_score 0 e is_dealbreaker_missing: true.
3. AREA GEOGRAFICA E MODALITÀ:
   - Città / Aree ammesse: {', '.join(target_cities)}
   - Modalità di lavoro ammesse: {', '.join(work_modes)}
   Se l'offerta richiede presenza in una città NON inclusa tra quelle ammesse, assegna match_score < 30 e is_dealbreaker_missing: true.
4. POSIZIONI / RUOLI CERCATE: {', '.join(target_keywords)}.
   Scarta ruoli fuori target o con tecnologie escluse dal profilo.
5. SENIORITY: Massimo {max_exp} anni di esperienza richiesta. Scarta posizioni Senior, Lead, Principal, Director o Manager se il profilo è Junior/Mid.
6. FEDELTÀ AI DATI DEL CANDIDATO: Attieniti esclusivamente al titolo di studio e all'università indicati in "{education_info}" e nel CV senza mai inventare atenei diversi.

Compito:
- Calcola match_score (0-100).
- Gestione RAL / Stipendio: NON INVENTARE E NON STIMARE MAI LA RAL. Cerca ESCLUSIVAMENTE se nell'annuncio è esplicitamente indicato un valore o range retributivo (es. '28.000€ - 32.000€', '1.000€/mese'). Se non è esplicitato chiaramente nel testo dell'annuncio, imposta estimated_ral TASSATIVAMENTE su 'Non specificata'.

--- PROFILO CANDIDATO ---
{json.dumps(profile_info, ensure_ascii=False)}

--- CV ESTRATTO ---
{cv_text[:6000]}

--- ANNUNCIO DA VALUTARE ---
Titolo: {job.get('title')}
Azienda: {job.get('company')}
Località: {job.get('location')}
Descrizione:
{job.get('description', '')[:5000]}
"""

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=JobMatchEvaluation,
                        temperature=0.1,
                    ),
                )
                result = json.loads(response.text)

                ai_ral = (result.get("estimated_ral") or "").strip()
                if (not ai_ral or "non specificata" in ai_ral.lower() or "stim" in ai_ral.lower()) and regex_ral != "Non specificata":
                    result["estimated_ral"] = regex_ral
                elif not ai_ral or "stim" in ai_ral.lower():
                    result["estimated_ral"] = "Non specificata"

                return result
            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    wait_sec = 8 * (attempt + 1)
                    logger.warning(f"Rate limit Gemini (429), pausa di {wait_sec}s...")
                    await asyncio.sleep(wait_sec)
                else:
                    logger.error(f"Errore chiamata Gemini: {e}")
                    break

        return {
            "match_score": 0,
            "estimated_ral": "Non specificata",
            "matching_skills": [],
            "missing_skills": [],
            "is_dealbreaker_missing": False,
            "ai_reasoning": "Errore temporaneo quota Gemini (429)",
            "tailored_cover_note": "",
        }

    async def answer_application_question(self, question_label: str, options: List[str] = None) -> str:
        config = load_candidate_config()
        profile = config.get("candidate_profile", {})
        q_lower = question_label.lower()
        if "anni" in q_lower or "years" in q_lower or "experience" in q_lower:
            return str(profile.get("years_of_experience_default", 1))
        if "ral" in q_lower or "salary" in q_lower or "compenso" in q_lower:
            return str(profile.get("expected_salary_ral", "30000"))
        if "preavviso" in q_lower or "notice" in q_lower:
            return str(profile.get("notice_period_days", "30"))
        return "Yes" if not options else options[0]
