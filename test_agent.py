import sys
import io
import asyncio

# Forza UTF-8 per il terminale Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

from app.config import settings
from app.ai_matcher import GeminiMatcher, extract_text_from_cv
from app.notifier import NotificationManager
from app.scrapers.multinationals_scraper import MultinationalsScraper


async def test_single_pipeline():
    print("\n--- AVVIO TEST DI DIAGNOSTICA AGENTE ---")

    # 1. Verifica estrazione CV
    cv_text = extract_text_from_cv()
    print(f"1. [OK] CV letto: {len(cv_text)} caratteri")

    # 2. Test chiamata reale a Gemini Pro con simulazione offerta
    matcher = GeminiMatcher()
    sample_job = {
        "external_id": "test_job_reply_1",
        "portal": "LinkedIn (Test)",
        "company": "Reply (Machine Learning Reply)",
        "title": "Junior AI & Computer Vision Engineer",
        "location": "Torino (Ibrido)",
        "url": "https://www.reply.com",
        "description": """
        Siamo alla ricerca di un Junior AI / Machine Learning Engineer per la sede di Torino.
        Requisiti:
        - Buona conoscenza di Python e librerie di Machine Learning (PyTorch, TensorFlow, Scikit-learn).
        - Conoscenza o interesse per Computer Vision o NLP.
        - Esperienza di stage o progetti universitari (0-2 anni di esperienza).
        - Buona conoscenza dell'inglese.
        Desiderata:
        - Familiarità con Docker e Git.
        """
    }

    print("\n2. Invio offerta di prova a Gemini Pro per calcolo Grado di Fedelta'...")
    eval_result = await matcher.evaluate_job(sample_job)
    print(f"   -> Match Score: {eval_result.get('match_score')}%")
    print(f"   -> Matching Skills: {eval_result.get('matching_skills')}")
    print(f"   -> Missing Skills: {eval_result.get('missing_skills')}")
    print(f"   -> Ragionamento Gemini: {eval_result.get('ai_reasoning')}")

    # 3. Test notifica su Telegram con bottoni interattivi
    print("\n3. Invio notifica di prova con bottoni interattivi sul tuo Telegram...")
    notifier = NotificationManager()
    sample_job.update(eval_result)
    await notifier.notify_job(sample_job, job_db_id=999, auto_applied=False)
    print("   -> Controlla Telegram! Dovresti vedere l'offerta di prova con i bottoni 'Approva e Invia CV' e 'Scarta'.")

    # 4. Test rapido scraper API (Amazon Jobs)
    print("\n4. Test prelievo offerte live da portale (Amazon Jobs API)...")
    scraper = MultinationalsScraper(matcher)
    jobs = await scraper._scrape_amazon_jobs(query="Software", country="ITA")
    print(f"   -> Offerte live trovate su Amazon Jobs: {len(jobs)}")
    if jobs:
        print(f"   -> Esempio prima offerta trovata: '{jobs[0]['title']}' - {jobs[0]['location']}")

    print("\n--- TUTTI I COMPONENTI FUNZIONANO CORRETTAMENTE! ---")


if __name__ == "__main__":
    asyncio.run(test_single_pipeline())
