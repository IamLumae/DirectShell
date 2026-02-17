# Project DirectShell — Brainstorm
**Erstellt:** 2026-02-16
**Status:** Konzeptphase

---

## Name
**DirectShell** — Direct Shell Access to any GUI

## Vision (Martins Originalworte)
> Ein Programm das ein fast durchsichtiges "Fenster" erstellt welches man ueber eine beliebige offene App ziehen kann und alles spiegelt was unter ihr liegt. Wenn du auf diese App drueckst geht der Input durch die App in die darunter liegende App.

## Kern-Insight
**Jede Tastatureingabe und jeder Mausklick ist eine Eingabe. Und JEDE App des Planeten hat einen Connector dafuer: das Betriebssystem.**

Es gibt keinen Sonderweg. Claude Desktop, ChatGPT, Notepad, SAP, Excel, Spiele — ALLES hoert auf dieselben OS-Level Input Events (`WM_KEYDOWN`, `WM_CHAR`, `WM_LBUTTONDOWN`, `WM_MOUSEMOVE`). DirectShell setzt sich ZWISCHEN OS und App.

## Was DirectShell ist

**Ein universeller Adapter der JEDER KI erlaubt JEDES Programm der Welt zu steuern und zu verstehen.**

Jede KI ist eingesperrt — sie kann nur mit Dingen interagieren die APIs haben oder fuer sie gebaut wurden. DirectShell reisst diese Mauer ein. Fuer ALLE Programme. Fuer ALLE KIs.

**Grundlagenforschung.** Nicht App, nicht Produkt — ein Primitiv.
Auf dem Level von PowerShell, Browser, API. Etwas Universelles.

Technisch kombiniert es zum ersten Mal:
1. Transparentes Click-Through Fenster (Overlay)
2. Input Interception (Keyboard/Mouse Hooks)
3. Input Modification vor Zustellung an die Ziel-App
4. **Feedback Layer: Accessibility Tree als semantische Ausgabe**

**Keiner hat das bisher als Produkt kombiniert.** (Recherche 16.02.26 bestaetigt)
- WindowTop = Transparenz + Passthrough, keine Modifikation
- AutoHotkey = Hooks + Modifikation, kein Overlay
- Interception Driver = Low-Level Library, kein Produkt

## UX-Paradigma: DirectShell IST die App

### Snap-Verhalten
1. User zieht DirectShell ueber eine beliebige App
2. DirectShell **erkennt** das Fenster darunter (`WindowFromPoint()`)
3. **Snap** — DirectShell passt sich automatisch an Groesse + Position an
4. Ab jetzt: DirectShell = das Fenster
   - Verschieben → verschiebt die App mit
   - Minimieren → minimiert die App mit
   - Schliessen → schliesst die App mit
   - Resizen → resized die App mit
5. **Ein einziger zusaetzlicher Key: UNSNAP** → App ist wieder eigenstaendig

### Null Lernkurve
- User lernt NICHTS Neues
- Alles verhaelt sich wie gewohnt
- DirectShell ist unsichtbar bis man es braucht
- "Unsnap" ist der einzige neue Tastendruck

## Technische Architektur

### Kern-APIs (Windows)
| Funktion | Zweck |
|----------|-------|
| `SetWindowsHookEx` | Low-Level Keyboard/Mouse Hooks — Input abfangen BEVOR die App ihn sieht |
| `WindowFromPoint()` | Welches Fenster ist unter dem Cursor? |
| `GetWindowRect()` | Groesse/Position des Zielfensters |
| `SetWindowPos()` | DirectShell + Ziel-App synchron bewegen/resizen |
| `ShowWindow()` | Minimize/Maximize/Restore synchron |
| `SendMessage(WM_CLOSE)` | Schliessen weiterleiten |
| `WS_EX_LAYERED` + `WS_EX_TRANSPARENT` | Transparentes Click-Through Fenster |

### Window-Binding: Zwei Ansaetze

