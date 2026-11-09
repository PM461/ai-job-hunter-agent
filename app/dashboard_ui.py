from app.config import settings, load_candidate_config


def render_full_dashboard_html() -> str:
    full_cfg = load_candidate_config()
    cfg = full_cfg.get("candidate_profile", {})
    prefs = full_cfg.get("search_preferences", {})
    current_ral = cfg.get("expected_salary_ral", "30000")
    candidate_name = f"{cfg.get('first_name', 'Candidato')} {cfg.get('last_name', '')}".strip()
    active_cities = ", ".join(prefs.get("target_cities", []) or [cfg.get("city", "Italia")])
    review_thresh = settings.REVIEW_THRESHOLD
    auto_thresh = settings.AUTO_APPLY_THRESHOLD

    return f"""<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Job Hunter & Market Intelligence - {candidate_name}</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
    <style>
        :root {{
            --bg-main: #0b1120;
            --bg-card: #131c31;
            --bg-card-hover: #1a2642;
            --bg-input: #0f172a;
            --border: #263554;
            --text-main: #f1f5f9;
            --text-muted: #94a3b8;
            --accent: #38bdf8;
            --accent-blue: #2563eb;
            --accent-green: #10b981;
            --accent-amber: #f59e0b;
            --accent-purple: #a855f7;
            --accent-rose: #f43f5e;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-main);
            color: var(--text-main);
            margin: 0;
            padding: 0;
        }}
        /* Topbar */
        .topbar {{
            background: rgba(19, 28, 49, 0.95);
            border-bottom: 1px solid var(--border);
            padding: 14px 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: sticky;
            top: 0;
            z-index: 100;
            backdrop-filter: blur(10px);
            flex-wrap: wrap;
            gap: 12px;
        }}
        .brand h1 {{
            margin: 0;
            font-size: 20px;
            font-weight: 700;
            display: flex;
            align-items: center;
            gap: 10px;
            color: #fff;
        }}
        .brand p {{
            margin: 3px 0 0 0;
            font-size: 12.5px;
            color: var(--text-muted);
        }}
        .nav-tabs {{
            display: flex;
            gap: 8px;
            background: var(--bg-input);
            padding: 5px;
            border-radius: 10px;
            border: 1px solid var(--border);
        }}
        .nav-tab {{
            padding: 8px 16px;
            border-radius: 7px;
            border: none;
            background: transparent;
            color: var(--text-muted);
            font-weight: 600;
            font-size: 13.5px;
            cursor: pointer;
            transition: all 0.15s;
        }}
        .nav-tab.active {{
            background: var(--accent-blue);
            color: #fff;
            box-shadow: 0 2px 8px rgba(37, 99, 235, 0.4);
        }}
        .top-actions {{
            display: flex;
            gap: 8px;
            align-items: center;
            flex-wrap: wrap;
        }}
        .btn {{
            padding: 8px 14px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--bg-card);
            color: var(--text-main);
            font-weight: 600;
            font-size: 12.5px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            transition: all 0.15s;
        }}
        .btn:hover {{ background: var(--bg-card-hover); border-color: var(--accent); }}
        .btn-primary {{ background: var(--accent-green); border-color: var(--accent-green); color: #fff; }}
        .btn-primary:hover {{ background: #059669; border-color: #059669; }}
        .btn-blue {{ background: var(--accent-blue); border-color: var(--accent-blue); color: #fff; }}
        .btn-blue:hover {{ background: #1d4ed8; }}
        .btn-sm {{ padding: 5px 10px; font-size: 11.5px; border-radius: 6px; }}

        /* Main Layout */
        .layout {{
            display: grid;
            grid-template-columns: 310px 1fr;
            gap: 20px;
            padding: 20px 24px;
            max-width: 1920px;
            margin: 0 auto;
        }}
        @media (max-width: 1100px) {{
            .layout {{ grid-template-columns: 1fr; }}
        }}

        /* Sidebar Companies Selector */
        .sidebar {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            display: flex;
            flex-direction: column;
            max-height: calc(100vh - 100px);
            position: sticky;
            top: 82px;
        }}
        .sidebar-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }}
        .sidebar-header h3 {{
            margin: 0;
            font-size: 14.5px;
            color: #fff;
        }}
        .company-search {{
            width: 100%;
            padding: 8px 11px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--bg-input);
            color: var(--text-main);
            font-size: 13px;
            margin-bottom: 10px;
        }}
        .company-quick-btns {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 6px;
            margin-bottom: 10px;
        }}
        .company-list {{
            flex: 1;
            overflow-y: auto;
            border-top: 1px solid var(--border);
            padding-top: 8px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }}
        .company-item {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 6px 8px;
            border-radius: 6px;
            font-size: 12.5px;
            cursor: pointer;
            user-select: none;
            transition: background 0.12s;
        }}
        .company-item:hover {{ background: var(--bg-card-hover); }}
        .company-item label {{
            display: flex;
            align-items: center;
            gap: 8px;
            cursor: pointer;
            flex: 1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .company-badges {{
            display: flex;
            gap: 4px;
            align-items: center;
        }}
        .badge-count {{
            background: #1e293b;
            color: var(--text-muted);
            padding: 2px 6px;
            border-radius: 10px;
            font-size: 11px;
            font-weight: 600;
        }}
        .badge-match {{
            background: rgba(16, 185, 129, 0.2);
            color: #34d399;
            padding: 2px 6px;
            border-radius: 10px;
            font-size: 11px;
            font-weight: 600;
        }}

        /* Filter Bar */
        .filter-panel {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 18px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
            gap: 12px;
            align-items: end;
        }}
        .filter-group {{
            display: flex;
            flex-direction: column;
            gap: 5px;
        }}
        .filter-group label {{
            font-size: 11.5px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.4px;
        }}
        .filter-group select, .filter-group input {{
            padding: 8px 10px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: var(--bg-input);
            color: var(--text-main);
            font-size: 13px;
        }}

        /* KPI Grid */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 14px;
            margin-bottom: 18px;
        }}
        .kpi-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            position: relative;
            overflow: hidden;
        }}
        .kpi-card .kpi-label {{
            font-size: 12px;
            color: var(--text-muted);
            text-transform: uppercase;
            font-weight: 600;
        }}
        .kpi-card .kpi-value {{
            font-size: 26px;
            font-weight: 800;
            margin: 6px 0 4px 0;
            color: #fff;
        }}
        .kpi-card .kpi-sub {{
            font-size: 12px;
            color: var(--accent);
        }}

        /* Market Pulse Insights */
        .pulse-box {{
            background: linear-gradient(135deg, rgba(37, 99, 235, 0.15), rgba(16, 185, 129, 0.1));
            border: 1px solid rgba(56, 189, 248, 0.35);
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 18px;
        }}
        .pulse-box h3 {{
            margin: 0 0 10px 0;
            font-size: 15px;
            color: var(--accent);
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .pulse-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 12px;
            font-size: 13px;
            line-height: 1.45;
            color: #e2e8f0;
        }}
        .pulse-item {{
            background: rgba(15, 23, 42, 0.65);
            padding: 10px 14px;
            border-radius: 8px;
            border-left: 3px solid var(--accent);
        }}

        /* Charts Grid */
        .charts-grid {{
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 18px;
            margin-bottom: 22px;
        }}
        .chart-card.full-width {{
            grid-column: 1 / -1;
        }}
        @media (max-width: 1300px) {{
            .charts-grid {{ grid-template-columns: 1fr; }}
        }}
        .chart-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px 18px;
            display: flex;
            flex-direction: column;
        }}
        .chart-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 12px;
            gap: 10px;
        }}
        .chart-header h4 {{
            margin: 0;
            font-size: 15px;
            color: #fff;
        }}
        .chart-header p {{
            margin: 3px 0 0 0;
            font-size: 12px;
            color: var(--text-muted);
        }}
        .chart-canvas-wrap {{
            position: relative;
            width: 100%;
            height: 320px;
        }}
        .chart-card.tall .chart-canvas-wrap {{
            height: 390px;
        }}

        /* Table Card */
        .table-card {{
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 18px;
            overflow-x: auto;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}
        th {{
            text-align: left;
            padding: 11px 10px;
            border-bottom: 2px solid var(--border);
            color: var(--text-muted);
            font-size: 12px;
            text-transform: uppercase;
            white-space: nowrap;
        }}
        td {{
            padding: 11px 10px;
            border-bottom: 1px solid rgba(38, 53, 84, 0.6);
            font-size: 13.5px;
            vertical-align: top;
        }}
        tr:hover td {{ background: rgba(26, 38, 66, 0.5); }}
        .skill-pill {{
            display: inline-block;
            background: rgba(56, 189, 248, 0.14);
            color: #7dd3fc;
            border: 1px solid rgba(56, 189, 248, 0.3);
            padding: 2px 7px;
            border-radius: 6px;
            font-size: 11px;
            margin: 2px 3px 2px 0;
        }}
        .missing-pill {{
            display: inline-block;
            background: rgba(245, 158, 11, 0.15);
            color: #fcd34d;
            border: 1px solid rgba(245, 158, 11, 0.3);
            padding: 2px 7px;
            border-radius: 6px;
            font-size: 11px;
            margin: 2px 3px 2px 0;
        }}
        .cat-badge {{
            display: inline-block;
            background: #1e293b;
            color: #cbd5e1;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11.5px;
            font-weight: 600;
        }}
        .sen-pill-btn {{
            padding: 5px 12px;
            border-radius: 20px;
            border: 1px solid var(--border);
            background: rgba(30, 41, 59, 0.7);
            color: var(--text-muted);
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }}
        .sen-pill-btn:hover {{
            background: rgba(56, 189, 248, 0.15);
            border-color: var(--accent);
            color: #fff;
        }}
        .sen-pill-btn.active {{
            background: var(--accent);
            color: #0b1120 !important;
            border-color: var(--accent);
            font-weight: 700;
            box-shadow: 0 0 12px rgba(56, 189, 248, 0.35);
        }}
        .btn-quick-link {{
            background: rgba(56, 189, 248, 0.12);
            border: 1px solid rgba(56, 189, 248, 0.35);
            color: #38bdf8;
            font-weight: 600;
            white-space: nowrap;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            transition: all 0.15s ease;
        }}
        .btn-quick-link:hover {{
            background: #38bdf8;
            color: #0b1120;
            border-color: #38bdf8;
        }}
        .app-toggle-label {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            cursor: pointer;
            user-select: none;
        }}
        .app-toggle-checkbox {{
            width: 17px;
            height: 17px;
            cursor: pointer;
            accent-color: #10b981;
        }}
        .app-status-badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            letter-spacing: 0.2px;
            white-space: nowrap;
            transition: all 0.15s ease;
        }}
        .app-status-badge.is-applied {{
            background: rgba(16, 185, 129, 0.2);
            color: #34d399;
            border: 1px solid rgba(16, 185, 129, 0.4);
        }}
        .app-status-badge.is-not-applied {{
            background: rgba(148, 163, 184, 0.12);
            color: #94a3b8;
            border: 1px solid rgba(148, 163, 184, 0.25);
        }}
        .hidden {{ display: none !important; }}
    </style>
</head>
<body>

    <!-- Top Navigation Bar -->
    <div class="topbar">
        <div class="brand">
            <h1>🤖 AI Job Hunter & Market Intelligence Suite</h1>
            <p>Candidato: <strong>{candidate_name}</strong> • Area Target: <strong>{active_cities}</strong> • Obiettivo RAL: <strong style="color:#10b981;">{current_ral}€</strong> • Soglia Notifica: <strong>&ge;{review_thresh}%</strong></p>
        </div>

        <div class="nav-tabs">
            <button id="tabBtnAnalytics" class="nav-tab active" onclick="switchView('analytics')">📊 Market Intelligence & Trend Storici</button>
            <button id="tabBtnQueue" class="nav-tab" onclick="switchView('queue')">🎯 Coda Candidature & Valutazioni AI</button>
            <button id="tabBtnHealth" class="nav-tab" onclick="switchView('health')">🩺 Diagnostica DOM & Scraper <span id="navDomAlertBadge" style="display:none;background:#f43f5e;color:#fff;padding:1px 7px;border-radius:10px;font-size:11px;margin-left:5px;">0</span></button>
            <button id="tabBtnProfile" class="nav-tab" onclick="switchView('profile')">⚙️ Profilo Su Misura</button>
        </div>

        <div class="top-actions">
            <button class="btn" id="btnVerifyDomTop" onclick="runDomHealthCheck()" title="Verifica se i portali hanno cambiato DOM o se le offerte non sono più in chiaro">🩺 Verifica DOM</button>
            <button class="btn" onclick="downloadAllCharts()" title="Scarica tutti i 9 grafici in formato PNG ad alta risoluzione">📦 Salva Tutti i Grafici (PNG)</button>
            <button class="btn" onclick="exportFilteredCSV()" title="Esporta i dati filtrati in CSV">📥 Esporta CSV</button>
            <button class="btn" id="btnBackfill" onclick="triggerDateBackfill()" title="Sincronizza le date di pubblicazione reali dai portali">🔄 Sincronizza Date</button>
            <button class="btn btn-primary" onclick="runNow()">🔍 Avvia Scansione Ora</button>
        </div>
    </div>

    <!-- Main Content Layout -->
    <div class="layout">
        <!-- Left Sidebar: Interactive Company Selector -->
        <aside class="sidebar">
            <div class="sidebar-header">
                <h3>🏢 Aziende Monitorate (<span id="selectedCompCount">0</span>/<span id="totalCompCount">0</span>)</h3>
                <button class="btn btn-sm" onclick="saveCompanyPreset()" title="Salva la selezione corrente di aziende nel browser">💾 Salva</button>
            </div>
            <input type="text" id="companySearchInput" class="company-search" placeholder="🔎 Cerca azienda (es. Reply, Sella, Leonardo...)" oninput="renderCompanyList()">
            <div class="company-quick-btns">
                <button class="btn btn-sm" onclick="selectAllCompanies(true)">✅ Tutte</button>
                <button class="btn btn-sm" onclick="selectAllCompanies(false)">⬜ Nessuna</button>
                <button class="btn btn-sm" onclick="selectTopCompanies(15)">🔥 Top 15</button>
                <button class="btn btn-sm" onclick="selectMatchedCompanies()">🎯 Con Match AI</button>
                <button class="btn btn-sm" onclick="invertCompanySelection()">🔄 Inverti</button>
                <button class="btn btn-sm" onclick="loadCompanyPreset()">📂 Carica Salvate</button>
            </div>
            <div id="companyListContainer" class="company-list">
                <!-- Populated dynamically via JS -->
            </div>
        </aside>

        <!-- Right Main Area -->
        <main>
            <!-- Banner Globale Monitoraggio DOM & Integrità Scraper -->
            <div id="domHealthBanner" style="margin-bottom:16px;padding:12px 18px;border-radius:10px;border:1px solid rgba(16,185,129,0.35);background:rgba(16,185,129,0.08);display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
                <div id="domHealthBannerText" style="font-size:13px;color:#e2e8f0;">
                    🩺 <strong>Monitoraggio DOM &amp; Offerte in Chiaro:</strong> Caricamento stato portali in corso...
                </div>
                <div style="display:flex;gap:8px;align-items:center;">
                    <button class="btn btn-sm" onclick="switchView('health')">📋 Dettaglio Portali</button>
                    <button class="btn btn-sm btn-blue" id="btnVerifyDomBanner" onclick="runDomHealthCheck()">🔍 Verifica DOM Live</button>
                </div>
            </div>

            <!-- Global Market Filters -->
            <section class="filter-panel">
                <div class="filter-group">
                    <label>📅 Riferimento Data Storico</label>
                    <select id="fltDateField" onchange="applyFiltersAndRender()">
                        <option value="published_at" selected>Data di Pubblicazione Offerta</option>
                        <option value="discovered_at">Data di Scoperta / Scansione</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>⏱ Raggruppamento Trend</label>
                    <select id="fltTimeGranularity" onchange="applyFiltersAndRender()">
                        <option value="day" selected>Giornaliero</option>
                        <option value="week">Settimanale</option>
                        <option value="month">Mensile</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>📆 Orizzonte Temporale</label>
                    <select id="fltPeriodDays" onchange="onPeriodPresetChange()">
                        <option value="all" selected>Tutto lo Storico</option>
                        <option value="7">Ultimi 7 Giorni</option>
                        <option value="14">Ultimi 14 Giorni</option>
                        <option value="30">Ultimi 30 Giorni</option>
                        <option value="90">Ultimi 90 Giorni</option>
                        <option value="180">Ultimi 6 Mesi</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>Dal Giorno</label>
                    <input type="date" id="fltDateFrom" onchange="applyFiltersAndRender()">
                </div>
                <div class="filter-group">
                    <label>Al Giorno</label>
                    <input type="date" id="fltDateTo" onchange="applyFiltersAndRender()">
                </div>
                <div class="filter-group">
                    <label>💼 Famiglia Professionale</label>
                    <select id="fltCategory" onchange="applyFiltersAndRender()">
                        <option value="all">Tutte le Categorie di Mercato</option>
                        <option value="AI, Machine Learning & Data Science">AI, Machine Learning & Data Science</option>
                        <option value="Software, Backend & Full-Stack">Software, Backend & Full-Stack</option>
                        <option value="Data Engineering & BI / Analytics">Data Engineering & BI / Analytics</option>
                        <option value="Cloud, DevOps & Cybersecurity">Cloud, DevOps & Cybersecurity</option>
                        <option value="Embedded, Automotive & Hardware/IoT">Embedded, Automotive & Hardware/IoT</option>
                        <option value="IT Consulting, PM & Functional">IT Consulting, PM & Functional</option>
                        <option value="Altri Ruoli / Operations & Engineering">Altri Ruoli / Operations & Engineering</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>📍 Polo Geografico</label>
                    <select id="fltCity" onchange="applyFiltersAndRender()">
                        <option value="all">Tutte le Sedi</option>
                        <option value="Torino">Torino & Piemonte</option>
                        <option value="Milano">Milano & Lombardia</option>
                        <option value="Roma">Roma & Lazio</option>
                        <option value="Bologna">Bologna & Emilia</option>
                        <option value="Firenze">Firenze & Toscana</option>
                        <option value="Napoli">Napoli & Campania</option>
                        <option value="Padova / Veneto">Padova & Veneto</option>
                        <option value="Genova">Genova & Liguria</option>
                        <option value="Bari / Puglia">Bari & Puglia</option>
                        <option value="Full Remote">Full Remote</option>
                        <option value="Altre Sedi Italia">Altre Sedi Italia</option>
                        <option value="Estero / Internazionale">Estero / Internazionale</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>🏠 Modalità Lavoro</label>
                    <select id="fltWorkMode" onchange="applyFiltersAndRender()">
                        <option value="all">Tutte (Remoto + Ibrido + Sede)</option>
                        <option value="Remoto">Solo Full Remote</option>
                        <option value="Ibrido">Solo Ibrido / Smart Working</option>
                        <option value="In Sede">Solo In Sede</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>🎓 Seniority Richiesta</label>
                    <select id="fltSeniority" onchange="onSeniorityChange()">
                        <option value="all">Tutti i Livelli</option>
                        <option value="Stage / Internship">🌱 Stage / Internship</option>
                        <option value="Junior / Entry Level">🚀 Junior / Entry Level</option>
                        <option value="Mid-Level / Regular">⚡ Mid-Level / Regular</option>
                        <option value="Senior / Lead / Manager">👑 Senior / Lead / Manager</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>🎯 Filtro Match CV</label>
                    <select id="fltScope" onchange="applyFiltersAndRender()">
                        <option value="all" selected>Intero Mercato (Tutte le offerte)</option>
                        <option value="matched">Solo In Linea col CV (&ge;60%)</option>
                        <option value="high">Solo Alto Match (&ge;{review_thresh}%)</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>📝 Stato Application</label>
                    <select id="fltApplication" onchange="onApplicationFilterChange()">
                        <option value="all" selected>Tutte le Offerte</option>
                        <option value="applied">✅ Solo Già Candidate</option>
                        <option value="not_applied">⏳ Solo Da Candidare</option>
                    </select>
                </div>
                <div class="filter-group">
                    <label>🔎 Ricerca Parola Chiave / Skill</label>
                    <input type="text" id="fltSearchText" placeholder="es. Python, LLM, Vision..." oninput="applyFiltersAndRender()">
                </div>
                <div class="filter-group">
                    <button class="btn" onclick="resetAllFilters()" style="width:100%;justify-content:center;">♻️ Reset Filtri</button>
                </div>
            </section>

            <!-- VIEW 1: MARKET INTELLIGENCE & ANALYTICS -->
            <div id="viewAnalytics">
                <!-- Quick Seniority & Application Filter Pills Bar -->
                <div style="background:rgba(19, 28, 49, 0.85);border:1px solid var(--border);border-radius:10px;padding:9px 14px;margin-bottom:16px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                        <span style="font-size:12px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.4px;">🎓 Seniority:</span>
                        <div class="sen-pills-bar" style="display:flex;gap:6px;flex-wrap:wrap;">
                            <button type="button" class="sen-pill-btn active" id="pillSen_all" onclick="setSeniorityFilter('all')">Tutti (<span id="cntSen_all">0</span>)</button>
                            <button type="button" class="sen-pill-btn" id="pillSen_stage" onclick="setSeniorityFilter('Stage / Internship')">🌱 Stage (<span id="cntSen_stage">0</span>)</button>
                            <button type="button" class="sen-pill-btn" id="pillSen_junior" onclick="setSeniorityFilter('Junior / Entry Level')">🚀 Junior (<span id="cntSen_junior">0</span>)</button>
                            <button type="button" class="sen-pill-btn" id="pillSen_mid" onclick="setSeniorityFilter('Mid-Level / Regular')">⚡ Mid (<span id="cntSen_mid">0</span>)</button>
                            <button type="button" class="sen-pill-btn" id="pillSen_senior" onclick="setSeniorityFilter('Senior / Lead / Manager')">👑 Senior (<span id="cntSen_senior">0</span>)</button>
                        </div>
                    </div>
                    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
                        <span style="font-size:12px;font-weight:700;color:#94a3b8;text-transform:uppercase;letter-spacing:0.4px;">📝 Application:</span>
                        <div style="display:flex;gap:6px;">
                            <button type="button" class="sen-pill-btn active" id="pillApp_all" onclick="setApplicationFilter('all')">Tutte</button>
                            <button type="button" class="sen-pill-btn" id="pillApp_applied" onclick="setApplicationFilter('applied')">✅ Candidate (<span id="cntApp_applied">0</span>)</button>
                            <button type="button" class="sen-pill-btn" id="pillApp_not" onclick="setApplicationFilter('not_applied')">⏳ Da fare (<span id="cntApp_not">0</span>)</button>
                        </div>
                    </div>
                </div>

                <!-- 6 KPI Cards -->
                <div class="kpi-grid">
                    <div class="kpi-card">
                        <div class="kpi-label">Posizioni Analizzate (Filtro)</div>
                        <div class="kpi-value" id="kpiTotalJobs">0</div>
                        <div class="kpi-sub" id="kpiTotalSub">su 0 totali nel DB</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Aziende Attive Selezionate</div>
                        <div class="kpi-value" id="kpiActiveCompanies">0</div>
                        <div class="kpi-sub" id="kpiTopCompanySub">-</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Settore / Ruolo Dominante</div>
                        <div class="kpi-value" id="kpiTopCategory" style="font-size:18px;margin-top:10px;">-</div>
                        <div class="kpi-sub" id="kpiTopCategorySub">-</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Skill Più Richiesta dal Mercato</div>
                        <div class="kpi-value" id="kpiTopSkill" style="color:#38bdf8;">-</div>
                        <div class="kpi-sub" id="kpiTopSkillSub">-</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Quota Flessibilità (Remote/Hybrid)</div>
                        <div class="kpi-value" id="kpiFlexShare" style="color:#a855f7;">0%</div>
                        <div class="kpi-sub" id="kpiFlexSub">-</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">Offerte In Target col Tuo CV</div>
                        <div class="kpi-value" id="kpiMatchedCount" style="color:#10b981;">0</div>
                        <div class="kpi-sub" id="kpiMatchedSub">-</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">RAL Media di Mercato (Filtro)</div>
                        <div class="kpi-value" id="kpiAvgSalary" style="color:#38bdf8;">0 €</div>
                        <div class="kpi-sub" id="kpiAvgSalarySub">-</div>
                    </div>
                </div>

                <!-- Market Pulse Box -->
                <div class="pulse-box">
                    <h3>💡 Sintesi Strategica & Tendenze di Mercato (Calcolata sulle Aziende Selezionate)</h3>
                    <div class="pulse-grid" id="marketPulseContainer">
                        <!-- Populated dynamically -->
                    </div>
                </div>

                <!-- 8 Interactive Charts Grid -->
                <div class="charts-grid">
                    <!-- Chart 1: Historical Timeline -->
                    <div class="chart-card full-width">
                        <div class="chart-header">
                            <div>
                                <h4>📈 1. Storico & Trend Temporale delle Posizioni Pubblicate</h4>
                                <p>Evoluzione nel tempo delle offerte pubblicate/scoperte con media mobile a 3 periodi e quota in linea col CV</p>
                            </div>
                            <button class="btn btn-sm" onclick="saveChartAsPNG('chartTimeline', 'trend_temporale_posizioni.png')">📸 Salva Grafico</button>
                        </div>
                        <div class="chart-canvas-wrap">
                            <canvas id="chartTimeline"></canvas>
                        </div>
                    </div>

                    <!-- Chart 2: Historical Trend by Top Companies -->
                    <div class="chart-card full-width">
                        <div class="chart-header">
                            <div>
                                <h4>🏢 2. Storico Assunzioni nel Tempo per Azienda (Confronto Top Aziende Selezionate)</h4>
                                <p>Ondate di pubblicazione annunci nel tempo suddivise tra le principali aziende selezionate</p>
                            </div>
                            <button class="btn btn-sm" onclick="saveChartAsPNG('chartCompanyTimeline', 'storico_per_azienda.png')">📸 Salva Grafico</button>
                        </div>
                        <div class="chart-canvas-wrap">
                            <canvas id="chartCompanyTimeline"></canvas>
                        </div>
                    </div>

                    <!-- Chart 3: Top Companies Volume & Match -->
                    <div class="chart-card tall">
                        <div class="chart-header">
                            <div>
                                <h4>🏆 3. Classifica Aziende che Assumono di Più</h4>
                                <p>Confronto tra volume totale di posizioni aperte e posizioni compatibili col tuo profilo</p>
                            </div>
                            <button class="btn btn-sm" onclick="saveChartAsPNG('chartCompanies', 'classifica_aziende_assunzioni.png')">📸 Salva Grafico</button>
                        </div>
                        <div class="chart-canvas-wrap">
                            <canvas id="chartCompanies"></canvas>
                        </div>
                    </div>

                    <!-- Chart 4: Hard Skills Demand -->
                    <div class="chart-card tall">
                        <div class="chart-header">
                            <div>
                                <h4>🛠 4. Tecnologie e Hard Skill Più Richieste dal Mercato</h4>
                                <p>Frequenza delle competenze tecniche estratte dagli annunci delle aziende selezionate</p>
                            </div>
                            <button class="btn btn-sm" onclick="saveChartAsPNG('chartSkills', 'competenze_piu_richieste.png')">📸 Salva Grafico</button>
                        </div>
                        <div class="chart-canvas-wrap">
                            <canvas id="chartSkills"></canvas>
                        </div>
                    </div>

                    <!-- Chart 5: Skill Gap Analysis -->
                    <div class="chart-card">
                        <div class="chart-header">
                            <div>
                                <h4>🎯 5. Skill Gap Analysis (Requisiti Extra Richiesti dai Recruiter)</h4>
                                <p>Competenze segnalate più spesso come mancanti da Gemini AI nelle offerte valutate: ottime da studiare o evidenziare sul CV</p>
                            </div>
                            <button class="btn btn-sm" onclick="saveChartAsPNG('chartSkillGap', 'skill_gap_analysis.png')">📸 Salva Grafico</button>
                        </div>
                        <div class="chart-canvas-wrap">
                            <canvas id="chartSkillGap"></canvas>
                        </div>
                    </div>

                    <!-- Chart 6: Role Categories Breakdown -->
                    <div class="chart-card">
                        <div class="chart-header">
                            <div>
                                <h4>🧭 6. Domanda di Mercato per Famiglia Professionale</h4>
                                <p>Ripartizione percentuale delle posizioni aperte per macro-settore tecnologico</p>
                            </div>
                            <button class="btn btn-sm" onclick="saveChartAsPNG('chartCategories', 'distribuzione_ruoli_mercato.png')">📸 Salva Grafico</button>
                        </div>
                        <div class="chart-canvas-wrap">
                            <canvas id="chartCategories"></canvas>
                        </div>
                    </div>

                    <!-- Chart 7: Geography & Work Modes -->
                    <div class="chart-card">
                        <div class="chart-header">
                            <div>
                                <h4>📍 7. Distribuzione per Polo Geografico & Modalità</h4>
                                <p>Volume di offerte per città / hub tecnologico e ripartizione In Sede / Ibrido / Remoto</p>
                            </div>
                            <button class="btn btn-sm" onclick="saveChartAsPNG('chartGeo', 'poli_geografici_e_modalita.png')">📸 Salva Grafico</button>
                        </div>
                        <div class="chart-canvas-wrap">
                            <canvas id="chartGeo"></canvas>
                        </div>
                    </div>

                    <!-- Chart 8: Seniority & Hiring Days -->
                    <div class="chart-card">
                        <div class="chart-header">
                            <div>
                                <h4>🎓 8. Livello di Seniority Richiesto dal Mercato</h4>
                                <p>Distribuzione tra Stage/Internship, Junior/Entry-Level, Mid-Level e Senior/Lead</p>
                            </div>
                            <button class="btn btn-sm" onclick="saveChartAsPNG('chartSeniority', 'seniority_mercato.png')">📸 Salva Grafico</button>
                        </div>
                        <div class="chart-canvas-wrap">
                            <canvas id="chartSeniority"></canvas>
                        </div>
                    </div>

                    <!-- Chart 9: Average Salary (RAL) by Role Category -->
                    <div class="chart-card full-width">
                        <div class="chart-header">
                            <div>
                                <h4>💰 9. RAL Media per Categoria di Posizione (€)</h4>
                                <p>Retribuzione Annua Lorda media calcolata per ogni macro-famiglia professionale (benchmark di mercato ponderato e annunci con retribuzione dichiarata)</p>
                            </div>
                            <button class="btn btn-sm" onclick="saveChartAsPNG('chartSalaryCategory', '09_ral_media_per_categoria.png')">📸 Salva Grafico</button>
                        </div>
                        <div class="chart-canvas-wrap" style="height: 360px;">
                            <canvas id="chartSalaryCategory"></canvas>
                        </div>
                    </div>
                </div>

                <!-- Historical Table -->
                <div class="table-card">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;flex-wrap:wrap;gap:10px;">
                        <h3 style="margin:0;font-size:16px;">🗂 Esplorazione Storico Posizioni Filtrate (<span id="tableCountLabel">0</span>)</h3>
                        <div style="display:flex;gap:8px;">
                            <button class="btn btn-sm" onclick="showMoreRows()">➕ Mostra altre righe</button>
                            <button class="btn btn-sm" onclick="exportFilteredCSV()">📥 Scarica Tabella in CSV</button>
                        </div>
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th>Data Pubbl. / Scoperta</th>
                                <th>Azienda & Portale</th>
                                <th>Posizione</th>
                                <th>Categoria & Seniority</th>
                                <th>Sede & Modalità</th>
                                <th>Competenze</th>
                                <th>Match AI</th>
                                <th style="text-align:center;">Candidato?</th>
                                <th>Link Rapido & Azione</th>
                            </tr>
                        </thead>
                        <tbody id="historicalTableBody">
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- VIEW 2: OPERATIONAL QUEUE (HIGH MATCH & AI REASONING) -->
            <div id="viewQueue" class="hidden">
                <div class="table-card">
                    <h3 style="margin:0 0 12px 0;font-size:17px;">🎯 Coda Candidature & Valutazioni Semantiche Gemini AI</h3>
                    <p style="margin:0 0 14px 0;font-size:13px;color:var(--text-muted);">Elenco delle offerte valutate dall'AI ordinate per compatibilità col tuo CV e pronte per l'invio candidatura.</p>
                    <table>
                        <thead>
                            <tr>
                                <th style="width:11%;">Data Pubbl.</th>
                                <th style="width:15%;">Azienda / Portale</th>
                                <th style="width:20%;">Ruolo & Sede</th>
                                <th style="width:10%;">RAL Stimata</th>
                                <th style="width:7%;">Match</th>
                                <th style="width:11%;text-align:center;">Candidato?</th>
                                <th style="width:14%;">Analisi Gemini AI</th>
                                <th style="width:12%;">Link & Azione</th>
                            </tr>
                        </thead>
                        <tbody id="queueTableBody">
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- VIEW 3: SCRAPER DOM INTEGRITY & VISIBILITY DIAGNOSTICS -->
            <div id="viewScraperHealth" class="hidden">
                <div class="kpi-grid" style="margin-bottom:16px;">
                    <div class="kpi-card">
                        <div class="kpi-label">Portali Aziendali Monitorati</div>
                        <div class="kpi-value" id="healthKpiTotal">0</div>
                        <div class="kpi-sub">Siti career &amp; ATS mappati</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">DOM Integro &amp; Offerte in Chiaro</div>
                        <div class="kpi-value" id="healthKpiHealthy" style="color:#10b981;">0</div>
                        <div class="kpi-sub">Selettori verificati e funzionanti</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">DOM Cambiato / Da Aggiornare</div>
                        <div class="kpi-value" id="healthKpiAlerts" style="color:#f43f5e;">0</div>
                        <div class="kpi-sub">Richiedono aggiornamento dello scraper</div>
                    </div>
                    <div class="kpi-card">
                        <div class="kpi-label">0 Offerte coi Filtri Correnti</div>
                        <div class="kpi-value" id="healthKpiWarnings" style="color:#f59e0b;">0</div>
                        <div class="kpi-sub">DOM integro ma nessuna posizione attiva</div>
                    </div>
                </div>

                <div class="table-card">
                    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:14px;">
                        <div>
                            <h3 style="margin:0 0 4px 0;font-size:17px;">🩺 Verifica Integrità DOM, Offerte in Chiaro &amp; Manutenzione Scraper</h3>
                            <p style="margin:0;font-size:13px;color:var(--text-muted);">
                                Rileva automaticamente se una pagina aziendale ha modificato i selettori DOM, se le offerte non sono più in chiaro o se occorre aggiornare lo scraper.
                            </p>
                        </div>
                        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                            <select id="fltHealthStatus" onchange="renderScraperHealthTable()" style="padding:7px 11px;border-radius:8px;border:1px solid var(--border);background:var(--bg-input);color:var(--text-main);font-size:12.5px;">
                                <option value="all">Tutti gli Stati</option>
                                <option value="needs_update">🚨 Solo DOM Cambiato / Da Aggiornare</option>
                                <option value="healthy">✅ Solo DOM Integro (In Chiaro)</option>
                                <option value="warning">⚠️ Solo 0 Offerte coi Filtri</option>
                            </select>
                            <button class="btn btn-primary" id="btnVerifyAllDom" onclick="runDomHealthCheck()">🔍 Verifica DOM di Tutti i Portali Ora</button>
                        </div>
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th style="width:15%;">Azienda</th>
                                <th style="width:10%;">Tipo Scraper</th>
                                <th style="width:17%;">Stato DOM / Offerte in Chiaro</th>
                                <th style="width:11%;">Offerte Estratte</th>
                                <th style="width:28%;">Diagnosi Tecnica &amp; Selettori</th>
                                <th style="width:10%;">Ultima Verifica</th>
                                <th style="width:9%;">Azioni</th>
                            </tr>
                        </thead>
                        <tbody id="scraperHealthTableBody">
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- VIEW 4: CUSTOMIZABLE CANDIDATE PROFILE & PREFERENCES -->
            <div id="viewProfileConfig" class="hidden">
                <div class="table-card">
                    <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:16px;">
                        <div>
                            <h3 style="margin:0 0 4px 0;font-size:17px;">⚙️ Configurazione Profilo Candidato &amp; Preferenze Su Misura</h3>
                            <p style="margin:0;font-size:13px;color:var(--text-muted);">
                                Personalizza liberamente i tuoi dati, il percorso del tuo CV PDF, le posizioni cercate, le città target, le modalità di lavoro e la seniority.
                            </p>
                        </div>
                        <button class="btn btn-primary" onclick="saveProfileConfig()">💾 Salva Configurazione Su Misura</button>
                    </div>
                    <div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(260px, 1fr));gap:14px;">
                        <div class="filter-group">
                            <label>Nome</label>
                            <input type="text" id="profFirstName" placeholder="Il tuo nome">
                        </div>
                        <div class="filter-group">
                            <label>Cognome</label>
                            <input type="text" id="profLastName" placeholder="Il tuo cognome">
                        </div>
                        <div class="filter-group">
                            <label>Email</label>
                            <input type="email" id="profEmail" placeholder="nome.cognome@example.com">
                        </div>
                        <div class="filter-group">
                            <label>Telefono</label>
                            <input type="text" id="profPhone" placeholder="+39 ...">
                        </div>
                        <div class="filter-group">
                            <label>Città Principale</label>
                            <input type="text" id="profCity" placeholder="es. Milano, Torino, Roma...">
                        </div>
                        <div class="filter-group">
                            <label>RAL Minima Desiderata (€)</label>
                            <input type="text" id="profRal" placeholder="es. 30000">
                        </div>
                        <div class="filter-group">
                            <label>URL Profilo LinkedIn</label>
                            <input type="text" id="profLinkedin" placeholder="https://www.linkedin.com/in/...">
                        </div>
                        <div class="filter-group">
                            <label>URL Portfolio / GitHub</label>
                            <input type="text" id="profGithub" placeholder="https://github.com/...">
                        </div>
                        <div class="filter-group">
                            <label>Percorso File CV PDF</label>
                            <input type="text" id="profCvPath" placeholder="data/cv.pdf">
                        </div>
                        <div class="filter-group">
                            <label>Posizioni / Ruoli Cercati (separati da virgola)</label>
                            <input type="text" id="profKeywords" placeholder="AI Engineer, Data Scientist, Software Engineer...">
                        </div>
                        <div class="filter-group">
                            <label>Città / Zone Target (separate da virgola)</label>
                            <input type="text" id="profTargetCities" placeholder="Milano, Torino, Roma, Bologna, Italia...">
                        </div>
                        <div class="filter-group">
                            <label>Modalità Lavoro (remoto, ibrido, in_sede)</label>
                            <input type="text" id="profWorkModes" placeholder="remoto, ibrido, in_sede">
                        </div>
                        <div class="filter-group">
                            <label>Livelli Seniority Accettati (stage, junior, mid, senior)</label>
                            <input type="text" id="profSeniorityTiers" placeholder="stage, junior, mid, senior">
                        </div>
                        <div class="filter-group" style="grid-column: 1 / -1;">
                            <label>Titolo di Studio / Formazione</label>
                            <input type="text" id="profEducation" placeholder="es. Laurea in Informatica / Ingegneria...">
                        </div>
                        <div class="filter-group" style="grid-column: 1 / -1;">
                            <label>Note &amp; Istruzioni Personalizzate per il Valutatore AI</label>
                            <textarea id="profNotes" rows="3" style="padding:8px 10px;border-radius:8px;border:1px solid var(--border);background:var(--bg-input);color:var(--text-main);font-size:13px;font-family:inherit;" placeholder="Descrivi le tue preferenze, tecnologie preferite ed eventuali esclusioni..."></textarea>
                        </div>
                    </div>
                </div>
            </div>
        </main>
    </div>

    <script>
        let RAW_DATA = {{ total_jobs: 0, total_companies: 0, companies: [], jobs: [] }};
        let SELECTED_COMPANIES = new Set();
        let FILTERED_JOBS = [];
        let TABLE_LIMIT = 100;
        const CHARTS = {{}};

        const PALETTE = [
            '#38bdf8', '#10b981', '#a855f7', '#f59e0b', '#f43f5e',
            '#3b82f6', '#14b8a6', '#eab308', '#ec4899', '#6366f1',
            '#22c55e', '#fb923c', '#06b6d4', '#8b5cf6', '#ef4444'
        ];

        // Plugin per sfondo scuro quando si esporta il canvas in PNG
        const darkBgPlugin = {{
            id: 'customCanvasBackgroundColor',
            beforeDraw: (chart) => {{
                const ctx = chart.canvas.getContext('2d');
                ctx.save();
                ctx.globalCompositeOperation = 'destination-over';
                ctx.fillStyle = '#131c31';
                ctx.fillRect(0, 0, chart.width, chart.height);
                ctx.restore();
            }}
        }};
        Chart.register(darkBgPlugin);
        Chart.defaults.color = '#cbd5e1';
        Chart.defaults.borderColor = 'rgba(148, 163, 184, 0.14)';
        Chart.defaults.font.family = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";

        async function loadAnalyticsData() {{
            const res = await fetch('/api/analytics');
            RAW_DATA = await res.json();

            // Ripristina aziende salvate in localStorage se presenti, altrimenti seleziona tutte
            const saved = localStorage.getItem('job_hunter_selected_companies');
            if (saved) {{
                try {{
                    const arr = JSON.parse(saved);
                    const validNames = new Set(RAW_DATA.companies.map(c => c.name));
                    const filtered = arr.filter(x => validNames.has(x));
                    if (filtered.length > 0) {{
                        SELECTED_COMPANIES = new Set(filtered);
                    }} else {{
                        RAW_DATA.companies.forEach(c => SELECTED_COMPANIES.add(c.name));
                    }}
                }} catch (e) {{
                    RAW_DATA.companies.forEach(c => SELECTED_COMPANIES.add(c.name));
                }}
            }} else {{
                RAW_DATA.companies.forEach(c => SELECTED_COMPANIES.add(c.name));
            }}

            renderCompanyList();
            applyFiltersAndRender();
        }}

        function renderCompanyList() {{
            const container = document.getElementById('companyListContainer');
            const q = (document.getElementById('companySearchInput').value || '').toLowerCase().trim();
            document.getElementById('selectedCompCount').innerText = SELECTED_COMPANIES.size;
            document.getElementById('totalCompCount').innerText = RAW_DATA.companies.length;

            let html = '';
            for (const c of RAW_DATA.companies) {{
                if (q && !c.name.toLowerCase().includes(q)) continue;
                const checked = SELECTED_COMPANIES.has(c.name) ? 'checked' : '';
                const safeName = c.name.replace(/"/g, '&quot;');
                const matchBadge = c.matched > 0 ? `<span class="badge-match" title="Offerte in target col CV">🎯 ${{c.matched}}</span>` : '';
                html += `
                    <div class="company-item" onclick="toggleCompany('${{safeName.replace(/'/g, "\\\\'")}}')">
                        <label onclick="event.stopPropagation()">
                            <input type="checkbox" ${{checked}} onchange="toggleCompany('${{safeName.replace(/'/g, "\\\\'")}}')">
                            <span title="${{safeName}}">${{c.name}}</span>
                        </label>
                        <div class="company-badges">
                            ${{matchBadge}}
                            <span class="badge-count">${{c.total}}</span>
                        </div>
                    </div>
                `;
            }}
            container.innerHTML = html || '<div style="color:#64748b;font-size:12px;padding:10px;">Nessuna azienda trovata.</div>';
        }}

        function toggleCompany(name) {{
            if (SELECTED_COMPANIES.has(name)) {{
                SELECTED_COMPANIES.delete(name);
            }} else {{
                SELECTED_COMPANIES.add(name);
            }}
            renderCompanyList();
            applyFiltersAndRender();
        }}

        function selectAllCompanies(flag) {{
            if (flag) {{
                RAW_DATA.companies.forEach(c => SELECTED_COMPANIES.add(c.name));
            }} else {{
                SELECTED_COMPANIES.clear();
            }}
            renderCompanyList();
            applyFiltersAndRender();
        }}

        function selectTopCompanies(n) {{
            SELECTED_COMPANIES.clear();
            RAW_DATA.companies.slice(0, n).forEach(c => SELECTED_COMPANIES.add(c.name));
            renderCompanyList();
            applyFiltersAndRender();
        }}

        function selectMatchedCompanies() {{
            SELECTED_COMPANIES.clear();
            RAW_DATA.companies.filter(c => c.matched > 0).forEach(c => SELECTED_COMPANIES.add(c.name));
            renderCompanyList();
            applyFiltersAndRender();
        }}

        function invertCompanySelection() {{
            const next = new Set();
            for (const c of RAW_DATA.companies) {{
                if (!SELECTED_COMPANIES.has(c.name)) next.add(c.name);
            }}
            SELECTED_COMPANIES = next;
            renderCompanyList();
            applyFiltersAndRender();
        }}

        function saveCompanyPreset() {{
            localStorage.setItem('job_hunter_selected_companies', JSON.stringify(Array.from(SELECTED_COMPANIES)));
            alert('Selezione di ' + SELECTED_COMPANIES.size + ' aziende salvata come predefinita!');
        }}

        function loadCompanyPreset() {{
            const saved = localStorage.getItem('job_hunter_selected_companies');
            if (!saved) {{
                alert('Nessun preset salvato trovato.');
                return;
            }}
            SELECTED_COMPANIES = new Set(JSON.parse(saved));
            renderCompanyList();
            applyFiltersAndRender();
        }}

        function onPeriodPresetChange() {{
            const val = document.getElementById('fltPeriodDays').value;
            const fromEl = document.getElementById('fltDateFrom');
            const toEl = document.getElementById('fltDateTo');
            if (val === 'all') {{
                fromEl.value = '';
                toEl.value = '';
            }} else {{
                const days = parseInt(val, 10);
                const now = new Date();
                const past = new Date(now.getTime() - days * 86400000);
                fromEl.value = past.toISOString().slice(0, 10);
                toEl.value = now.toISOString().slice(0, 10);
            }}
            applyFiltersAndRender();
        }}

        function setSeniorityFilter(val) {{
            document.getElementById('fltSeniority').value = val;
            updateSeniorityPillsUI(val);
            applyFiltersAndRender();
        }}

        function onSeniorityChange() {{
            const val = document.getElementById('fltSeniority').value;
            updateSeniorityPillsUI(val);
            applyFiltersAndRender();
        }}

        function updateSeniorityPillsUI(val) {{
            const map = {{
                'all': 'pillSen_all',
                'Stage / Internship': 'pillSen_stage',
                'Junior / Entry Level': 'pillSen_junior',
                'Mid-Level / Regular': 'pillSen_mid',
                'Senior / Lead / Manager': 'pillSen_senior'
            }};
            Object.values(map).forEach(id => {{
                const el = document.getElementById(id);
                if (el) el.classList.remove('active');
            }});
            const activeId = map[val] || 'pillSen_all';
            const activeEl = document.getElementById(activeId);
            if (activeEl) activeEl.classList.add('active');
        }}

        function isJobApplied(j) {{
            if (j.is_applied === true) return true;
            const st = (j.status || '').toUpperCase();
            return st === 'APPLIED' || st === 'MANUAL_APPLIED' || st === 'AUTO_APPLIED' || st === 'APPROVED_APPLIED' || Boolean(j.applied_at);
        }}

        async function toggleApplication(jobId, isChecked) {{
            const j = RAW_DATA.jobs.find(x => x.id === jobId);
            if (j) {{
                j.is_applied = isChecked;
                j.status = isChecked ? 'MANUAL_APPLIED' : 'DISCOVERED';
                j.applied_at = isChecked ? new Date().toISOString() : null;
            }}
            try {{
                await fetch(`/api/jobs/${{jobId}}/toggle-applied`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ applied: isChecked }})
                }});
            }} catch (e) {{
                console.error('Errore salvataggio application:', e);
            }}
            const curApp = document.getElementById('fltApplication') ? document.getElementById('fltApplication').value : 'all';
            if (curApp !== 'all') {{
                applyFiltersAndRender();
            }} else {{
                updateKPIsAndPulse();
                renderHistoricalTable();
                renderQueueTable();
            }}
        }}

        function setApplicationFilter(val) {{
            const selectEl = document.getElementById('fltApplication');
            if (selectEl) selectEl.value = val;
            updateApplicationPillsUI(val);
            applyFiltersAndRender();
        }}

        function onApplicationFilterChange() {{
            const selectEl = document.getElementById('fltApplication');
            const val = selectEl ? selectEl.value : 'all';
            updateApplicationPillsUI(val);
            applyFiltersAndRender();
        }}

        function updateApplicationPillsUI(val) {{
            const map = {{
                'all': 'pillApp_all',
                'applied': 'pillApp_applied',
                'not_applied': 'pillApp_not'
            }};
            Object.values(map).forEach(id => {{
                const el = document.getElementById(id);
                if (el) el.classList.remove('active');
            }});
            const activeId = map[val] || 'pillApp_all';
            const activeEl = document.getElementById(activeId);
            if (activeEl) activeEl.classList.add('active');
        }}

        function resetAllFilters() {{
            document.getElementById('fltDateField').value = 'published_at';
            document.getElementById('fltTimeGranularity').value = 'day';
            document.getElementById('fltPeriodDays').value = 'all';
            document.getElementById('fltDateFrom').value = '';
            document.getElementById('fltDateTo').value = '';
            document.getElementById('fltCategory').value = 'all';
            document.getElementById('fltCity').value = 'all';
            document.getElementById('fltWorkMode').value = 'all';
            document.getElementById('fltSeniority').value = 'all';
            updateSeniorityPillsUI('all');
            if (document.getElementById('fltApplication')) {{
                document.getElementById('fltApplication').value = 'all';
                updateApplicationPillsUI('all');
            }}
            document.getElementById('fltScope').value = 'all';
            document.getElementById('fltSearchText').value = '';
            selectAllCompanies(true);
        }}

        function applyFiltersAndRender() {{
            const dateField = document.getElementById('fltDateField').value;
            const dateFrom = document.getElementById('fltDateFrom').value;
            const dateTo = document.getElementById('fltDateTo').value;
            const category = document.getElementById('fltCategory').value;
            const city = document.getElementById('fltCity').value;
            const workMode = document.getElementById('fltWorkMode').value;
            const seniority = document.getElementById('fltSeniority').value;
            const appFilter = document.getElementById('fltApplication') ? document.getElementById('fltApplication').value : 'all';
            const scope = document.getElementById('fltScope').value;
            const searchQ = (document.getElementById('fltSearchText').value || '').toLowerCase().trim();

            FILTERED_JOBS = RAW_DATA.jobs.filter(j => {{
                if (!SELECTED_COMPANIES.has(j.company)) return false;
                const dVal = j[dateField] || j.published_at || j.discovered_at || '';
                if (dateFrom && dVal && dVal < dateFrom) return false;
                if (dateTo && dVal && dVal > dateTo) return false;
                if (category !== 'all' && j.role_category !== category) return false;
                if (city !== 'all' && j.city_hub !== city) return false;
                if (workMode !== 'all' && j.work_mode !== workMode) return false;
                if (seniority !== 'all' && j.seniority !== seniority) return false;
                const applied = isJobApplied(j);
                if (appFilter === 'applied' && !applied) return false;
                if (appFilter === 'not_applied' && applied) return false;
                if (scope === 'matched' && j.match_score < 60) return false;
                if (scope === 'high' && j.match_score < {review_thresh}) return false;
                if (searchQ) {{
                    const hay = `${{j.title}} ${{j.company}} ${{j.location}} ${{(j.skills || []).join(' ')}}`.toLowerCase();
                    if (!hay.includes(searchQ)) return false;
                }}
                return true;
            }});

            TABLE_LIMIT = 100;
            updateKPIsAndPulse();
            renderAllCharts();
            renderHistoricalTable();
            renderQueueTable();
        }}

        function countBy(arr, keyFn) {{
            const map = new Map();
            for (const item of arr) {{
                const k = keyFn(item);
                if (!k) continue;
                map.set(k, (map.get(k) || 0) + 1);
            }}
            return Array.from(map.entries()).sort((a, b) => b[1] - a[1]);
        }}

        function bucketDate(dateStr, granularity) {{
            if (!dateStr || dateStr.length < 10) return 'N/D';
            if (granularity === 'month') return dateStr.slice(0, 7);
            if (granularity === 'week') {{
                const d = new Date(dateStr + 'T00:00:00');
                if (isNaN(d)) return dateStr;
                const day = d.getDay() || 7;
                d.setDate(d.getDate() - day + 1);
                return d.toISOString().slice(0, 10);
            }}
            return dateStr.slice(0, 10);
        }}

        function updateKPIsAndPulse() {{
            const total = FILTERED_JOBS.length;
            document.getElementById('kpiTotalJobs').innerText = total;
            document.getElementById('kpiTotalSub').innerText = `su ${{RAW_DATA.total_jobs}} totali a database`;

            // Aggiorna conteggi pills seniority basati sulle aziende selezionate
            const compJobs = RAW_DATA.jobs ? RAW_DATA.jobs.filter(j => SELECTED_COMPANIES.has(j.company)) : [];
            const cAll = compJobs.length;
            const cStage = compJobs.filter(j => j.seniority === 'Stage / Internship').length;
            const cJunior = compJobs.filter(j => j.seniority === 'Junior / Entry Level').length;
            const cMid = compJobs.filter(j => j.seniority === 'Mid-Level / Regular').length;
            const cSenior = compJobs.filter(j => j.seniority === 'Senior / Lead / Manager').length;

            const elAll = document.getElementById('cntSen_all');
            const elStage = document.getElementById('cntSen_stage');
            const elJr = document.getElementById('cntSen_junior');
            const elMid = document.getElementById('cntSen_mid');
            const elSr = document.getElementById('cntSen_senior');
            if (elAll) elAll.innerText = cAll;
            if (elStage) elStage.innerText = cStage;
            if (elJr) elJr.innerText = cJunior;
            if (elMid) elMid.innerText = cMid;
            if (elSr) elSr.innerText = cSenior;

            // Aggiorna conteggi pills application basati sulle aziende selezionate
            const appliedCount = compJobs.filter(j => isJobApplied(j)).length;
            const notAppliedCount = compJobs.length - appliedCount;
            const elAppApplied = document.getElementById('cntApp_applied');
            const elAppNot = document.getElementById('cntApp_not');
            if (elAppApplied) elAppApplied.innerText = appliedCount;
            if (elAppNot) elAppNot.innerText = notAppliedCount;

            const activeComps = countBy(FILTERED_JOBS, j => j.company);
            document.getElementById('kpiActiveCompanies').innerText = activeComps.length;
            document.getElementById('kpiTopCompanySub').innerText = activeComps.length > 0
                ? `Top: ${{activeComps[0][0]}} (${{activeComps[0][1]}} offerte)`
                : 'Nessuna azienda selezionata';

            const cats = countBy(FILTERED_JOBS, j => j.role_category);
            if (cats.length > 0) {{
                const pct = Math.round((cats[0][1] / total) * 100);
                document.getElementById('kpiTopCategory').innerText = cats[0][0];
                document.getElementById('kpiTopCategorySub').innerText = `${{cats[0][1]}} posizioni (${{pct}}% del mercato filtrato)`;
            }} else {{
                document.getElementById('kpiTopCategory').innerText = '-';
                document.getElementById('kpiTopCategorySub').innerText = '-';
            }}

            const skillMap = new Map();
            FILTERED_JOBS.forEach(j => (j.skills || []).forEach(s => skillMap.set(s, (skillMap.get(s) || 0) + 1)));
            const topSkills = Array.from(skillMap.entries()).sort((a, b) => b[1] - a[1]);
            if (topSkills.length > 0) {{
                const pct = Math.round((topSkills[0][1] / Math.max(total, 1)) * 100);
                document.getElementById('kpiTopSkill').innerText = topSkills[0][0];
                document.getElementById('kpiTopSkillSub').innerText = `Richiesta in ${{topSkills[0][1]}} annunci (${{pct}}%)`;
            }} else {{
                document.getElementById('kpiTopSkill').innerText = '-';
                document.getElementById('kpiTopSkillSub').innerText = '-';
            }}

            const remCount = FILTERED_JOBS.filter(j => j.work_mode === 'Remoto').length;
            const hybCount = FILTERED_JOBS.filter(j => j.work_mode === 'Ibrido').length;
            const flexPct = total > 0 ? Math.round(((remCount + hybCount) / total) * 100) : 0;
            document.getElementById('kpiFlexShare').innerText = `${{flexPct}}%`;
            document.getElementById('kpiFlexSub').innerText = `${{remCount}} Full Remote • ${{hybCount}} Ibride`;

            const matchedJobs = FILTERED_JOBS.filter(j => j.match_score >= 60);
            const highJobs = FILTERED_JOBS.filter(j => j.match_score >= {review_thresh});
            document.getElementById('kpiMatchedCount').innerText = matchedJobs.length;
            document.getElementById('kpiMatchedSub').innerText = `${{highJobs.length}} con fedeltà alta (&ge;{review_thresh}%)`;

            // Calcolo RAL Media su posizioni filtrate
            const rals = FILTERED_JOBS.filter(j => (j.ral_numeric || 0) > 0).map(j => j.ral_numeric);
            const avgSalary = rals.length > 0 ? Math.round(rals.reduce((a, b) => a + b, 0) / rals.length) : 0;
            document.getElementById('kpiAvgSalary').innerText = avgSalary > 0
                ? `${{avgSalary.toLocaleString('it-IT')}} €`
                : 'N/D';
            const explicitJobs = FILTERED_JOBS.filter(j => j.is_explicit_ral);
            const expAvg = explicitJobs.length > 0
                ? Math.round(explicitJobs.reduce((a, b) => a + b.ral_numeric, 0) / explicitJobs.length)
                : 0;
            document.getElementById('kpiAvgSalarySub').innerText = explicitJobs.length > 0
                ? `${{explicitJobs.length}} offerte con cifra (${{expAvg.toLocaleString('it-IT')}} €)`
                : 'Benchmark di mercato ponderato';

            // Market Pulse Insights
            const top3Comps = activeComps.slice(0, 3).map(x => `<strong>${{x[0]}}</strong> (${{x[1]}})`).join(', ') || 'N/D';
            const top5Skills = topSkills.slice(0, 5).map(x => `<strong>${{x[0]}}</strong> (${{x[1]}})`).join(', ') || 'N/D';

            const missingMap = new Map();
            FILTERED_JOBS.forEach(j => (j.missing_skills || []).forEach(m => missingMap.set(m, (missingMap.get(m) || 0) + 1)));
            const topMissing = Array.from(missingMap.entries()).sort((a, b) => b[1] - a[1]).slice(0, 4).map(x => `<strong>${{x[0]}}</strong> (${{x[1]}})`).join(', ') || 'Nessun gap critico rilevato';

            const cities = countBy(FILTERED_JOBS, j => j.city_hub).slice(0, 3).map(x => `<strong>${{x[0]}}</strong> (${{x[1]}})`).join(', ') || 'N/D';

            document.getElementById('marketPulseContainer').innerHTML = `
                <div class="pulse-item">🏢 <strong>Aziende Leader per Assunzioni:</strong><br>${{top3Comps}}</div>
                <div class="pulse-item">🛠 <strong>Stack Tecnologico Più Domandato:</strong><br>${{top5Skills}}</div>
                <div class="pulse-item">🎯 <strong>Skill Gap Prioritario (Alto ROI):</strong><br>${{topMissing}}</div>
                <div class="pulse-item">📍 <strong>Poli Geografici Più Attivi:</strong><br>${{cities}}</div>
                <div class="pulse-item">💰 <strong>Livello Salariale Medio (Filtro):</strong><br><strong>${{avgSalary > 0 ? avgSalary.toLocaleString('it-IT') + ' €' : 'N/D'}}</strong> lordi/anno ${{explicitJobs.length > 0 ? `(Media annunci espliciti: ${{expAvg.toLocaleString('it-IT')}} €)` : ''}}</div>
            `;
        }}

        function destroyChart(id) {{
            if (CHARTS[id]) {{
                CHARTS[id].destroy();
                delete CHARTS[id];
            }}
        }}

        function renderAllCharts() {{
            const dateField = document.getElementById('fltDateField').value;
            const gran = document.getElementById('fltTimeGranularity').value;

            // 1. Timeline Chart (Total vs Matched + 3-period Moving Average)
            const timeMapTotal = new Map();
            const timeMapMatched = new Map();
            for (const j of FILTERED_JOBS) {{
                const rawD = j[dateField] || j.published_at || j.discovered_at;
                const b = bucketDate(rawD, gran);
                if (b === 'N/D') continue;
                timeMapTotal.set(b, (timeMapTotal.get(b) || 0) + 1);
                if (j.match_score >= 60) {{
                    timeMapMatched.set(b, (timeMapMatched.get(b) || 0) + 1);
                }}
            }}
            const sortedDates = Array.from(timeMapTotal.keys()).sort();
            const seriesTotal = sortedDates.map(d => timeMapTotal.get(d) || 0);
            const seriesMatched = sortedDates.map(d => timeMapMatched.get(d) || 0);
            const seriesMA = seriesTotal.map((_, idx, arr) => {{
                const start = Math.max(0, idx - 2);
                const slice = arr.slice(start, idx + 1);
                return +(slice.reduce((a, b) => a + b, 0) / slice.length).toFixed(1);
            }});

            destroyChart('chartTimeline');
            CHARTS['chartTimeline'] = new Chart(document.getElementById('chartTimeline'), {{
                type: 'line',
                data: {{
                    labels: sortedDates,
                    datasets: [
                        {{
                            label: 'Posizioni Pubblicate / Rilevate',
                            data: seriesTotal,
                            borderColor: '#38bdf8',
                            backgroundColor: 'rgba(56, 189, 248, 0.16)',
                            fill: true,
                            tension: 0.3,
                            pointRadius: 3
                        }},
                        {{
                            label: 'Media Mobile Trend (3 periodi)',
                            data: seriesMA,
                            borderColor: '#f59e0b',
                            borderDash: [6, 4],
                            fill: false,
                            tension: 0.35,
                            pointRadius: 0
                        }},
                        {{
                            label: 'Posizioni In Target col CV (>=60%)',
                            data: seriesMatched,
                            borderColor: '#10b981',
                            backgroundColor: 'rgba(16, 185, 129, 0.2)',
                            fill: true,
                            tension: 0.3,
                            pointRadius: 3
                        }}
                    ]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {{ mode: 'index', intersect: false }},
                    plugins: {{ legend: {{ position: 'top' }} }},
                    scales: {{ y: {{ beginAtZero: true }} }}
                }}
            }});

            // 2. Historical Trend by Top 6 Selected Companies
            const top6Comps = countBy(FILTERED_JOBS, j => j.company).slice(0, 6).map(x => x[0]);
            const compDatasets = top6Comps.map((compName, idx) => {{
                const cMap = new Map();
                FILTERED_JOBS.filter(j => j.company === compName).forEach(j => {{
                    const b = bucketDate(j[dateField] || j.published_at || j.discovered_at, gran);
                    if (b !== 'N/D') cMap.set(b, (cMap.get(b) || 0) + 1);
                }});
                return {{
                    label: compName,
                    data: sortedDates.map(d => cMap.get(d) || 0),
                    borderColor: PALETTE[idx % PALETTE.length],
                    backgroundColor: PALETTE[idx % PALETTE.length] + '99',
                    borderWidth: 2,
                    tension: 0.25
                }};
            }});

            destroyChart('chartCompanyTimeline');
            CHARTS['chartCompanyTimeline'] = new Chart(document.getElementById('chartCompanyTimeline'), {{
                type: sortedDates.length > 12 ? 'line' : 'bar',
                data: {{
                    labels: sortedDates,
                    datasets: compDatasets
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {{ mode: 'index', intersect: false }},
                    plugins: {{ legend: {{ position: 'top' }} }},
                    scales: {{
                        x: {{ stacked: sortedDates.length <= 12 }},
                        y: {{ stacked: sortedDates.length <= 12, beginAtZero: true }}
                    }}
                }}
            }});

            // 3. Top 15 Companies Horizontal Bar Chart
            const top15Comps = countBy(FILTERED_JOBS, j => j.company).slice(0, 15);
            const compLabels = top15Comps.map(x => x[0]);
            const compTotals = top15Comps.map(x => x[1]);
            const compMatched = compLabels.map(cName => FILTERED_JOBS.filter(j => j.company === cName && j.match_score >= 60).length);

            destroyChart('chartCompanies');
            CHARTS['chartCompanies'] = new Chart(document.getElementById('chartCompanies'), {{
                type: 'bar',
                data: {{
                    labels: compLabels,
                    datasets: [
                        {{
                            label: 'Totale Posizioni Aperte',
                            data: compTotals,
                            backgroundColor: '#3b82f6',
                            borderRadius: 5
                        }},
                        {{
                            label: 'Compatibili col Profilo (>=60%)',
                            data: compMatched,
                            backgroundColor: '#10b981',
                            borderRadius: 5
                        }}
                    ]
                }},
                options: {{
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ position: 'top' }} }},
                    scales: {{ x: {{ beginAtZero: true }} }}
                }}
            }});

            // 4. Hard Skills Demand (Top 16)
            const skillCounts = new Map();
            FILTERED_JOBS.forEach(j => (j.skills || []).forEach(s => skillCounts.set(s, (skillCounts.get(s) || 0) + 1)));
            const topSkills = Array.from(skillCounts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 16);

            destroyChart('chartSkills');
            CHARTS['chartSkills'] = new Chart(document.getElementById('chartSkills'), {{
                type: 'bar',
                data: {{
                    labels: topSkills.map(x => x[0]),
                    datasets: [{{
                        label: 'Annunci che richiedono la competenza',
                        data: topSkills.map(x => x[1]),
                        backgroundColor: topSkills.map((_, i) => PALETTE[i % PALETTE.length]),
                        borderRadius: 5
                    }}]
                }},
                options: {{
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{ x: {{ beginAtZero: true }} }}
                }}
            }});

            // 5. Skill Gap Analysis (Missing skills flagged by AI)
            const gapCounts = new Map();
            FILTERED_JOBS.forEach(j => (j.missing_skills || []).forEach(m => {{
                const key = m.trim();
                if (key) gapCounts.set(key, (gapCounts.get(key) || 0) + 1);
            }}));
            const topGaps = Array.from(gapCounts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 12);

            destroyChart('chartSkillGap');
            CHARTS['chartSkillGap'] = new Chart(document.getElementById('chartSkillGap'), {{
                type: 'bar',
                data: {{
                    labels: topGaps.length ? topGaps.map(x => x[0]) : ['Nessun gap registrato nella selezione'],
                    datasets: [{{
                        label: 'Frequenza nei requisiti mancanti',
                        data: topGaps.length ? topGaps.map(x => x[1]) : [0],
                        backgroundColor: '#f59e0b',
                        borderRadius: 5
                    }}]
                }},
                options: {{
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{ x: {{ beginAtZero: true }} }}
                }}
            }});

            // 6. Role Categories Doughnut
            const catCounts = countBy(FILTERED_JOBS, j => j.role_category);
            destroyChart('chartCategories');
            CHARTS['chartCategories'] = new Chart(document.getElementById('chartCategories'), {{
                type: 'doughnut',
                data: {{
                    labels: catCounts.map(x => x[0]),
                    datasets: [{{
                        data: catCounts.map(x => x[1]),
                        backgroundColor: PALETTE,
                        borderColor: '#131c31',
                        borderWidth: 2
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ position: 'right' }} }}
                }}
            }});

            // 7. Geography & Work Mode Stacked Bar
            const topCities = countBy(FILTERED_JOBS, j => j.city_hub).slice(0, 9).map(x => x[0]);
            const modes = ['In Sede', 'Ibrido', 'Remoto'];
            const modeColors = {{ 'In Sede': '#3b82f6', 'Ibrido': '#a855f7', 'Remoto': '#10b981' }};
            const geoDatasets = modes.map(m => ({{
                label: m,
                data: topCities.map(c => FILTERED_JOBS.filter(j => j.city_hub === c && j.work_mode === m).length),
                backgroundColor: modeColors[m],
                borderRadius: 4
            }}));

            destroyChart('chartGeo');
            CHARTS['chartGeo'] = new Chart(document.getElementById('chartGeo'), {{
                type: 'bar',
                data: {{
                    labels: topCities,
                    datasets: geoDatasets
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ position: 'top' }} }},
                    scales: {{
                        x: {{ stacked: true }},
                        y: {{ stacked: true, beginAtZero: true }}
                    }}
                }}
            }});

            // 8. Seniority Distribution
            const senOrder = ['Stage / Internship', 'Junior / Entry Level', 'Mid-Level / Regular', 'Senior / Lead / Manager'];
            const senData = senOrder.map(s => FILTERED_JOBS.filter(j => j.seniority === s).length);

            destroyChart('chartSeniority');
            CHARTS['chartSeniority'] = new Chart(document.getElementById('chartSeniority'), {{
                type: 'bar',
                data: {{
                    labels: senOrder,
                    datasets: [{{
                        label: 'Numero di Posizioni',
                        data: senData,
                        backgroundColor: ['#38bdf8', '#10b981', '#a855f7', '#f43f5e'],
                        borderRadius: 6
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    onClick: (evt, elements) => {{
                        if (elements && elements.length > 0) {{
                            const idx = elements[0].index;
                            const clickedSen = senOrder[idx];
                            const cur = document.getElementById('fltSeniority').value;
                            setSeniorityFilter(cur === clickedSen ? 'all' : clickedSen);
                        }}
                    }},
                    plugins: {{
                        legend: {{ display: false }},
                        tooltip: {{
                            callbacks: {{
                                footer: () => '👉 Clicca sulla barra per filtrare la dashboard su questo livello'
                            }}
                        }}
                    }},
                    scales: {{ y: {{ beginAtZero: true }} }}
                }}
            }});

            // 9. Average Salary (RAL) by Role Category
            const catMap = new Map();
            for (const j of FILTERED_JOBS) {{
                const cat = j.role_category || 'Altro';
                if (!catMap.has(cat)) {{
                    catMap.set(cat, {{ totalRal: 0, count: 0, explicitRal: 0, explicitCount: 0, minRal: 999999, maxRal: 0 }});
                }}
                const stats = catMap.get(cat);
                const r = j.ral_numeric || 0;
                if (r > 0) {{
                    stats.totalRal += r;
                    stats.count += 1;
                    if (j.ral_min && j.ral_min < stats.minRal) stats.minRal = j.ral_min;
                    if (j.ral_max && j.ral_max > stats.maxRal) stats.maxRal = j.ral_max;
                    if (j.is_explicit_ral) {{
                        stats.explicitRal += r;
                        stats.explicitCount += 1;
                    }}
                }}
            }}

            const sortedSalaryCats = Array.from(catMap.entries())
                .filter(x => x[1].count > 0)
                .map(([cat, stats]) => ({{
                    category: cat,
                    avg: Math.round(stats.totalRal / stats.count),
                    count: stats.count,
                    min: stats.minRal === 999999 ? 0 : stats.minRal,
                    max: stats.maxRal,
                    expAvg: stats.explicitCount > 0 ? Math.round(stats.explicitRal / stats.explicitCount) : null,
                    expCount: stats.explicitCount
                }}))
                .sort((a, b) => b.avg - a.avg);

            const catLabels = sortedSalaryCats.map(x => x.category);
            const mktSalaries = sortedSalaryCats.map(x => x.avg);
            const expSalaries = sortedSalaryCats.map(x => x.expAvg);

            destroyChart('chartSalaryCategory');
            CHARTS['chartSalaryCategory'] = new Chart(document.getElementById('chartSalaryCategory'), {{
                type: 'bar',
                data: {{
                    labels: catLabels,
                    datasets: [
                        {{
                            label: 'RAL Media Mercato (€)',
                            data: mktSalaries,
                            backgroundColor: '#38bdf8',
                            borderRadius: 6,
                            maxBarThickness: 28
                        }},
                        {{
                            label: 'Media Offerte con RAL Esplicita (€)',
                            data: expSalaries,
                            backgroundColor: '#10b981',
                            borderRadius: 6,
                            maxBarThickness: 28
                        }}
                    ]
                }},
                options: {{
                    indexAxis: 'y',
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{
                        legend: {{ position: 'top' }},
                        tooltip: {{
                            callbacks: {{
                                label: function(context) {{
                                    const val = context.raw;
                                    if (val === null || val === undefined) return context.dataset.label + ': Nessun annuncio con RAL esplicita';
                                    const idx = context.dataIndex;
                                    const info = sortedSalaryCats[idx];
                                    if (context.datasetIndex === 0) {{
                                        return `RAL Media: ${{val.toLocaleString('it-IT')}} € (su ${{info.count}} posizioni, Range: ${{info.min.toLocaleString('it-IT')}}€ - ${{info.max.toLocaleString('it-IT')}}€)`;
                                    }} else {{
                                        return `Media Esplicita: ${{val.toLocaleString('it-IT')}} € (su ${{info.expCount}} annunci con retribuzione)`;
                                    }}
                                }}
                            }}
                        }}
                    }},
                    scales: {{
                        x: {{
                            beginAtZero: true,
                            ticks: {{
                                callback: function(value) {{
                                    return (value / 1000) + 'k €';
                                }}
                            }}
                        }}
                    }}
                }}
            }});
        }}

        function renderHistoricalTable() {{
            const tbody = document.getElementById('historicalTableBody');
            document.getElementById('tableCountLabel').innerText = `${{Math.min(FILTERED_JOBS.length, TABLE_LIMIT)}} di ${{FILTERED_JOBS.length}}`;

            let html = '';
            const slice = FILTERED_JOBS.slice(0, TABLE_LIMIT);
            for (const j of slice) {{
                const score = j.match_score || 0;
                const badgeColor = score >= {auto_thresh} ? '#10b981' : (score >= {review_thresh} ? '#f59e0b' : '#475569');
                const skillsHtml = (j.skills || []).slice(0, 6).map(s => `<span class="skill-pill">${{s}}</span>`).join('');
                const missingHtml = (j.missing_skills || []).slice(0, 3).map(s => `<span class="missing-pill" title="Skill mancante">⚠️ ${{s}}</span>`).join('');
                const applied = isJobApplied(j);
                const appliedBadge = `
                    <label class="app-toggle-label" title="${{applied ? 'Candidatura già inviata! Clicca per deselezionare' : 'Clicca per segnare come Candidata'}}">
                        <input type="checkbox" class="app-toggle-checkbox" ${{applied ? 'checked' : ''}} onchange="toggleApplication(${{j.id}}, this.checked)">
                        <span class="app-status-badge ${{applied ? 'is-applied' : 'is-not-applied'}}">
                            ${{applied ? '✅ Inviata' : '⬜ Da fare'}}
                        </span>
                    </label>
                `;
                const quickLinkBtn = `
                    <a href="${{j.url}}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-quick-link" title="Apri subito la pagina dell'annuncio">
                        🔗 Apri Offerta
                    </a>
                `;
                const actionBtn = score >= 50
                    ? `<button class="btn btn-sm btn-blue" onclick="applyJob(${{j.id}})">Invia CV</button>`
                    : '';

                html += `
                    <tr style="${{applied ? 'background: rgba(16, 185, 129, 0.04);' : ''}}">
                        <td style="white-space:nowrap;">
                            <strong style="color:#38bdf8;">📅 ${{j.published_at || 'N/D'}}</strong><br>
                            <small style="color:#64748b;">Scoperta: ${{j.discovered_at || 'N/D'}}</small>
                        </td>
                        <td>
                            <strong>${{j.company}}</strong><br>
                            <small style="color:#94a3b8;">${{j.portal}}</small>
                        </td>
                        <td>
                            <a href="${{j.url}}" target="_blank" rel="noopener noreferrer" style="color:#f8fafc;text-decoration:none;font-weight:600;" title="Clicca per aprire">${{j.title}}</a>
                        </td>
                        <td>
                            <span class="cat-badge">${{j.role_category}}</span><br>
                            <small style="color:#94a3b8;cursor:pointer;display:inline-block;padding:1px 5px;border-radius:4px;background:rgba(255,255,255,0.05);margin-top:2px;" onclick="setSeniorityFilter('${{j.seniority}}')" title="Filtra la dashboard per ${{j.seniority}}">🎓 ${{j.seniority}}</small>
                        </td>
                        <td>
                            <strong>${{j.city_hub}}</strong><br>
                            <small style="color:#a855f7;">${{j.work_mode}}</small>
                        </td>
                        <td>${{skillsHtml}} ${{missingHtml}}</td>
                        <td><span style="background:${{badgeColor}};color:#fff;padding:3px 8px;border-radius:10px;font-weight:700;font-size:12px;">${{score}}%</span></td>
                        <td style="text-align:center;">${{appliedBadge}}</td>
                        <td style="white-space:nowrap;">
                            <div style="display:flex;gap:6px;align-items:center;">
                                ${{quickLinkBtn}}
                                ${{actionBtn}}
                            </div>
                        </td>
                    </tr>
                `;
            }}
            tbody.innerHTML = html || '<tr><td colspan="9" style="text-align:center;padding:30px;color:#64748b;">Nessuna posizione corrisponde ai filtri selezionati.</td></tr>';
        }}

        function renderQueueTable() {{
            const tbody = document.getElementById('queueTableBody');
            const sortedByScore = [...FILTERED_JOBS].sort((a, b) => (b.match_score || 0) - (a.match_score || 0)).slice(0, 120);
            let html = '';
            for (const j of sortedByScore) {{
                const score = j.match_score || 0;
                const badgeColor = score >= {auto_thresh} ? '#10b981' : (score >= {review_thresh} ? '#f59e0b' : '#475569');
                const applied = isJobApplied(j);
                const appliedBadge = `
                    <label class="app-toggle-label" title="${{applied ? 'Candidatura inviata! Clicca per deselezionare' : 'Clicca per segnare come Candidata'}}">
                        <input type="checkbox" class="app-toggle-checkbox" ${{applied ? 'checked' : ''}} onchange="toggleApplication(${{j.id}}, this.checked)">
                        <span class="app-status-badge ${{applied ? 'is-applied' : 'is-not-applied'}}">
                            ${{applied ? '✅ Inviata' : '⬜ Da fare'}}
                        </span>
                    </label>
                `;
                html += `
                    <tr style="${{applied ? 'background: rgba(16, 185, 129, 0.04);' : ''}}">
                        <td style="white-space:nowrap;"><strong>${{j.published_at || j.discovered_at}}</strong></td>
                        <td><strong>${{j.company}}</strong><br><small style="color:#94a3b8;">${{j.portal}}</small></td>
                        <td><a href="${{j.url}}" target="_blank" rel="noopener noreferrer" style="color:#38bdf8;text-decoration:none;font-weight:600;">${{j.title}}</a><br><small style="color:#94a3b8;">${{j.location}} (${{j.work_mode}})</small></td>
                        <td><span style="color:#10b981;font-weight:600;">💰 ${{j.estimated_ral}}</span></td>
                        <td><span style="background:${{badgeColor}};color:#fff;padding:3px 8px;border-radius:10px;font-weight:700;font-size:12px;">${{score}}%</span></td>
                        <td style="text-align:center;">${{appliedBadge}}</td>
                        <td><small style="color:#cbd5e1;line-height:1.35;">${{j.ai_reasoning || '-'}}</small></td>
                        <td style="white-space:nowrap;">
                            <div style="display:flex;gap:6px;align-items:center;">
                                <a href="${{j.url}}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-quick-link" title="Apri subito la pagina dell'annuncio">🔗 Apri</a>
                                <button class="btn btn-sm btn-blue" onclick="applyJob(${{j.id}})">Invia CV</button>
                            </div>
                        </td>
                    </tr>
                `;
            }}
            tbody.innerHTML = html || '<tr><td colspan="8" style="text-align:center;padding:30px;">Nessuna offerta trovata.</td></tr>';
        }}

        function showMoreRows() {{
            TABLE_LIMIT += 150;
            renderHistoricalTable();
        }}

        let SCRAPER_HEALTH_DATA = {{ total_portals: 0, healthy_count: 0, warning_count: 0, needs_update_count: 0, alerts: [], portals: [] }};

        function switchView(view) {{
            document.getElementById('viewAnalytics').classList.toggle('hidden', view !== 'analytics');
            document.getElementById('viewQueue').classList.toggle('hidden', view !== 'queue');
            document.getElementById('viewScraperHealth').classList.toggle('hidden', view !== 'health');
            document.getElementById('viewProfileConfig').classList.toggle('hidden', view !== 'profile');

            document.getElementById('tabBtnAnalytics').classList.toggle('active', view === 'analytics');
            document.getElementById('tabBtnQueue').classList.toggle('active', view === 'queue');
            document.getElementById('tabBtnHealth').classList.toggle('active', view === 'health');
            document.getElementById('tabBtnProfile').classList.toggle('active', view === 'profile');

            if (view === 'health') {{
                renderScraperHealthTable();
            }} else if (view === 'profile') {{
                loadProfileConfig();
            }}
        }}

        async function loadScraperHealth() {{
            try {{
                const res = await fetch('/api/scraper-health');
                SCRAPER_HEALTH_DATA = await res.json();
                updateDomHealthBanner();
                renderScraperHealthTable();
            }} catch (e) {{
                console.warn('Errore caricamento scraper health:', e);
            }}
        }}

        function updateDomHealthBanner() {{
            const banner = document.getElementById('domHealthBanner');
            const textEl = document.getElementById('domHealthBannerText');
            const badge = document.getElementById('navDomAlertBadge');
            if (!banner || !textEl) return;

            const alertsCount = SCRAPER_HEALTH_DATA.needs_update_count || 0;
            const total = SCRAPER_HEALTH_DATA.total_portals || 0;
            const healthy = SCRAPER_HEALTH_DATA.healthy_count || 0;

            if (badge) {{
                badge.style.display = alertsCount > 0 ? 'inline-block' : 'none';
                badge.innerText = alertsCount;
            }}

            if (alertsCount > 0) {{
                const names = (SCRAPER_HEALTH_DATA.alerts || []).slice(0, 6).map(a => a.company).join(', ');
                const extra = alertsCount > 6 ? ` e altri ${{alertsCount - 6}}` : '';
                banner.style.borderColor = 'rgba(244, 63, 94, 0.55)';
                banner.style.background = 'rgba(244, 63, 94, 0.14)';
                textEl.innerHTML = `🚨 <strong>AVVISO CAMBIO DOM / AGGIORNAMENTO SCRAPER (${{alertsCount}} portali):</strong> Le offerte non risultano più in chiaro o il DOM è cambiato su: <strong style="color:#fda4af;">${{names}}${{extra}}</strong>. Clicca su <em>Dettaglio Portali</em> per verificare quale scraper aggiornare.`;
            }} else {{
                banner.style.borderColor = 'rgba(16, 185, 129, 0.35)';
                banner.style.background = 'rgba(16, 185, 129, 0.08)';
                textEl.innerHTML = `✅ <strong>Integrità DOM &amp; Offerte in Chiaro Verificata:</strong> Tutti i <strong>${{total}}</strong> portali aziendali monitorati sono allineati (${{healthy}} con posizioni attive in chiaro, 0 scraper da aggiornare).`;
            }}
        }}

        function renderScraperHealthTable() {{
            const tbody = document.getElementById('scraperHealthTableBody');
            if (!tbody) return;

            document.getElementById('healthKpiTotal').innerText = SCRAPER_HEALTH_DATA.total_portals || 0;
            document.getElementById('healthKpiHealthy').innerText = SCRAPER_HEALTH_DATA.healthy_count || 0;
            document.getElementById('healthKpiAlerts').innerText = SCRAPER_HEALTH_DATA.needs_update_count || 0;
            document.getElementById('healthKpiWarnings').innerText = SCRAPER_HEALTH_DATA.warning_count || 0;

            const flt = (document.getElementById('fltHealthStatus')?.value || 'all');
            const rows = (SCRAPER_HEALTH_DATA.portals || []).filter(p => {{
                if (flt === 'needs_update') return p.needs_scraper_update || p.status === 'DOM_CHANGED' || p.status === 'HTTP_ERROR';
                if (flt === 'healthy') return p.status === 'HEALTHY' && !p.needs_scraper_update;
                if (flt === 'warning') return p.status === 'WARNING_EMPTY' && !p.needs_scraper_update;
                return true;
            }});

            let html = '';
            for (const p of rows) {{
                let badgeHtml = '';
                if (p.needs_scraper_update || p.status === 'DOM_CHANGED') {{
                    badgeHtml = `<span style="background:rgba(244,63,94,0.2);color:#fda4af;border:1px solid rgba(244,63,94,0.45);padding:3px 8px;border-radius:6px;font-size:11.5px;font-weight:700;">🚨 DOM Cambiato / Aggiornare Scraper</span>`;
                }} else if (p.status === 'HTTP_ERROR') {{
                    badgeHtml = `<span style="background:rgba(244,63,94,0.2);color:#fda4af;border:1px solid rgba(244,63,94,0.45);padding:3px 8px;border-radius:6px;font-size:11.5px;font-weight:700;">❌ Non in Chiaro / Blocco HTTP</span>`;
                }} else if (p.status === 'WARNING_EMPTY') {{
                    badgeHtml = `<span style="background:rgba(245,158,11,0.18);color:#fcd34d;border:1px solid rgba(245,158,11,0.4);padding:3px 8px;border-radius:6px;font-size:11.5px;font-weight:700;">⚠️ DOM OK (0 Offerte coi Filtri)</span>`;
                }} else {{
                    badgeHtml = `<span style="background:rgba(16,185,129,0.2);color:#34d399;border:1px solid rgba(16,185,129,0.4);padding:3px 8px;border-radius:6px;font-size:11.5px;font-weight:700;">✅ DOM Integro &amp; Offerte in Chiaro</span>`;
                }}

                const checkedDate = (p.last_checked_at || '').slice(0, 16).replace('T', ' ');
                const safeComp = (p.company || '').replace(/'/g, "\\\\'");
                html += `
                    <tr>
                        <td><strong>${{p.company}}</strong></td>
                        <td><span class="tag">${{p.site_type}}</span></td>
                        <td>${{badgeHtml}}</td>
                        <td>
                            <strong style="color:#fff;">${{p.last_jobs_count || 0}}</strong> attive
                            <div style="font-size:11px;color:#94a3b8;">Storico max: ${{p.last_success_count || 0}}</div>
                        </td>
                        <td><small style="color:#cbd5e1;line-height:1.4;">${{p.diagnostic_message || '-'}}</small></td>
                        <td style="font-size:12px;color:#94a3b8;">${{checkedDate || '-'}}</td>
                        <td style="white-space:nowrap;">
                            <div style="display:flex;gap:6px;">
                                <a href="${{p.portal_url}}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-quick-link" title="Apri il portale aziendale">🌐 Sito</a>
                                <button class="btn btn-sm" onclick="runDomHealthCheck('${{safeComp}}')" title="Verifica live il DOM di questa azienda">🔍 Test DOM</button>
                            </div>
                        </td>
                    </tr>
                `;
            }}
            tbody.innerHTML = html || '<tr><td colspan="7" style="text-align:center;padding:26px;">Nessun portale trovato per il filtro selezionato.</td></tr>';
        }}

        async function runDomHealthCheck(companyName) {{
            const btns = [
                document.getElementById('btnVerifyDomTop'),
                document.getElementById('btnVerifyDomBanner'),
                document.getElementById('btnVerifyAllDom')
            ].filter(Boolean);
            btns.forEach(b => {{ b.disabled = true; b.innerText = '⏳ Verifica DOM in corso...'; }});
            try {{
                const payload = companyName ? {{ company: companyName }} : {{}};
                const res = await fetch('/api/scraper-health/check', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload)
                }});
                SCRAPER_HEALTH_DATA = await res.json();
                updateDomHealthBanner();
                renderScraperHealthTable();
                if (!companyName) {{
                    switchView('health');
                }}
            }} catch (e) {{
                alert('Errore durante la verifica DOM: ' + e);
            }} finally {{
                if (document.getElementById('btnVerifyDomTop')) {{
                    document.getElementById('btnVerifyDomTop').disabled = false;
                    document.getElementById('btnVerifyDomTop').innerText = '🩺 Verifica DOM';
                }}
                if (document.getElementById('btnVerifyDomBanner')) {{
                    document.getElementById('btnVerifyDomBanner').disabled = false;
                    document.getElementById('btnVerifyDomBanner').innerText = '🔍 Verifica DOM Live';
                }}
                if (document.getElementById('btnVerifyAllDom')) {{
                    document.getElementById('btnVerifyAllDom').disabled = false;
                    document.getElementById('btnVerifyAllDom').innerText = '🔍 Verifica DOM di Tutti i Portali Ora';
                }}
            }}
        }}

        async function loadProfileConfig() {{
            try {{
                const res = await fetch('/api/profile');
                const data = await res.json();
                const p = data.candidate_profile || {{}};
                const prefs = data.search_preferences || {{}};
                const sen = p.target_seniority || {{}};

                document.getElementById('profFirstName').value = p.first_name || '';
                document.getElementById('profLastName').value = p.last_name || '';
                document.getElementById('profEmail').value = p.email || '';
                document.getElementById('profPhone').value = p.phone || '';
                document.getElementById('profCity').value = p.city || '';
                document.getElementById('profRal').value = p.expected_salary_ral || '';
                document.getElementById('profLinkedin').value = p.linkedin_url || '';
                document.getElementById('profGithub').value = p.portfolio_or_github_url || '';
                document.getElementById('profCvPath').value = p.cv_pdf_path || 'data/cv.pdf';
                document.getElementById('profKeywords').value = (prefs.keywords || []).join(', ');
                document.getElementById('profTargetCities').value = (prefs.target_cities || []).join(', ');
                document.getElementById('profWorkModes').value = (prefs.work_modes || ['remoto', 'ibrido', 'in_sede']).join(', ');
                document.getElementById('profSeniorityTiers').value = (sen.accepted_tiers || ['stage', 'junior', 'mid', 'senior']).join(', ');
                document.getElementById('profEducation').value = p.education || '';
                document.getElementById('profNotes').value = p.custom_Preferences_notes || '';
            }} catch (e) {{
                console.warn('Errore caricamento profilo:', e);
            }}
        }}

        async function saveProfileConfig() {{
            const splitList = (val) => (val || '').split(',').map(s => s.trim()).filter(Boolean);
            const payload = {{
                first_name: document.getElementById('profFirstName').value,
                last_name: document.getElementById('profLastName').value,
                email: document.getElementById('profEmail').value,
                phone: document.getElementById('profPhone').value,
                city: document.getElementById('profCity').value,
                expected_salary_ral: document.getElementById('profRal').value,
                linkedin_url: document.getElementById('profLinkedin').value,
                portfolio_or_github_url: document.getElementById('profGithub').value,
                cv_pdf_path: document.getElementById('profCvPath').value,
                keywords: splitList(document.getElementById('profKeywords').value),
                target_cities: splitList(document.getElementById('profTargetCities').value),
                work_modes: splitList(document.getElementById('profWorkModes').value),
                accepted_tiers: splitList(document.getElementById('profSeniorityTiers').value),
                education: document.getElementById('profEducation').value,
                custom_Preferences_notes: document.getElementById('profNotes').value
            }};
            const res = await fetch('/api/profile', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(payload)
            }});
            const out = await res.json();
            if (res.ok) {{
                alert('✅ ' + out.message);
                window.location.reload();
            }} else {{
                alert('❌ Errore salvataggio: ' + (out.error || 'Impossibile salvare'));
            }}
        }}

        function saveChartAsPNG(canvasId, filename) {{
            const canvas = document.getElementById(canvasId);
            if (!canvas) return;
            const link = document.createElement('a');
            link.download = filename || (canvasId + '.png');
            link.href = canvas.toDataURL('image/png', 1.0);
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }}

        function downloadAllCharts() {{
            const list = [
                ['chartTimeline', '01_trend_temporale_posizioni.png'],
                ['chartCompanyTimeline', '02_storico_per_azienda.png'],
                ['chartCompanies', '03_classifica_aziende.png'],
                ['chartSkills', '04_competenze_piu_richieste.png'],
                ['chartSkillGap', '05_skill_gap_analysis.png'],
                ['chartCategories', '06_famiglie_professionali.png'],
                ['chartGeo', '07_poli_geografici_e_modalita.png'],
                ['chartSeniority', '08_seniority_mercato.png'],
                ['chartSalaryCategory', '09_ral_media_per_categoria.png']
            ];
            list.forEach((item, idx) => {{
                setTimeout(() => saveChartAsPNG(item[0], item[1]), idx * 250);
            }});
        }}

        function exportFilteredCSV() {{
            if (!FILTERED_JOBS.length) {{
                alert('Nessun dato da esportare con i filtri attuali.');
                return;
            }}
            const headers = ['ID', 'Data_Pubblicazione', 'Data_Scoperta', 'Azienda', 'Portale', 'Titolo', 'Categoria_Ruolo', 'Seniority', 'Polo_Citta', 'Modalita_Lavoro', 'Localita_Raw', 'Match_Score', 'Candidatura_Inviata', 'Data_Candidatura', 'RAL_Stimata', 'Competenze_Rilevate', 'Skill_Mancanti', 'URL'];
            const rows = FILTERED_JOBS.map(j => [
                j.id,
                j.published_at,
                j.discovered_at,
                `"${{(j.company || '').replace(/"/g, '""')}}"`,
                `"${{(j.portal || '').replace(/"/g, '""')}}"`,
                `"${{(j.title || '').replace(/"/g, '""')}}"`,
                `"${{(j.role_category || '').replace(/"/g, '""')}}"`,
                `"${{(j.seniority || '').replace(/"/g, '""')}}"`,
                `"${{(j.city_hub || '').replace(/"/g, '""')}}"`,
                `"${{(j.work_mode || '').replace(/"/g, '""')}}"`,
                `"${{(j.location || '').replace(/"/g, '""')}}"`,
                j.match_score,
                isJobApplied(j) ? '"Sì"' : '"No"',
                `"${{(j.applied_at || '').replace(/"/g, '""')}}"`,
                `"${{(j.estimated_ral || '').replace(/"/g, '""')}}"`,
                `"${{(j.skills || []).join('; ')}}"`,
                `"${{(j.missing_skills || []).join('; ')}}"`,
                `"${{(j.url || '').replace(/"/g, '""')}}"`
            ]);
            const csvContent = '\\uFEFF' + [headers.join(','), ...rows.map(r => r.join(','))].join('\\n');
            const blob = new Blob([csvContent], {{ type: 'text/csv;charset=utf-8;' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `market_intelligence_offerte_${{new Date().toISOString().slice(0,10)}}.csv`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
        }}

        async function triggerDateBackfill() {{
            const btn = document.getElementById('btnBackfill');
            btn.disabled = true;
            btn.innerText = '⏳ Sincronizzazione...';
            try {{
                const res = await fetch('/api/analytics/backfill-dates', {{ method: 'POST' }});
                const data = await res.json();
                await loadAnalyticsData();
                alert(`Sincronizzazione completata! Aggiornate ${{data.updated}} date di pubblicazione storiche dai portali.`);
            }} catch (e) {{
                alert('Errore durante la sincronizzazione: ' + e);
            }} finally {{
                btn.disabled = false;
                btn.innerText = '🔄 Sincronizza Date';
            }}
        }}

        async function runNow() {{
            const res = await fetch('/api/run-now', {{ method: 'POST' }});
            const data = await res.json();
            alert(data.message);
        }}

        async function applyJob(id) {{
            const res = await fetch('/api/jobs/' + id + '/apply', {{ method: 'POST' }});
            const data = await res.json();
            alert(data.message);
        }}

        window.addEventListener('DOMContentLoaded', () => {{
            loadAnalyticsData();
            loadScraperHealth();
        }});
    </script>
</body>
</html>"""

