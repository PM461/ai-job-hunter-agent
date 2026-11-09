import asyncio
import json
import logging
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
MAPPING_FILE = BASE_DIR / "data" / "career_sites_mapping.json"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

# Lista di tutti i siti carriere e ATS specifici delle aziende indicate
COMPANY_SITES = [
    {
        "company": "Reply Group (Machine Learning Reply, Data Reply)",
        "start_url": "https://www.reply.com/it/about/careers/job-opportunities",
        "notes": "Filtra per sede Torino / Italia e area AI / Software"
    },
    {
        "company": "Leonardo S.p.A.",
        "start_url": "https://www.leonardo.com/it/careers/open-positions",
        "notes": "Portale carriere Leonardo (Torino / Caselle / Remote)"
    },
    {
        "company": "Thales Alenia Space / Altec",
        "start_url": "https://jobs.thalesgroup.com/search-jobs/Italy",
        "notes": "Posizioni Thales & Altec Torino"
    },
    {
        "company": "Intesa Sanpaolo / ISP Innovation Center",
        "start_url": "https://group.intesasanpaolo.com/it/carriere/posizioni-aperte",
        "notes": "Grattacielo Torino / AI / Data / IT"
    },
    {
        "company": "Banca Sella & Centrico",
        "start_url": "https://www.sellagroup.eu/it/lavora-con-noi",
        "notes": "Centrico / Sella Torino & Biella IT"
    },
    {
        "company": "Iveco Group & CNH Industrial",
        "start_url": "https://www.ivecogroup.com/careers/opportunities",
        "notes": "Sede Torino Lungo Stura Lazio / R&D"
    },
    {
        "company": "Zucchetti Spa & Tilby",
        "start_url": "https://www.zucchetti.it/it/lavora-con-noi.html",
        "notes": "Software & POS Tilby Torino"
    },
    {
        "company": "Teoresi Group & Italdesign & Stellantis",
        "start_url": "https://www.teoresigroup.com/it/carriere/",
        "notes": "Teoresi Torino (AI, Automotive, Smart Mobility)"
    },
    {
        "company": "IIT (Istituto Italiano di Tecnologia) & LINKS",
        "start_url": "https://www.iit.it/it/careers-opportunities",
        "notes": "Centri di Ricerca Torino & Genova"
    },
    {
        "company": "Amazon Development Centre Italy (Torino)",
        "start_url": "https://www.amazon.jobs/en/locations/turin-italy",
        "notes": "Amazon Alexa / AI Lab Torino"
    },
    {
        "company": "Accenture Italia",
        "start_url": "https://www.accenture.com/it-it/careers/jobsearch?jk=&sb=1&vw=0&is_rg=1&rgn=Torino",
        "notes": "Accenture Cloud & AI Torino"
    },
    {
        "company": "Deloitte Italia",
        "start_url": "https://www.deloitte.com/it/it/careers/candidati.html",
        "notes": "Deloitte AI & Data Torino"
    },
    {
        "company": "NTT DATA Italia",
        "start_url": "https://it.nttdata.com/careers/offerte-di-lavoro",
        "notes": "NTT DATA Torino / AI & Cloud"
    },
    {
        "company": "Capgemini / Capgemini Engineering",
        "start_url": "https://www.capgemini.com/it-it/carriere/trova-il-tuo-lavoro/",
        "notes": "Ex-Altran Torino"
    },
    {
        "company": "Akkodis (Modis / Akka) & Alten",
        "start_url": "https://www.akkodis.com/it/candidates/jobs",
        "notes": "Engineering & Tech Torino"
    },
    {
        "company": "CSI Piemonte & TIM",
        "start_url": "https://www.csipiemonte.it/it/lavora-con-noi",
        "notes": "Consorzio per il Sistema Informativo Torino"
    },
    {
        "company": "Reale Mutua & UnipolTech",
        "start_url": "https://careers.realegroup.eu/",
        "notes": "Reale Group HQ Torino"
    },
    {
        "company": "I3P PoliTO & OGR Tech (Startup AI Torino)",
        "start_url": "https://www.i3p.it/opportunita-lavoro",
        "notes": "Startup incubatore PoliTO & OGR"
    }
]


async def run_interactive_site_mapper():
    """
    Apre Google Chrome reale sito per sito:
    Ti permette di navigare fino alla pagina esatta con l'elenco delle posizioni
    (o applicare filtri di sede/keyword). Quando sei sulla pagina giusta, premi
    INVIO nel terminale e lo script salva l'URL effettivo e la struttura per l'agente.
    """
    print("\n" + "="*70)
    print("🎯 MAPPER INTERATTIVO DEI PORTALI AZIENDALI")
    print("="*70)
    print("Per ogni azienda si aprirà Google Chrome.")
    print("1. Naviga o accetta eventuali cookie fino alla pagina con le offerte.")
    print("2. Applica se vuoi il filtro 'Torino' o 'Junior/AI' sul loro form.")
    print("3. Torna qui nel terminale e premi INVIO.")
    print("   -> L'agente salverà l'URL esatto per le future scansioni orarie!")
    print("="*70 + "\n")

    saved_mappings = {}
    if MAPPING_FILE.exists():
        try:
            with open(MAPPING_FILE, "r", encoding="utf-8") as f:
                saved_mappings = json.load(f)
        except Exception:
            saved_mappings = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=CHROME_PATH,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(viewport={"width": 1366, "height": 850})
        page = await context.new_page()

        for idx, item in enumerate(COMPANY_SITES, 1):
            comp_name = item["company"]
            url = item["start_url"]
            notes = item["notes"]

            print(f"\n[{idx}/{len(COMPANY_SITES)}] 🏢 Azienda: {comp_name}")
            print(f"🔗 Apertura: {url}")
            print(f"💡 Suggerimento: {notes}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                print(f"⚠️ Nota apertura: {e}")

            print("\n👉 Naviga nella finestra del browser fino alla lista annunci.")
            print("   - Premi INVIO per salvare questa pagina esatta.")
            print("   - Oppure scrivi 's' e premi INVIO per saltare questa azienda.")
            
            user_choice = input("Comando [INVIO=Salva, s=Salta]: ").strip().lower()

            if user_choice != 's':
                final_url = page.url
                saved_mappings[comp_name] = {
                    "mapped_url": final_url,
                    "notes": notes
                }
                print(f"✅ Mappato: {final_url}")
                # Salva progressivamente sul disco
                with open(MAPPING_FILE, "w", encoding="utf-8") as f:
                    json.dump(saved_mappings, f, indent=2, ensure_ascii=False)
            else:
                print(f"⏩ Saltata {comp_name}")

        await browser.close()

    print("\n" + "="*70)
    print(f"🎉 MAPPATURA COMPLETATA! {len(saved_mappings)} portali registrati in:")
    print(f"{MAPPING_FILE}")
    print("="*70)


if __name__ == "__main__":
    asyncio.run(run_interactive_site_mapper())