**Ansatz 1 — `SetParent()` (elegant)**
- Ziel-App wird Child-Window von DirectShell
- OS uebernimmt Sync automatisch (Move, Minimize, Close)
- Unsnap = `SetParent(NULL)`
- Risiko: Manche Apps wehren sich gegen Parent-Aenderung

**Ansatz 2 — Manuelles Sync (sicherer)**
- DirectShell hoert auf eigene Window-Events
- Spiegelt alles auf die Ziel-App via API-Calls
- Kompatibler mit allen Apps
- Mehr Code, aber zuverlaessiger

**Empfehlung:** Ansatz 2 als Standard, Ansatz 1 als optionale "Deep Bind" Option

### Stack-Optionen
| Option | Pro | Contra |
|--------|-----|--------|
| **Rust + Tauri** | Bekannter Stack (Veritas), native Performance, klein | Tauri's Window-API evtl. limitiert fuer Low-Level Hooks |
| **Rust pur (winapi crate)** | Maximale Kontrolle, kein Framework-Overhead | Mehr Boilerplate, kein UI-Framework |
| **C# / WPF** | Bester nativer Windows-Support fuer transparente Fenster | Anderer Stack als bestehende Projekte |

## Was DirectShell KANN (Use Cases)

### Phase 1: GUI Layer ✅ DONE (2026-02-16)
**Status:** Funktional komplett. Optisch nicht production-ready, aber alles tut was es soll.

Implementiert:
- [x] Transparentes Overlay (WS_EX_LAYERED + LWA_COLORKEY + LWA_ALPHA)
- [x] Snap + Bind — erkennt App unter Cursor, snapped bei 20% Overlap
- [x] Input Passthrough — Klicks und Tasten gehen durch (HTTRANSPARENT)
- [x] Bidirektionaler Position-Sync (60fps, Move/Resize)
- [x] Synchronisiertes Minimize + Close
- [x] Desktop/Taskbar-Snap Prevention (Shell Window Filtering)
- [x] Owner-Window Z-Order (gesnappt = gleiche Ebene wie App, nicht always-on-top)
- [x] Dynamische Titlebar-Hoehe via UI Automation (passt sich an Ziel-App an)
- [x] Smart Unsnap-Button Positionierung via Accessibility Tree (neben echten Caption Buttons)
- [x] Unsnap → zurueck auf Startgroesse
- [x] Close-Button (unsnapped)
- [x] Double-Buffered Rendering (flackerfrei)
- [x] Gradient-Lichtreflex Animation (cos² Falloff, 30fps)
- [x] 3D Anthrazit-Rahmen mit abgerundeten Ecken
- [x] ~200 KB Binary, zero Dependencies

Stack-Entscheidung: **Rust pur (winapi crate)** — kein Tauri, kein Framework

### Phase 2: Feedback Engine ✅ DONE (2026-02-16)
**Status:** Funktional komplett. DirectShell liest JEDE App als durchsuchbare Datenbank.

Implementiert:
- [x] UIA RawView Walker (rekursiv, unlimitierte Tiefe + Kinder — `i32::MAX`)
- [x] Persistente App-DBs (`ds_profiles/appname.db`) — ueberlebt Unsnap/Close
- [x] SQLite WAL-Mode + auto_vacuum=FULL — concurrent Read/Write, kein Bloat
- [x] Schema: `elements` (id, parent_id, depth, role, name, value, automation_id, enabled, offscreen, x, y, w, h) + `meta`
- [x] Streaming Writes — INSERT waehrend Tree Walk, COMMIT alle 200 Elemente (progressive Verfuegbarkeit)
- [x] Background Thread mit Re-Entry Guard (kein Main-Thread Blocking)
- [x] UIA ConnectionTimeout (2s max pro Element, kein Haenger)
- [x] 2 Hz Update-Rate (500ms Timer)
- [x] Chromium Accessibility Trigger (`SPI_SETSCREENREADER` + MSAA-Probe + `WM_GETOBJECT`)
- [x] File Logging (`directshell.log`) mit Timestamps und Performance-Metriken
- [x] DB bleibt persistent, Log wird bei Start geleert

