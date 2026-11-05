import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent
STATE_FILE = BASE_DIR / "data" / "browser_state" / "linkedin_state.json"
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

# Percorso di Google Chrome reale installato sul tuo PC
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


async def interactive_login():
    """
    Apre Google Chrome REALE (non Chromium di test) con profilo anti-rilevamento completo.
    In questo modo il pulsante 'Accedi con Google' compare sempre!
    """
    print("\n========================================================")
    print("Avvio Google Chrome reale in corso...")
    print("========================================================\n")

    async with async_playwright() as p:
        # Usiamo l'eseguibile di Google Chrome reale
        browser = await p.chromium.launch(
            executable_path=CHROME_PATH,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-default-browser-check",
                "--no-first-run",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="it-IT",
        )

        page = await context.new_page()

        # Apriamo la pagina di benvenuto/login di LinkedIn dove c'è il tasto Google
        await page.goto("https://www.linkedin.com/", wait_until="domcontentloaded")

        print("========================================================")
        print("1. Nella finestra di Chrome che si è aperta, clicca su 'Continua con Google' o 'Accedi'.")
        print("2. Completa l'accesso fino a vedere la home/feed di LinkedIn.")
        print("3. Torna qui nel terminale e premi INVIO per salvare la sessione.")
        print("========================================================\n")

        input("Premi INVIO una volta effettuato l'accesso su LinkedIn...")

        await context.storage_state(path=str(STATE_FILE))
        print(f"\n✅ SUCCESSO! Sessione e cookie salvati in:\n{STATE_FILE}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(interactive_login())