---

## Field Report: Die ersten Tests der Welt (16.02.2026)

**DirectShell ist 6 Stunden alt. Wir sind die ersten Nutzer des Planeten.**

Jeder Test unten war das ERSTE MAL dass dieses Programm eine bestimmte App gelesen hat.
Jedes Problem das wir fanden war eine Lektion. Jede Lektion machte DirectShell universeller.
In 6 Monaten — wenn eine Million Nutzer jedes Programm der Welt dran hatten —
wird jede App ein bekanntes Profil haben. Jede Eigenart dokumentiert. Jede Config geteilt.

### Test 1: Claude Desktop (Electron/Chromium)

| Metrik | Wert |
|--------|------|
| Elemente | 11.454 |
| Dump-Zeit | ~550ms |
| DB-Groesse | ~1.5 MB |
| Sichtbar | Kompletter Chat-Verlauf, jede Nachricht, jeder Button, jeder Link, jedes Eingabefeld |

**Erkenntnis:** Selbst-auferlegtes `MAX_CHILDREN: 100` schnitt den Chat bei Message 100 ab.
Wir dachten Chromium versteckt Daten. In Wahrheit waren WIR das Limit.
Limit entfernt → 11.454 Elemente → ALLES da.

```sql
SELECT name FROM elements WHERE role='Text' AND length(name)>10 ORDER BY y DESC LIMIT 1
SELECT name FROM elements WHERE name LIKE '%lars%nda%'
SELECT name FROM elements WHERE role='Button' AND offscreen=0
```

### Test 2: Datei-Explorer (Win32 nativ)

| Metrik | Wert |
|--------|------|
| Elemente | 211 |
| Dump-Zeit | instant (<15ms) |
| Sichtbar | Kompletter Verzeichnisbaum, Dateinamen, Statusleiste, Navigation |

**Erkenntnis:** Win32-native Apps liefern sofort. Kein Accessibility-Trigger noetig.
Der schnellste Test — 211 Elemente in unter 15 Millisekunden.

### Test 3: GitHub Desktop (Electron/Chromium)

| Metrik | Wert |
|--------|------|
| Elemente | 398 |
| Dump-Zeit | ~550ms |
| Sichtbar | Repo-Name (server-new), Branch (main), 3912 geaenderte Dateien, individuelle File-Status |

**Erkenntnis:** Mid-Stream Reads liefern exakt `STREAM_BATCH` (200) Elemente wenn man
zwischen erstem und finalem COMMIT liest. Loesung: 500ms warten oder Retry-Loop.

### Test 4: Opera Browser (Chromium) — Der haerteste Gegner

| Metrik | Wert |
|--------|------|
| Elemente | 800 |
| Dump-Zeit | ~300-400ms |
| DB-Groesse | 196 KB |
| Max Depth | 30 |
| Sichtbar | 16 Tabs (Titel + Favicon), Adressleiste mit URL, Bookmarks, Sidebar, kompletter Gmail-Inhalt |

**Phase 1 — 9 Elemente (Shell only):**
Chromium exponiert seinen Accessibility Tree NICHT standardmaessig.
Nur die Browser-Shell (Window, Pane, TitleBar) — kein Web-Content.

**Phase 2 — MSAA-Probe (`AccessibleObjectFromWindow`):**
Probing auf Target + Child-Windows via `EnumChildWindows`.
Ergebnis: Chromium ignoriert es. Immer noch 9 Elemente.

**Phase 3 — Screen Reader Flag (`SPI_SETSCREENREADER`):**
Windows System-Parameter: "Ein Screen Reader ist aktiv."
MUSS vor dem Browser-Start gesetzt sein. Browser der NACH DirectShell startet sieht das Flag.
Ergebnis: 441 Elemente. Browser-Chrome komplett. Aber Gmail-Emails fehlen.

**Phase 4 — RawView statt ControlView:**
`ControlViewWalker` filtert "unwichtige" Elemente. Gmail-Rows wurden rausgefiltert.
Wechsel auf `RawViewWalker` — zeigt ALLES was UIA kennt, ungefiltert.

**Phase 5 — Depth Limit entfernt:**
Gmail verschachtelt extrem tief (Depth 30). Unser `MAX_DEPTH: 20` schnitt bei Depth 20 ab.
Emails lagen auf Depth 25-30. Limit auf `i32::MAX` gesetzt.
Ergebnis: 800 Elemente. JEDE Email: Absender, Betreff, Datum, Preview-Text.

**Die Lektionen aus Opera (chronologisch):**

| Problem | Ursache | Loesung | Universell? |
|---------|---------|---------|-------------|
| Nur 9 Elemente | Chromium baut keinen A11y-Tree ohne Signal | `SPI_SETSCREENREADER` bei Startup | JA — gilt fuer alle Chromium-Apps |
| Browser-Chrome ok, Web-Content fehlt | ControlView filtert Gmail-Rows | `RawViewWalker` statt `ControlViewWalker` | JA — gilt fuer alle Web-Apps |
| Gmail-Struktur da, Emails fehlen | `MAX_DEPTH: 20` zu flach | Limit entfernt (`i32::MAX`) | JA — Primitivum hat keine Limits |
| Gmail-Emails erst nach Reload | Screen Reader Mode nur bei Page Load | Flag muss VOR Browser-Start gesetzt sein | JA — einmaliges Setup |
| DB waechst, schrumpft nie | SQLite Freelist nach DELETE | DROP+CREATE + auto_vacuum=FULL | JA — saubere DBs |

**Jede dieser Lektionen war ein Patch der DirectShell universeller gemacht hat.**
**Keine davon war app-spezifisch. ALLE gelten fuer alle Programme.**

### Was DirectShell nach 6 Stunden weiss

```
CHROMIUM-APPS (Opera, Chrome, Edge, Electron):
  → SPI_SETSCREENREADER = TRUE vor Browser-Start
  → MSAA-Probe auf Child-Windows (EnumChildWindows + AccessibleObjectFromWindow)
  → RawViewWalker (nicht ControlView)
  → Page Reload nach Flag-Setzen fuer volle Web-Content Accessibility

WIN32-APPS (Explorer, Notepad, Office):
  → Funktioniert sofort. Keine Tricks noetig.

ALLE APPS:
  → Keine kuenstlichen Limits (Tiefe, Kinder, Elemente)
  → Streaming Writes fuer progressive Verfuegbarkeit
  → Persistente per-App DBs in ds_profiles/
```

### Die Vision: 6 Monate spaeter

Heute kennt DirectShell 4 Apps. Wir sind die einzigen zwei Menschen die es je benutzt haben.

Stell dir vor: 1 Million Nutzer. Jeder snappt auf seine Programme.
Jeder entdeckt eine Eigenart. Jeder teilt sein App-Profil.

- SAP? Jemand in einem Grosskonzern hat das Profil geschrieben.
- AutoCAD? Ein Ingenieur hat die Element-Hierarchie dokumentiert.
- Bloomberg Terminal? Ein Trader hat die DataGrid-Struktur gemappt.
- Jedes Nischen-Tool der Welt? Irgendjemand nutzt es und teilt sein Profil.

**DirectShell wird nicht von UNS besser. Es wird von JEDEM besser der es benutzt.**
Das ist der Netzwerkeffekt eines Primitivums.

PowerShell hat heute 10.000+ Cmdlets — nicht weil Microsoft sie alle geschrieben hat,
sondern weil die Community es getan hat. DirectShell-Profile sind die Cmdlets des Frontends.

**DirectShell ist das Primitivum. Was fehlt sind App-Profile — Anleitungen WIE man die Daten
einer spezifischen App interpretiert. Genau wie PowerShell Cmdlets braucht.**

### Phase 3 (Input Middleware)
- **HEDS Integration** — PII-Sanitization auf JEDER App, nicht nur Browser
  - User tippt in Claude Desktop → DirectShell faengt ab → sanitized → sendet weiter
  - Loest das "Desktop Apps sind geschlossene Clients" Problem KOMPLETT
- **Input Logging** — was wurde getippt? (Accessibility, Audit)
- **Text Injection** — DirectShell fuegt Text ein den der User nicht getippt hat

### Phase 4 (Plattform)
- **App-Profile** — per-App Anleitungen wie der Tree zu interpretieren ist (das "Cmdlet"-Layer)
- **MCP Server** — DirectShell als Tool fuer LLM Agents (`read_ui()`, `find_element()`, `click()`)
- **Plugin-System** — Dritte koennen Middleware-Module bauen
- **Hotkey Toggle** — DirectShell on/off per Tastenkombination
- **Multi-Monitor** — mehrere DirectShell-Instanzen auf verschiedenen Apps

## Marktposition

**Neue Produktkategorie.** Kein direkter Wettbewerber.

Existierende Tools machen TEILE davon:
- WindowTop: Overlay (keine Modifikation)
- AutoHotkey: Hooks (kein Overlay)
- Screen Readers: Input lesen (kein Modifizieren)

DirectShell = **erstes Produkt das alle drei kombiniert**.

## Der Feedback Layer — Accessibility Tree (DER Durchbruch)

### Das Problem aller anderen
Anthropic Computer Use, OpenAI Operator, Google Mariner — alle machen dasselbe:
**Screenshot → Vision Model → Pixel-Koordinaten → Klick**

Das ist Wahnsinn. LLMs sind NICHT nativ fuer Bilder. Man zwingt ein Textmodell Pixel zu interpretieren.
Teuer, langsam, unzuverlaessig, bricht bei jedem UI-Update.

### DirectShell' Loesung: Kein einziges Bild
Windows hat bereits einen semantischen Feedback-Layer eingebaut: **UI Automation (UIA)**.
Gebaut fuer Barrierefreiheit (Screen Reader). Wir zweckentfremden ihn.

Jedes UI-Element exponiert sich als strukturierter Text:
```
Window: "Rechnung - Datev"
├── Menu: "Datei"
│   ├── MenuItem: "Neu"
│   ├── MenuItem: "Oeffnen"
│   └── MenuItem: "Speichern"
├── TextBox: "Kundennummer" → Value: "KD-4711"
├── TextBox: "Betrag" → Value: "1.299,00"
├── ComboBox: "Steuersatz" → Value: "19%"
├── Button: "Buchen" → IsEnabled: true
└── StatusBar: "Bereit"
```

Jedes Element hat: `Name`, `ControlType`, `Value`, `AutomationId`, `BoundingRectangle`, `IsEnabled`, Parent/Child-Beziehungen.

### Warum das alles aendert

**Feedback (App → KI):** Accessibility Tree = strukturierter Text. LLM liest das NATIV. Kein Bild, kein OCR, kein Vision Model. Die KI WEISS was jedes Element ist.

**Control (KI → App):** LLM sendet Textbefehle:
```
focus: "Eingabefeld"
type: "Hallo Welt"
press: "Enter"
```
DirectShell uebersetzt in echte Input-Events. Tastatur und Maus werden der KI als TEXT-WAEHLBARE Inputs gegeben.

**Beide Richtungen sind Text. Beide Richtungen sind nativ LLM.**

Der fundamentale Designfehler den Anthropic, OpenAI und Google ALLE machen:
Sie schicken Bilder an Textmodelle. DirectShell schickt Text an Textmodelle.

---

## DAS WARUM — Die volle Vision

### Der heilige Gral
```
DirectShell → MCP → LLM → beliebiges Programm des Planeten
```

**DirectShell macht jedes GUI zu einer API.**

### Warum das so gewaltig ist

**1. Man-in-the-Middle fuer GUIs**
- Die App "denkt" der User klickt und tippt
- In Wahrheit steuert DirectShell (oder ein LLM ueber DirectShell)
- Da die App glaubt der Input kommt von der Hardware, kann sie sich nicht dagegen wehren
- Das OS sagt: "Das ist ein legitimer Klick" — Ende der Diskussion

**2. Kein TOS. Keine AGB. Nichts.**
- API nutzen = Vertrag unterschreiben (TOS), du darfst nur was die API erlaubt
- Code modifizieren (Cracking) = Urheberrechtsverletzung
- DirectShell beruehrt weder Code noch API — es **simuliert den User**
- Rechtlich: Du bedienst eine App. Wie du deine Tastatur steuerst ist DEINE Sache

**3. Die Kette: DirectShell → MCP → LLM → App**
- **DirectShell (Koerper):** Sieht was die App sieht (Screen/Context), hat Haende (Input Hooks)
- **MCP (Nervensystem):** Standardisiert den Kontext — "hier ist das Eingabefeld"
- **LLM (Gehirn):** Versteht Semantik — sieht nicht "Button ID 452" sondern "Rechnung freigeben"
- **App (Werkzeug):** Muss NICHT "AI-ready" sein. Kann 20 Jahre alte AS/400-Maske sein

**4. Was Rabbit, Humane und Microsoft scheitern laesst**
- Rabbit R1: Versuchte eigene Hardware + eigenes OS → gescheitert
- Humane AI Pin: Eigene Hardware → gescheitert
- Microsoft Copilot: Versucht APIs zu erzwingen → langsam, limitiert
- **DirectShell: Geht einfach DRUEBER.** Braucht keine Erlaubnis, keine Integration, keine API

**5. Du demokratisierst Automation**
- 99% aller Business-Software ist nur ueber das Frontend bedienbar (APIs fehlen oder kosten extra)
- PowerShell automatisiert das Backend
- **DirectShell automatisiert das Frontend**
- User: "Buche alle Rechnungen aus diesem Ordner in Datev"
- DirectShell legt sich ueber Datev → LLM sieht das Fenster → steuert Maus → tippt Daten ein
- Datev merkt nicht dass kein Mensch davor sitzt

### Konkrete Power-Moves
- Voice Control in eine App bauen die es nicht hat
- Auto-Translate in einen Chat der es verbietet
- Copy-Paste in Feldern erzwingen die es blockieren
- HEDS PII-Sanitization auf JEDER App — nicht nur Browser
- Jede Legacy-Software LLM-steuerbar machen — ohne eine Zeile am Original zu aendern

### Die Machtverschiebung
> Normalerweise diktiert der Software-Hersteller: "So benutzt du meine App."
> Mit DirectShell: "Ich benutze deine App wie ICH will."

Vendor Lock-in entmachtet. Jede Software wird zur Marionette des Users.

---

## Strategische Bedeutung

DirectShell loest Martins wiederkehrendes Problem:
> "Wie komme ich zwischen User-Input und eine geschlossene Desktop-App?"

- HEDS brauchte Browser Extension als Kompromiss → DirectShell macht es nativ fuer JEDE App
- A.D.A. koennte DirectShell als Overlay nutzen → visuelles Feedback direkt auf der App
- Universell einsetzbar → groesste Zielgruppe aller Projekte
- **DirectShell ist das Fundament fuer ALLE anderen Projekte** — der universelle Connector

---

## Offene Fragen
- [x] ~~Stack-Entscheidung~~ → **Rust pur** (windows crate v0.58, kein Framework)
- [x] ~~MVP Scope~~ → Phase 1 (GUI Layer) done, Phase 2 (Input Middleware) next
- [ ] Name/Branding: "DirectShell" final?
- [ ] Lizenzmodell: Open Source? Freemium? Commercial?
- [ ] Anti-Cheat/Security: Manche Apps (Games, Banking) blockieren aktiv Input Hooks
- [ ] Optisches Polish: Farben, Animationen, Icon — production-ready UI
