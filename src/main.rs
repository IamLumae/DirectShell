// DirectShell — Universal Application Control Through the Accessibility Layer
// Copyright (C) 2026  Martin Gehrken (IamLumae)
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU Affero General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// SPDX-License-Identifier: AGPL-3.0-or-later

#![windows_subsystem = "windows"]

use std::ffi::c_void;
use std::fs::{self, OpenOptions};
use std::io::Write as IoWrite;
use std::mem;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicIsize, Ordering::SeqCst};
use std::sync::{Mutex, OnceLock};
use std::time::{Instant, SystemTime, UNIX_EPOCH};
use rusqlite::{Connection, params};
use windows::core::*;
use windows::Win32::Foundation::*;
use windows::Win32::Graphics::Gdi::*;
use windows::Win32::System::Com::*;
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Accessibility::*;
use windows::Win32::System::Threading::{GetCurrentThreadId, AttachThreadInput};
use windows::Win32::UI::Input::KeyboardAndMouse::*;
use windows::Win32::UI::WindowsAndMessaging::*;

// ── Farben (COLORREF = 0x00BBGGRR) ─────────────────
const INVIS: COLORREF = COLORREF(0x00FF00FF);
const TOP_CLR: COLORREF = COLORREF(0x00827873);
const SIDE_CLR: COLORREF = COLORREF(0x00736964);
const BOT_CLR: COLORREF = COLORREF(0x005F5550);
const HL_CLR: COLORREF = COLORREF(0x00D7CDC8);
const SH_CLR: COLORREF = COLORREF(0x00413732);
const ICON_CLR: COLORREF = COLORREF(0x00D0D0D0);

// ── Dimensionen ─────────────────────────────────────
const DEFAULT_TOP_H: i32 = 20;    // Standard-Höhe wenn ungesnappt
const SIDE_W: i32 = 4;
const GRIP: i32 = 12;
const CORNER_R: i32 = 8;
const FALLBACK_BTN_X: i32 = 140;
const ALPHA: u8 = 180;
const SNAP_THRESH: f64 = 0.20;
const SYNC_TIMER: usize = 1;
const ANIM_TIMER: usize = 2;
const TIMER_MS: u32 = 16;
const ANIM_MS: u32 = 33;
const LIGHT_PERIOD: f64 = 3000.0;
const LIGHT_LEN: f64 = 120.0;     // etwas länger für weicheren Fade
const LIGHT_STEPS: i32 = 24;      // Gradient-Auflösung
const INIT_W: i32 = 500;          // Startgröße (Breite)
const INIT_H: i32 = 350;          // Startgröße (Höhe)
const TREE_TIMER: usize = 3;      // Accessibility Tree Dump
const TREE_MS: u32 = 500;         // 2 Hz — genug Raum für ~200ms Dumps + Puffer
const INJECT_TIMER: usize = 4;    // Action Queue Processing (eigener Timer)
const INJECT_MS: u32 = 30;        // 33 Hz — schnelles Typing wie ein Mensch
const MAX_DEPTH: i32 = i32::MAX;  // Primitivum. Kein Limit.
const MAX_CHILDREN: i32 = i32::MAX; // Primitivum. Kein Limit.
const STREAM_BATCH: i32 = 200;    // COMMIT alle 200 Elemente → progressive Verfügbarkeit
const DB_DIR: &str = "ds_profiles";  // Persistente App-DBs
const ACTIVE_FILE: &str = "ds_profiles/is_active";  // Status für KI-Agents
const LOG_FILE: &str = "directshell.log";

// ── Logging ────────────────────────────────────────
fn log(msg: &str) {
    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = ts.as_secs();
    let millis = ts.subsec_millis();
    // HH:MM:SS.mmm (UTC, but good enough for debugging)
    let h = (secs / 3600) % 24;
    let m = (secs / 60) % 60;
    let s = secs % 60;
    let line = format!("[{:02}:{:02}:{:02}.{:03}] {}\n", h, m, s, millis, msg);
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(LOG_FILE) {
        let _ = IoWrite::write_all(&mut f, line.as_bytes());
    }
}

// ── Globaler State ──────────────────────────────────
static TARGET_HW: AtomicIsize = AtomicIsize::new(0);
static IS_SNAPPED: AtomicBool = AtomicBool::new(false);
static TREE_BUSY: AtomicBool = AtomicBool::new(false);
static CURRENT_DB: Mutex<String> = Mutex::new(String::new());
static KB_HOOK: AtomicIsize = AtomicIsize::new(0);
static EVENT_UIA_PTR: AtomicIsize = AtomicIsize::new(0);      // UIA instance for event handlers (cleanup on unsnap)
static LAST_EVENT_DUMP_MS: AtomicIsize = AtomicIsize::new(0);  // Debounce: last event-triggered dump timestamp
static LAST_X: AtomicI32 = AtomicI32::new(0);
static LAST_Y: AtomicI32 = AtomicI32::new(0);
static LAST_W: AtomicI32 = AtomicI32::new(0);
static LAST_H: AtomicI32 = AtomicI32::new(0);
static BTN_OFF_X: AtomicI32 = AtomicI32::new(FALLBACK_BTN_X);
static DYN_TOP_H: AtomicI32 = AtomicI32::new(DEFAULT_TOP_H);
static START_TIME: OnceLock<Instant> = OnceLock::new();

fn tgt() -> HWND { HWND(TARGET_HW.load(SeqCst) as *mut _) }
fn snapped() -> bool { IS_SNAPPED.load(SeqCst) }
fn top_h() -> i32 { DYN_TOP_H.load(SeqCst) }
fn save(x: i32, y: i32, w: i32, h: i32) {
    LAST_X.store(x, SeqCst); LAST_Y.store(y, SeqCst);
    LAST_W.store(w, SeqCst); LAST_H.store(h, SeqCst);
}
fn saved() -> (i32, i32, i32, i32) {
    (LAST_X.load(SeqCst), LAST_Y.load(SeqCst),
     LAST_W.load(SeqCst), LAST_H.load(SeqCst))
}

// App-Name aus Fenstertitel extrahieren → sauberer DB-Filename
// "Google Gemini – Opera" → "opera.db"
// "GitHub Desktop" → "github_desktop.db"
// "release – Datei-Explorer" → "datei_explorer.db"
fn db_name_from_title(title: &str) -> String {
    // Letztes Segment nach " – " (em-dash) oder " - " (hyphen)
    let app = title
        .rsplit(&['\u{2013}', '\u{2014}'][..]) // en-dash, em-dash
        .next()
        .unwrap_or(title);
    let app = app
        .rsplit(" - ")
        .next()
        .unwrap_or(app)
        .trim();

    // Sanitize: lowercase, nur alphanumerisch + underscore
    let clean: String = app
        .chars()
        .map(|c| if c.is_alphanumeric() { c.to_ascii_lowercase() } else { '_' })
        .collect();
    let clean = clean.trim_matches('_');

    // Fallback
    let name = if clean.is_empty() { "unknown" } else { clean };
    format!("{}/{}.db", DB_DIR, name)
}

fn get_db_path() -> String {
    CURRENT_DB.lock().unwrap().clone()
}

fn set_db_path(path: &str) {
    *CURRENT_DB.lock().unwrap() = path.to_string();
}

/// Write is_active status file for AI agents.
/// Snapped: app name + .a11y path + .snap path
/// Unsnapped: "none"
fn write_active_status(db_path: &str) {
    let content = if db_path.is_empty() {
        "none\n".to_string()
    } else {
        // ds_profiles/claude.db → base = ds_profiles/claude
        let base = db_path.trim_end_matches(".db");
        let app = base.rsplit('/').next().unwrap_or("unknown");
        format!("{}\n{}.a11y\n{}.snap\n", app, base, base)
    };
    let _ = fs::write(ACTIVE_FILE, content);
}

fn anim_t() -> f64 {
    let ms = START_TIME.get_or_init(Instant::now).elapsed().as_millis() as f64;
    (ms % LIGHT_PERIOD) / LIGHT_PERIOD
}

fn overlap(a: &RECT, b: &RECT) -> f64 {
    let ox = (a.right.min(b.right) - a.left.max(b.left)).max(0) as f64;
    let oy = (a.bottom.min(b.bottom) - a.top.max(b.top)).max(0) as f64;
    let area = (a.right - a.left) as f64 * (a.bottom - a.top) as f64;
    if area > 0.0 { ox * oy / area } else { 0.0 }
}

// Farbinterpolation für Gradient
fn lerp_clr(a: COLORREF, b: COLORREF, t: f64) -> COLORREF {
    let mix = |av: u32, bv: u32| -> u32 {
        (av as f64 + (bv as f64 - av as f64) * t).round() as u32
    };
    COLORREF(
        mix(a.0 & 0xFF, b.0 & 0xFF)
        | (mix((a.0 >> 8) & 0xFF, (b.0 >> 8) & 0xFF) << 8)
        | (mix((a.0 >> 16) & 0xFF, (b.0 >> 16) & 0xFF) << 16)
    )
}

// ── Shell-Fenster erkennen ─────────────────────────
unsafe fn is_shell(hwnd: HWND) -> bool {
    if hwnd == GetDesktopWindow() { return true; }
    let mut buf = [0u16; 64];
    let len = GetClassNameW(hwnd, &mut buf);
    if len == 0 { return false; }
    let cls = String::from_utf16_lossy(&buf[..len as usize]);
    matches!(cls.as_str(),
        "Progman" | "WorkerW" | "Shell_TrayWnd" |
        "Shell_SecondaryTrayWnd" | "SHELLDLL_DefView"
    )
}

// ── UI Automation: TitleBar-Höhe + Button-Offset ───
struct CaptionInfo {
    btn_offset: i32,
    bar_height: i32,
}

unsafe fn probe_caption(target: HWND) -> CaptionInfo {
    log(&format!("probe_caption: target=0x{:X}", target.0 as usize));
    let default = CaptionInfo { btn_offset: FALLBACK_BTN_X, bar_height: DEFAULT_TOP_H };

    let uia: IUIAutomation = match CoCreateInstance(
        &CUIAutomation8, None, CLSCTX_INPROC_SERVER,
    ) {
        Ok(u) => u,
        Err(e) => { log(&format!("probe_caption: CoCreateInstance FAILED: {e}")); return default; }
    };

    let elem = match uia.ElementFromHandle(target) {
        Ok(e) => e,
        Err(e) => { log(&format!("probe_caption: ElementFromHandle FAILED: {e}")); return default; }
    };

    let mut win_rc = RECT::default();
    let _ = GetWindowRect(target, &mut win_rc);
    let win_right = win_rc.right;
    let win_top = win_rc.top;

    // TitleBar finden (ControlType 50037)
    let cond = match uia.CreatePropertyCondition(
        UIA_ControlTypePropertyId, &VARIANT::from(50037i32),
    ) {
        Ok(c) => c,
        Err(_) => return default,
    };

    let titlebar = match elem.FindFirst(TreeScope_Descendants, &cond) {
        Ok(tb) => tb,
        Err(_) => return default,
    };

    // TitleBar-Höhe aus BoundingRectangle
    let bar_height = match titlebar.CurrentBoundingRectangle() {
        Ok(r) => {
            let h = r.bottom - r.top;
            // Manche Apps: TitleBar beginnt NICHT am Fenster-Top (Schatten/Border)
            // Also: Höhe = TitleBar.bottom - Window.top
            let full_h = r.bottom - win_top;
            full_h.max(h).max(DEFAULT_TOP_H).min(60)
        }
        Err(_) => DEFAULT_TOP_H,
    };

    // Buttons in der TitleBar finden (ControlType 50000)
    let btn_cond = match uia.CreatePropertyCondition(
        UIA_ControlTypePropertyId, &VARIANT::from(50000i32),
    ) {
        Ok(c) => c,
        Err(_) => return CaptionInfo { btn_offset: FALLBACK_BTN_X, bar_height },
    };

    let buttons = match titlebar.FindAll(TreeScope_Children, &btn_cond) {
        Ok(b) => b,
        Err(_) => return CaptionInfo { btn_offset: FALLBACK_BTN_X, bar_height },
    };

    let count = buttons.Length().unwrap_or(0);
    if count == 0 {
        return CaptionInfo { btn_offset: FALLBACK_BTN_X, bar_height };
    }

    let mut leftmost_x = win_right;
    for i in 0..count {
        if let Ok(btn) = buttons.GetElement(i) {
            if let Ok(rect) = btn.CurrentBoundingRectangle() {
                if rect.left < leftmost_x {
                    leftmost_x = rect.left;
                }
            }
        }
    }

    let btn_offset = win_right - leftmost_x;
    let result = CaptionInfo {
        btn_offset: if btn_offset > 0 && btn_offset < 400 { btn_offset } else { FALLBACK_BTN_X },
        bar_height,
    };
    log(&format!("probe_caption: btn_offset={}, bar_height={}", result.btn_offset, result.bar_height));
    result
}

// ── Accessibility Tree Engine ───────────────────────

fn role_name(ct: i32) -> &'static str {
    match ct {
        50000 => "Button",     50001 => "Calendar",   50002 => "CheckBox",
        50003 => "ComboBox",   50004 => "Edit",       50005 => "Hyperlink",
        50006 => "Image",      50007 => "ListItem",   50008 => "List",
        50009 => "Menu",       50010 => "MenuBar",    50011 => "MenuItem",
        50012 => "ProgressBar",50013 => "RadioButton",50014 => "ScrollBar",
        50015 => "Slider",     50016 => "Spinner",    50017 => "StatusBar",
        50018 => "Tab",        50019 => "TabItem",    50020 => "Text",
        50021 => "ToolBar",    50022 => "ToolTip",    50023 => "Tree",
        50024 => "TreeItem",   50025 => "Custom",     50026 => "Group",
        50027 => "Thumb",      50028 => "DataGrid",   50029 => "DataItem",
        50030 => "Document",   50031 => "SplitButton",50032 => "Window",
        50033 => "Pane",       50034 => "Header",     50035 => "HeaderItem",
        50036 => "Table",      50037 => "TitleBar",   50038 => "Separator",
        _ => "Unknown",
    }
}

unsafe fn get_value(elem: &IUIAutomationElement) -> String {
    if let Ok(pat) = elem.GetCurrentPattern(UIA_ValuePatternId) {
        if let Ok(vp) = pat.cast::<IUIAutomationValuePattern>() {
            if let Ok(val) = vp.CurrentValue() {
                return val.to_string();
            }
        }
    }
    String::new()
}


const TREE_TIMEOUT_MS: u64 = 2000;

// ── SQLite DB Setup ──────────────────────────────────
fn init_db(db_path: &str) -> Option<Connection> {
    let conn = match Connection::open(db_path) {
        Ok(c) => c,
        Err(e) => { log(&format!("init_db: FAILED: {e}")); return None; }
    };
    // auto_vacuum=FULL muss VOR der ersten Tabelle gesetzt werden.
    // Bei bestehender DB: einmalig VACUUM nötig um umzustellen.
    let av: i32 = conn.query_row("PRAGMA auto_vacuum", [], |r| r.get(0)).unwrap_or(0);
    if av != 1 {
        let _ = conn.execute_batch("PRAGMA auto_vacuum=FULL; VACUUM;");
    }
    let _ = conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;");
    let _ = conn.execute_batch("
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS elements (
            id            INTEGER PRIMARY KEY,
            parent_id     INTEGER,
            depth         INTEGER,
            role          TEXT NOT NULL,
            name          TEXT,
            value         TEXT,
            automation_id TEXT,
            enabled       INTEGER DEFAULT 1,
            offscreen     INTEGER DEFAULT 0,
            x             INTEGER,
            y             INTEGER,
            w             INTEGER,
            h             INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_role      ON elements(role);
        CREATE INDEX IF NOT EXISTS idx_offscreen ON elements(offscreen);
        CREATE INDEX IF NOT EXISTS idx_visible   ON elements(offscreen, role) WHERE offscreen=0;
        CREATE TABLE IF NOT EXISTS inject (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT DEFAULT 'text',
            text   TEXT NOT NULL,
            target TEXT DEFAULT '',
            done   INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp     INTEGER NOT NULL,
            event_type    TEXT NOT NULL,
            element_name  TEXT,
            element_role  TEXT,
            detail        TEXT,
            new_value     TEXT,
            consumed      INTEGER DEFAULT 0
        );
    ");
    // Migrations for pre-existing DBs
    let _ = conn.execute_batch("ALTER TABLE inject ADD COLUMN target TEXT DEFAULT '';");
    let _ = conn.execute_batch("ALTER TABLE inject ADD COLUMN action TEXT DEFAULT 'text';");
    log("init_db: OK");
    Some(conn)
}

// Streaming: Direkt in DB schreiben während Tree Walk
struct StreamCtx<'a> {
    conn: &'a Connection,
    count: i64,
    batch: i32,
}

unsafe fn stream_elements(
    ctx: &mut StreamCtx,
    elem: &IUIAutomationElement,
    walker: &IUIAutomationTreeWalker,
    parent_id: i64,
    depth: i32,
) {
    if depth > MAX_DEPTH { return; }

    let ct = elem.CurrentControlType().unwrap_or_default();
    let name = elem.CurrentName().ok().map(|s| s.to_string()).unwrap_or_default();
    let aid = elem.CurrentAutomationId().ok().map(|s| s.to_string()).unwrap_or_default();
    let enabled = elem.CurrentIsEnabled().map(|b| b.as_bool()).unwrap_or(true);
    let offscreen = elem.CurrentIsOffscreen().map(|b| b.as_bool()).unwrap_or(false);
    let rect = elem.CurrentBoundingRectangle().unwrap_or_default();
    let value = get_value(elem);

    ctx.count += 1;
    let my_id = ctx.count;

    let _ = ctx.conn.execute(
        "INSERT INTO elements(id,parent_id,depth,role,name,value,automation_id,enabled,offscreen,x,y,w,h) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13)",
        params![
            my_id, parent_id, depth,
            role_name(ct.0),
            if name.is_empty() { None } else { Some(&name) },
            if value.is_empty() { None } else { Some(&value) },
            if aid.is_empty() { None } else { Some(&aid) },
            enabled as i32, offscreen as i32,
            rect.left, rect.top,
            rect.right - rect.left, rect.bottom - rect.top
        ],
    );

    // Periodic commit: macht bisherige Daten sofort querybar
    ctx.batch += 1;
    if ctx.batch >= STREAM_BATCH {
        let _ = ctx.conn.execute_batch("COMMIT; BEGIN TRANSACTION;");
        ctx.batch = 0;
    }

    // Kinder (depth-first = obere Layer kommen zuerst)
    let mut child_count = 0i32;
    if let Ok(child) = walker.GetFirstChildElement(elem) {
        stream_elements(ctx, &child, walker, my_id, depth + 1);
        child_count += 1;
        let mut prev = child;
        loop {
            if child_count >= MAX_CHILDREN { break; }
            match walker.GetNextSiblingElement(&prev) {
                Ok(next) => {
                    stream_elements(ctx, &next, walker, my_id, depth + 1);
                    prev = next;
                    child_count += 1;
                }
                Err(_) => break,
            }
        }
    }
}

fn dump_tree() {
    if TREE_BUSY.compare_exchange(false, true, SeqCst, SeqCst).is_err() {
        return;
    }

    let target_raw = TARGET_HW.load(SeqCst);
    if target_raw == 0 {
        TREE_BUSY.store(false, SeqCst);
        return;
    }

    std::thread::spawn(move || {
        let t0 = Instant::now();

        unsafe {
            let _ = CoInitializeEx(None, COINIT_MULTITHREADED);

            let target = HWND(target_raw as *mut _);
            if !IsWindow(target).as_bool() {
                CoUninitialize();
                TREE_BUSY.store(false, SeqCst);
                return;
            }

            let uia: IUIAutomation = match CoCreateInstance(
                &CUIAutomation8, None, CLSCTX_INPROC_SERVER,
            ) {
                Ok(u) => u,
                Err(e) => {
                    log(&format!("dump[t]: CoCreate FAIL: {e}"));
                    CoUninitialize();
                    TREE_BUSY.store(false, SeqCst);
                    return;
                }
            };

            if let Ok(uia6) = uia.cast::<IUIAutomation6>() {
                let _ = uia6.SetConnectionTimeout(TREE_TIMEOUT_MS as u32);
            }

            let root = match uia.ElementFromHandle(target) {
                Ok(e) => e,
                Err(_) => {
                    CoUninitialize();
                    TREE_BUSY.store(false, SeqCst);
                    return;
                }
            };

            let walker = match uia.RawViewWalker() {
                Ok(w) => w,
                Err(_) => {
                    CoUninitialize();
                    TREE_BUSY.store(false, SeqCst);
                    return;
                }
            };

            let title = root.CurrentName().ok().map(|s| s.to_string()).unwrap_or_default();
            let mut win_rc = RECT::default();
            let _ = GetWindowRect(target, &mut win_rc);
            let ts = SystemTime::now()
                .duration_since(UNIX_EPOCH).unwrap_or_default().as_millis();

            // Streaming: Walk + INSERT gleichzeitig, COMMIT alle 200 Elemente
            let db_path = get_db_path();
            if db_path.is_empty() {
                CoUninitialize();
                TREE_BUSY.store(false, SeqCst);
                return;
            }
            if let Some(conn) = init_db(&db_path) {
                // DROP + CREATE statt DELETE → keine Freelist-Bloat
                let _ = conn.execute_batch("
                    DROP TABLE IF EXISTS elements;
                    DROP TABLE IF EXISTS meta;
                    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
                    CREATE TABLE elements (
                        id INTEGER PRIMARY KEY, parent_id INTEGER, depth INTEGER,
                        role TEXT NOT NULL, name TEXT, value TEXT, automation_id TEXT,
                        enabled INTEGER DEFAULT 1, offscreen INTEGER DEFAULT 0,
                        x INTEGER, y INTEGER, w INTEGER, h INTEGER
                    );
                ");

                // Meta
                let _ = conn.execute(
                    "INSERT INTO meta(key,value) VALUES('window',?1),('hwnd',?2),('timestamp',?3),('x',?4),('y',?5),('w',?6),('h',?7)",
                    params![title, format!("0x{:X}", target.0 as usize), ts.to_string(),
                        win_rc.left, win_rc.top,
                        win_rc.right - win_rc.left, win_rc.bottom - win_rc.top],
                );

                // Stream: Walk tree + INSERT in einem Rutsch
                let _ = conn.execute_batch("BEGIN TRANSACTION;");
                let mut ctx = StreamCtx { conn: &conn, count: 0, batch: 0 };
                stream_elements(&mut ctx, &root, &walker, 0, 0);
                let _ = conn.execute_batch("COMMIT;");

                let total_ms = t0.elapsed().as_millis();
                log(&format!("dump: {} rows streamed, total={}ms", ctx.count, total_ms));
                generate_snap(&db_path);
                generate_a11y(&db_path, target);
                generate_a11y_snap(&db_path);
                write_active_status(&db_path);
            }

            CoUninitialize();
        }
        TREE_BUSY.store(false, SeqCst);
    });
}

// ── Chromium Accessibility Trigger ───────────────────
// Chromium prüft 3 Dinge:
// 1. SPI_GETSCREENREADER — beim Start UND bei WM_SETTINGCHANGE
// 2. UiaClientsAreListening() — true wenn UIA Event Handler registriert sind
// 3. WM_GETOBJECT auf Chrome_RenderWidgetHostHWND — per-Renderer Aktivierung
// Wir müssen ALLE DREI triggern damit es auch bei bereits laufendem Browser klappt.

unsafe fn activate_accessibility(target: HWND) {
    log("activate_a11y: full activation sequence...");

    // ── Phase 1: System-Level Signal ──
    // Screen Reader Flag setzen + persistieren
    let _ = SystemParametersInfoW(
        SPI_SETSCREENREADER,
        1,
        None,
        SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS(0x0003), // SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
    );

    // WM_SETTINGCHANGE DIREKT an Target senden (nicht auf Broadcast warten)
    let _ = SendMessageW(
        target,
        WM_SETTINGCHANGE,
        WPARAM(SPI_SETSCREENREADER.0 as usize),
        LPARAM(0),
    );

    // ── Phase 2: UIA Event Handler registrieren ──
    // DAS ist der Schlüssel: UiaClientsAreListening() wird true
    // Chromium checkt das und aktiviert Accessibility für alle Renderer
    if let Ok(uia) = CoCreateInstance::<_, IUIAutomation>(&CUIAutomation8, None, CLSCTX_INPROC_SERVER) {
        // FocusChanged Handler auf Root — leichtgewichtig, signalisiert AT-Präsenz
        let handler: IUIAutomationFocusChangedEventHandler = UiaFocusHandler.into();
        let _ = uia.AddFocusChangedEventHandler(None, &handler);
        log("activate_a11y: UIA FocusChanged handler registered → UiaClientsAreListening() = true");

        // UIA Instanz + Handler am Leben halten (Leak = nie deregistriert)
        let _ = Box::leak(Box::new(uia));
    }

    // Kurz warten damit Chromium die Signale verarbeiten kann
    std::thread::sleep(std::time::Duration::from_millis(300));

    // ── Phase 3: MSAA + WM_GETOBJECT Probes ──
    // Jetzt wo UiaClientsAreListening() true ist, werden die Probes wirksam

    // Hauptfenster proben
    let mut acc: *mut c_void = std::ptr::null_mut();
    let _ = AccessibleObjectFromWindow(
        target,
        0xFFFFFFFC, // OBJID_CLIENT
        &IAccessible::IID,
        &mut acc,
    );

    // Alle Child-Windows proben — insbesondere Chrome_RenderWidgetHostHWND
    unsafe extern "system" fn probe_child(hwnd: HWND, _: LPARAM) -> BOOL {
        let mut acc: *mut c_void = std::ptr::null_mut();
        let _ = AccessibleObjectFromWindow(hwnd, 0xFFFFFFFC, &IAccessible::IID, &mut acc);
        let _ = SendMessageW(hwnd, WM_GETOBJECT, WPARAM(0), LPARAM(0xFFFFFFFC_u32 as i32 as isize));
        TRUE
    }

    let _ = EnumChildWindows(target, Some(probe_child), LPARAM(0));

    // ── Phase 4: Warten + Retry ──
    std::thread::sleep(std::time::Duration::from_millis(500));
    let _ = EnumChildWindows(target, Some(probe_child), LPARAM(0));

    log("activate_a11y: done — all 4 phases complete");
}

// Dummy UIA FocusChanged Handler — existiert nur damit UiaClientsAreListening() true ist
#[windows::core::implement(IUIAutomationFocusChangedEventHandler)]
struct UiaFocusHandler;

impl IUIAutomationFocusChangedEventHandler_Impl for UiaFocusHandler_Impl {
    fn HandleFocusChangedEvent(
        &self,
        _sender: Option<&IUIAutomationElement>,
    ) -> windows::core::Result<()> {
        Ok(()) // Noop — wir brauchen nur die Registrierung
    }
}

// ── UIA Live Event System ───────────────────────────
// Drei Handler: Automation Events, Property Changes, Structure Changes
// Events → SQLite `events` Tabelle → MCP liest nur Deltas

/// Write a single event row to the events table.
fn write_event(event_type: &str, elem_name: &str, elem_role: &str, detail: &str, new_val: &str) {
    let db_path = get_db_path();
    if db_path.is_empty() { return; }
    if let Ok(conn) = Connection::open(&db_path) {
        let _ = conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;");
        // Ensure events table exists (init_db might not have created it for pre-existing DBs)
        let _ = conn.execute_batch("
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp INTEGER NOT NULL,
                event_type TEXT NOT NULL, element_name TEXT, element_role TEXT,
                detail TEXT, new_value TEXT, consumed INTEGER DEFAULT 0
            );
        ");
        let ts = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as i64;
        let _ = conn.execute(
            "INSERT INTO events(timestamp,event_type,element_name,element_role,detail,new_value) \
             VALUES(?1,?2,?3,?4,?5,?6)",
            params![ts, event_type,
                if elem_name.is_empty() { None } else { Some(elem_name) },
                if elem_role.is_empty() { None } else { Some(elem_role) },
                detail,
                if new_val.is_empty() { None } else { Some(new_val) }],
        );
        // Prune: keep max 500 events
        let _ = conn.execute(
            "DELETE FROM events WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT 500)", [],
        );
    }
}

/// Debounced dump_tree trigger from event handlers.
/// Only fires if >500ms since last event-triggered dump.
fn event_trigger_dump() {
    let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as isize;
    let last = LAST_EVENT_DUMP_MS.load(SeqCst);
    if now - last > 500 {
        LAST_EVENT_DUMP_MS.store(now, SeqCst);
        dump_tree();
    }
}

/// Safely extract element name from UIA callback sender.
fn sender_name(sender: Option<&IUIAutomationElement>) -> String {
    sender.and_then(|e| unsafe { e.CurrentName().ok() })
        .map(|s| s.to_string()).unwrap_or_default()
}

/// Safely extract element role from UIA callback sender.
fn sender_role(sender: Option<&IUIAutomationElement>) -> String {
    sender.and_then(|e| unsafe { e.CurrentControlType().ok() })
        .map(|ct| role_name(ct.0).to_string()).unwrap_or_default()
}

// ── Handler 1: Automation Events (Window opened, Menu, Content loaded) ──

#[windows::core::implement(IUIAutomationEventHandler)]
struct DsEventHandler;

impl IUIAutomationEventHandler_Impl for DsEventHandler_Impl {
    fn HandleAutomationEvent(
        &self,
        sender: Option<&IUIAutomationElement>,
        eventid: UIA_EVENT_ID,
    ) -> windows::core::Result<()> {
        let name = sender_name(sender);
        let role = sender_role(sender);
        let event_name = match eventid.0 {
            20016 => "window_opened",
            20003 => "menu_opened",
            20006 => "content_loaded",
            other => { log(&format!("EVENT[auto]: unknown id={}", other)); return Ok(()); }
        };
        log(&format!("EVENT[auto]: {} on '{}' ({})", event_name, name, role));
        write_event("automation", &name, &role, event_name, "");

        // Content loaded = new tab ready → refresh tree (fixes tab-switch bug!)
        if eventid.0 == 20006 {
            event_trigger_dump();
        }
        Ok(())
    }
}

// ── Handler 2: Property Changes (Name, Value, ToggleState, IsEnabled) ──

#[windows::core::implement(IUIAutomationPropertyChangedEventHandler)]
struct DsPropertyHandler;

impl IUIAutomationPropertyChangedEventHandler_Impl for DsPropertyHandler_Impl {
    fn HandlePropertyChangedEvent(
        &self,
        sender: Option<&IUIAutomationElement>,
        propertyid: UIA_PROPERTY_ID,
        newvalue: &VARIANT,
    ) -> windows::core::Result<()> {
        let name = sender_name(sender);
        let role = sender_role(sender);
        let prop_name = match propertyid.0 {
            30005 => "Name",
            30045 => "Value",
            30086 => "ToggleState",
            30010 => "IsEnabled",
            _ => "unknown",
        };
        // Extract value from VARIANT (windows-rs 0.58 safe API)
        let val_str = if let Ok(s) = BSTR::try_from(newvalue) {
            s.to_string()
        } else if let Ok(i) = i32::try_from(newvalue) {
            format!("{}", i)
        } else if let Ok(b) = bool::try_from(newvalue) {
            if b { "true".into() } else { "false".into() }
        } else {
            "(unknown_type)".into()
        };
        log(&format!("EVENT[prop]: {}.{} = '{}' on '{}'", role, prop_name, val_str, name));
        write_event("property", &name, &role, prop_name, &val_str);
        Ok(())
    }
}

// ── Handler 3: Structure Changes (DOM mutations) ──

#[windows::core::implement(IUIAutomationStructureChangedEventHandler)]
struct DsStructureHandler;

impl IUIAutomationStructureChangedEventHandler_Impl for DsStructureHandler_Impl {
    fn HandleStructureChangedEvent(
        &self,
        sender: Option<&IUIAutomationElement>,
        changetype: StructureChangeType,
        _runtimeid: *const SAFEARRAY,
    ) -> windows::core::Result<()> {
        let name = sender_name(sender);
        let role = sender_role(sender);
        let change_name = match changetype.0 {
            0 => "child_added",
            1 => "child_removed",
            2 => "children_invalidated",
            3 => "children_bulk_added",
            4 => "children_bulk_removed",
            5 => "children_reordered",
            _ => "unknown",
        };
        log(&format!("EVENT[struct]: {} on '{}' ({})", change_name, name, role));
        write_event("structure", &name, &role, change_name, "");

        // Major structure changes → refresh tree (debounced)
        if changetype.0 == 2 || changetype.0 == 3 {
            event_trigger_dump();
        }
        Ok(())
    }
}

// ── Event Handler Registration / Cleanup ────────────

unsafe fn register_event_handlers(target: HWND) {
    log("register_events: starting...");

    let uia: IUIAutomation = match CoCreateInstance(&CUIAutomation8, None, CLSCTX_INPROC_SERVER) {
        Ok(u) => u,
        Err(e) => { log(&format!("register_events: CoCreate FAIL: {e}")); return; }
    };

    let root = match uia.ElementFromHandle(target) {
        Ok(e) => e,
        Err(e) => { log(&format!("register_events: ElementFromHandle FAIL: {e}")); return; }
    };

    let scope = TreeScope(7); // TreeScope_Subtree

    // 1. Automation Events
    let auto_handler: IUIAutomationEventHandler = DsEventHandler.into();
    for &eid in &[20016i32, 20003, 20006] { // WindowOpened, MenuOpened, AsyncContentLoaded
        match uia.AddAutomationEventHandler(UIA_EVENT_ID(eid), &root, scope, None, &auto_handler) {
            Ok(_) => log(&format!("register_events: automation event {} OK", eid)),
            Err(e) => log(&format!("register_events: automation event {} FAIL: {e}", eid)),
        }
    }

    // 2. Property Changed Events
    let prop_handler: IUIAutomationPropertyChangedEventHandler = DsPropertyHandler.into();
    let prop_ids = [
        UIA_PROPERTY_ID(30005), // Name
        UIA_PROPERTY_ID(30045), // Value
        UIA_PROPERTY_ID(30086), // ToggleState
        UIA_PROPERTY_ID(30010), // IsEnabled
    ];
    match uia.AddPropertyChangedEventHandlerNativeArray(&root, scope, None, &prop_handler, &prop_ids) {
        Ok(_) => log("register_events: property handler OK"),
        Err(e) => log(&format!("register_events: property handler FAIL: {e}")),
    }

    // 3. Structure Changed Events
    let struct_handler: IUIAutomationStructureChangedEventHandler = DsStructureHandler.into();
    match uia.AddStructureChangedEventHandler(&root, scope, None, &struct_handler) {
        Ok(_) => log("register_events: structure handler OK"),
        Err(e) => log(&format!("register_events: structure handler FAIL: {e}")),
    }

    // Store UIA instance for cleanup on unsnap
    let ptr = Box::into_raw(Box::new(uia)) as isize;
    EVENT_UIA_PTR.store(ptr, SeqCst);
    log("register_events: ALL handlers registered");
}

unsafe fn unregister_event_handlers() {
    let ptr = EVENT_UIA_PTR.swap(0, SeqCst);
    if ptr != 0 {
        let uia = Box::from_raw(ptr as *mut IUIAutomation);
        match uia.RemoveAllEventHandlers() {
            Ok(_) => log("unregister_events: all handlers removed"),
            Err(e) => log(&format!("unregister_events: FAIL: {e}")),
        }
        // uia drops here → COM Release
    }
}

// ── .snap File Generation ───────────────────────────

/// Map UI control role → input tool. None = not interactive.
fn input_tool(role: &str) -> Option<&'static str> {
    match role {
        "Edit" | "Document" => Some("keyboard"),
        "Button" | "Hyperlink" | "MenuItem" | "TabItem" | "ListItem"
        | "TreeItem" | "DataItem" | "SplitButton" => Some("click"),
        "CheckBox" | "RadioButton" => Some("toggle"),
        "ComboBox" => Some("select"),
        "Slider" => Some("slide"),
        "Spinner" => Some("spin"),
        _ => None,
    }
}

/// Generate .snap file from DB — lists all interactive elements with their input tool.
fn generate_snap(db_path: &str) {
    let snap_path = db_path.replace(".db", ".snap");

    let conn = match Connection::open(db_path) {
        Ok(c) => c,
        Err(_) => return,
    };
    let _ = conn.execute_batch("PRAGMA journal_mode=WAL;");

    let title: String = conn
        .query_row("SELECT value FROM meta WHERE key='window'", [], |r| r.get(0))
        .unwrap_or_default();

    let mut stmt = match conn.prepare(
        "SELECT role, name, automation_id, x, y, w, h FROM elements \
         WHERE enabled=1 AND offscreen=0 AND name IS NOT NULL AND name != '' \
         ORDER BY y, x",
    ) {
        Ok(s) => s,
        Err(_) => return,
    };

    let mut lines: Vec<String> = Vec::new();
    let snap_name = snap_path.split('/').last().unwrap_or("unknown");
    lines.push(format!("# {} — Generated by DirectShell", snap_name));
    lines.push(format!("# Window: {}", title));
    lines.push(String::new());

    let mut count = 0usize;
    let rows = stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2).unwrap_or_default(),
            row.get::<_, i32>(3)?,
            row.get::<_, i32>(4)?,
            row.get::<_, i32>(5)?,
            row.get::<_, i32>(6)?,
        ))
    });

    if let Ok(rows) = rows {
        for row in rows.flatten() {
            let (role, name, aid, x, y, w, h) = row;
            if let Some(tool) = input_tool(&role) {
                let mut line = format!("[{}] \"{}\" @ {},{} ({}x{})", tool, name, x, y, w, h);
                if !aid.is_empty() {
                    line.push_str(&format!(" id={}", aid));
                }
                lines.push(line);
                count += 1;
            }
        }
    }

    let content = lines.join("\n");
    let _ = fs::write(&snap_path, &content);
    log(&format!("snap: {} interactive elements → {}", count, snap_path));
}

// ── .a11y File Generation (Screen Reader View) ──────

/// Generate .a11y file — DB-based. Only GetFocusedElement() is live UIA.
/// Everything else comes from the SQLite dump that just ran.
fn generate_a11y(db_path: &str, _target: HWND) {
    let a11y_path = db_path.replace(".db", ".a11y");

    let conn = match Connection::open(db_path) {
        Ok(c) => c,
        Err(_) => return,
    };
    let _ = conn.execute_batch("PRAGMA journal_mode=WAL;");

    let title: String = conn
        .query_row("SELECT value FROM meta WHERE key='window'", [], |r| r.get(0))
        .unwrap_or_default();

    let mut lines: Vec<String> = Vec::new();
    let a11y_name = a11y_path.split('/').last().unwrap_or("unknown");
    lines.push(format!("# {} — Screen Reader View (DirectShell)", a11y_name));
    lines.push(format!("# Window: {}", title));
    lines.push(String::new());

    // 1. Focus — single live UIA call
    lines.push("## Focus".to_string());
    unsafe {
        if let Ok(uia) = CoCreateInstance::<_, IUIAutomation>(
            &CUIAutomation8, None, CLSCTX_INPROC_SERVER,
        ) {
            if let Ok(fe) = uia.GetFocusedElement() {
                let fname = fe.CurrentName().ok().map(|s| s.to_string()).unwrap_or_default();
                let fct = fe.CurrentControlType().unwrap_or_default();
                let frole = role_name(fct.0);
                let ftool = input_tool(frole).unwrap_or("interact");
                let frect = fe.CurrentBoundingRectangle().unwrap_or_default();
                let fval = get_value(&fe);
                lines.push(format!("[{}] \"{}\" @ {},{} ({}x{})",
                    ftool, fname, frect.left, frect.top,
                    frect.right - frect.left, frect.bottom - frect.top));
                if !fval.is_empty() {
                    let preview = if fval.len() > 100 { &fval[..100] } else { &fval };
                    lines.push(format!("  value: \"{}\"", preview));
                }
            } else {
                lines.push("(none)".to_string());
            }
        }
    }
    lines.push(String::new());

    // 2. Input Targets — from DB (Edit/Document with name + value)
    lines.push("## Input Targets".to_string());
    {
        let mut stmt = conn.prepare(
            "SELECT role, name, value, x, y, w, h FROM elements \
             WHERE enabled=1 AND offscreen=0 \
             AND name IS NOT NULL AND name != '' \
             AND w > 10 AND h > 10 \
             AND role IN ('Edit', 'Document', 'ComboBox') \
             ORDER BY y, x"
        ).ok();
        if let Some(ref mut st) = stmt {
            let rows = st.query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?,
                    row.get::<_, i32>(3)?,
                    row.get::<_, i32>(4)?,
                    row.get::<_, i32>(5)?,
                    row.get::<_, i32>(6)?,
                ))
            });
            if let Ok(rows) = rows {
                for row in rows.flatten() {
                    let (role, name, value, x, y, w, h) = row;
                    let tool = input_tool(&role).unwrap_or("keyboard");
                    lines.push(format!("[{}] \"{}\" @ {},{} ({}x{})", tool, name, x, y, w, h));
                    if let Some(ref v) = value {
                        if !v.is_empty() {
                            let preview = if v.len() > 100 { &v[..100] } else { v.as_str() };
                            lines.push(format!("  value: \"{}\"", preview));
                        }
                    }
                }
            }
        }
    }
    lines.push(String::new());

    // 3. Content — visible elements with names (from DB, no UIA walk)
    lines.push("## Content".to_string());
    {
        let mut stmt = conn.prepare(
            "SELECT name, value FROM elements \
             WHERE offscreen=0 \
             AND name IS NOT NULL AND name != '' \
             AND w > 20 AND h > 10 \
             AND role IN ('Text', 'Document', 'Hyperlink', 'Image', 'ListItem', 'TreeItem', 'DataItem', 'Group') \
             ORDER BY y, x"
        ).ok();
        if let Some(ref mut st) = stmt {
            let rows = st.query_map([], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, Option<String>>(1)?,
                ))
            });
            if let Ok(rows) = rows {
                for row in rows.flatten() {
                    let (name, value) = row;
                    if let Some(ref v) = value {
                        if !v.is_empty() && v != &name {
                            lines.push(format!("{} ({})", name, v));
                            continue;
                        }
                    }
                    lines.push(name);
                }
            }
        }
    }

    let content = lines.join("\n");
    let _ = fs::write(&a11y_path, &content);
}

// ── .a11y.snap File Generation (Operable Elements in Viewport) ──

/// Generate .a11y.snap from the EXISTING DB — no extra UIA calls.
/// Lists all interactive, visible, named elements the AI can operate.
fn generate_a11y_snap(db_path: &str) {
    let snap_path = db_path.replace(".db", ".a11y.snap");

    let conn = match Connection::open(db_path) {
        Ok(c) => c,
        Err(_) => return,
    };
    let _ = conn.execute_batch("PRAGMA journal_mode=WAL;");

    let title: String = conn
        .query_row("SELECT value FROM meta WHERE key='window'", [], |r| r.get(0))
        .unwrap_or_default();

    let mut stmt = match conn.prepare(
        "SELECT role, name, x, y, w, h FROM elements \
         WHERE enabled=1 AND offscreen=0 \
         AND name IS NOT NULL AND name != '' \
         AND w > 10 AND h > 10 \
         ORDER BY y, x",
    ) {
        Ok(s) => s,
        Err(_) => return,
    };

    let mut lines: Vec<String> = Vec::new();
    let fname = snap_path.split('/').last().unwrap_or("unknown");
    lines.push(format!("# {} — Operable Elements (DirectShell)", fname));
    lines.push(format!("# Window: {}", title));
    lines.push(format!("# Use 'target' column in inject table to aim at an element by name"));
    lines.push(String::new());

    let mut idx = 0u32;
    let rows = stmt.query_map([], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, i32>(2)?,
            row.get::<_, i32>(3)?,
            row.get::<_, i32>(4)?,
            row.get::<_, i32>(5)?,
        ))
    });

    if let Ok(rows) = rows {
        for row in rows.flatten() {
            let (role, name, x, y, w, h) = row;
            if let Some(tool) = input_tool(&role) {
                idx += 1;
                lines.push(format!("[{}] [{}] \"{}\" @ {},{} ({}x{})",
                    idx, tool, name, x, y, w, h));
            }
        }
    }

    lines.push(String::new());
    lines.push(format!("# {} operable elements in viewport", idx));

    let content = lines.join("\n");
    let _ = fs::write(&snap_path, &content);
}

// ── Injection Pipeline (External → App) ─────────────

/// Inject text into the target app — screen reader style.
/// Reads .a11y.snap to know WHAT can be operated.
/// `target_name`: element name from .a11y.snap (e.g. "Einen Prompt für Gemini eingeben")
///   If empty: falls back to first focusable+value element (legacy).
unsafe fn inject_text(target: HWND, text: &str, target_name: &str) -> bool {
    let uia: IUIAutomation = match CoCreateInstance(
        &CUIAutomation8, None, CLSCTX_INPROC_SERVER,
    ) {
        Ok(u) => u,
        Err(e) => { log(&format!("inject: CoCreate FAIL: {e}")); return false; }
    };

    let root = match uia.ElementFromHandle(target) {
        Ok(e) => e,
        Err(e) => { log(&format!("inject: ElementFromHandle FAIL: {e}")); return false; }
    };

    // Base conditions: focusable + accepts value
    let cond_focus = match uia.CreatePropertyCondition(
        UIA_IsKeyboardFocusablePropertyId, &VARIANT::from(true),
    ) {
        Ok(c) => c,
        Err(e) => { log(&format!("inject: cond_focus FAIL: {e}")); return false; }
    };
    let cond_value = match uia.CreatePropertyCondition(
        UIA_IsValuePatternAvailablePropertyId, &VARIANT::from(true),
    ) {
        Ok(c) => c,
        Err(e) => { log(&format!("inject: cond_value FAIL: {e}")); return false; }
    };
    let base_cond = match uia.CreateAndCondition(&cond_focus, &cond_value) {
        Ok(c) => c,
        Err(e) => { log(&format!("inject: AndCondition FAIL: {e}")); return false; }
    };

    // If target_name given: add Name condition for precision targeting
    let cond: IUIAutomationCondition = if !target_name.is_empty() {
        let cond_name = match uia.CreatePropertyCondition(
            UIA_NamePropertyId, &VARIANT::from(BSTR::from(target_name)),
        ) {
            Ok(c) => c,
            Err(e) => { log(&format!("inject: cond_name FAIL: {e}")); return false; }
        };
        match uia.CreateAndCondition(&base_cond, &cond_name) {
            Ok(c) => c.cast().unwrap(),
            Err(e) => { log(&format!("inject: name+base FAIL: {e}")); return false; }
        }
    } else {
        base_cond.cast().unwrap()
    };

    let elem = match root.FindFirst(TreeScope_Descendants, &cond) {
        Ok(e) => e,
        Err(e) => {
            log(&format!("inject: FindFirst FAIL (target='{}'): {e}", target_name));
            return false;
        }
    };

    let name = elem.CurrentName().ok().map(|s| s.to_string()).unwrap_or_default();
    let ct = elem.CurrentControlType().unwrap_or_default();
    log(&format!("inject: target='{}' ct={}", name, ct.0));

    // Focus it — like a screen reader navigating with Tab
    let _ = elem.SetFocus();

    // Strategy 1: ValuePattern (direct text set)
    if let Ok(pat) = elem.GetCurrentPattern(UIA_ValuePatternId) {
        if let Ok(vp) = pat.cast::<IUIAutomationValuePattern>() {
            let current = vp.CurrentValue().ok()
                .map(|s| s.to_string()).unwrap_or_default();
            let combined = format!("{}{}", current, text);
            let bstr = BSTR::from(combined.as_str());
            if vp.SetValue(&bstr).is_ok() {
                log(&format!("inject: ValuePattern OK, len={}", combined.len()));
                return true;
            }
        }
    }

    // Strategy 2: SendInput to the now-focused element
    log("inject: ValuePattern failed, using SendInput");
    let our_tid = GetCurrentThreadId();
    let target_tid = GetWindowThreadProcessId(target, None);
    let _ = AttachThreadInput(our_tid, target_tid, TRUE);
    for ch in text.chars() {
        inject_char(ch);
    }
    let _ = AttachThreadInput(our_tid, target_tid, FALSE);
    log("inject: SendInput done");
    true
}

/// Map a key name to its VK code. Covers all 150+ keyboard keys.
fn key_to_vk(name: &str) -> Option<VIRTUAL_KEY> {
    match name.to_lowercase().as_str() {
        // Letters
        "a" => Some(VIRTUAL_KEY(0x41)), "b" => Some(VIRTUAL_KEY(0x42)),
        "c" => Some(VIRTUAL_KEY(0x43)), "d" => Some(VIRTUAL_KEY(0x44)),
        "e" => Some(VIRTUAL_KEY(0x45)), "f" => Some(VIRTUAL_KEY(0x46)),
        "g" => Some(VIRTUAL_KEY(0x47)), "h" => Some(VIRTUAL_KEY(0x48)),
        "i" => Some(VIRTUAL_KEY(0x49)), "j" => Some(VIRTUAL_KEY(0x4A)),
        "k" => Some(VIRTUAL_KEY(0x4B)), "l" => Some(VIRTUAL_KEY(0x4C)),
        "m" => Some(VIRTUAL_KEY(0x4D)), "n" => Some(VIRTUAL_KEY(0x4E)),
        "o" => Some(VIRTUAL_KEY(0x4F)), "p" => Some(VIRTUAL_KEY(0x50)),
        "q" => Some(VIRTUAL_KEY(0x51)), "r" => Some(VIRTUAL_KEY(0x52)),
        "s" => Some(VIRTUAL_KEY(0x53)), "t" => Some(VIRTUAL_KEY(0x54)),
        "u" => Some(VIRTUAL_KEY(0x55)), "v" => Some(VIRTUAL_KEY(0x56)),
        "w" => Some(VIRTUAL_KEY(0x57)), "x" => Some(VIRTUAL_KEY(0x58)),
        "y" => Some(VIRTUAL_KEY(0x59)), "z" => Some(VIRTUAL_KEY(0x5A)),
        // Numbers
        "0" => Some(VIRTUAL_KEY(0x30)), "1" => Some(VIRTUAL_KEY(0x31)),
        "2" => Some(VIRTUAL_KEY(0x32)), "3" => Some(VIRTUAL_KEY(0x33)),
        "4" => Some(VIRTUAL_KEY(0x34)), "5" => Some(VIRTUAL_KEY(0x35)),
        "6" => Some(VIRTUAL_KEY(0x36)), "7" => Some(VIRTUAL_KEY(0x37)),
        "8" => Some(VIRTUAL_KEY(0x38)), "9" => Some(VIRTUAL_KEY(0x39)),
        // Function keys
        "f1"  => Some(VK_F1),  "f2"  => Some(VK_F2),  "f3"  => Some(VK_F3),
        "f4"  => Some(VK_F4),  "f5"  => Some(VK_F5),  "f6"  => Some(VK_F6),
        "f7"  => Some(VK_F7),  "f8"  => Some(VK_F8),  "f9"  => Some(VK_F9),
        "f10" => Some(VK_F10), "f11" => Some(VK_F11), "f12" => Some(VK_F12),
        // Modifiers
        "ctrl" | "control" => Some(VK_CONTROL),
        "alt" | "menu"     => Some(VK_MENU),
        "shift"            => Some(VK_SHIFT),
        "win" | "lwin"     => Some(VK_LWIN),
        "rwin"             => Some(VK_RWIN),
        // Navigation
        "enter" | "return" => Some(VK_RETURN),
        "tab"              => Some(VK_TAB),
        "escape" | "esc"   => Some(VK_ESCAPE),
        "space"            => Some(VK_SPACE),
        "backspace" | "bs" => Some(VK_BACK),
        "delete" | "del"   => Some(VK_DELETE),
        "insert" | "ins"   => Some(VK_INSERT),
        "home"             => Some(VK_HOME),
        "end"              => Some(VK_END),
        "pageup" | "pgup"  => Some(VK_PRIOR),
        "pagedown" | "pgdn"=> Some(VK_NEXT),
        // Arrow keys
        "up"    => Some(VK_UP),
        "down"  => Some(VK_DOWN),
        "left"  => Some(VK_LEFT),
        "right" => Some(VK_RIGHT),
        // Special keys
        "printscreen" | "prtsc" => Some(VK_SNAPSHOT),
        "scrolllock"            => Some(VK_SCROLL),
        "pause" | "break"       => Some(VK_PAUSE),
        "numlock"               => Some(VK_NUMLOCK),
        "capslock" | "caps"     => Some(VK_CAPITAL),
        // Punctuation / symbols
        ";" | "semicolon"       => Some(VK_OEM_1),
        "=" | "equals"          => Some(VK_OEM_PLUS),
        "," | "comma"           => Some(VK_OEM_COMMA),
        "-" | "minus"           => Some(VK_OEM_MINUS),
        "." | "period"          => Some(VK_OEM_PERIOD),
        "/" | "slash"           => Some(VK_OEM_2),
        "`" | "backtick"        => Some(VK_OEM_3),
        "[" | "lbracket"        => Some(VK_OEM_4),
        "\\" | "backslash"      => Some(VK_OEM_5),
        "]" | "rbracket"        => Some(VK_OEM_6),
        "'" | "quote"           => Some(VK_OEM_7),
        // Numpad
        "num0" => Some(VK_NUMPAD0), "num1" => Some(VK_NUMPAD1),
        "num2" => Some(VK_NUMPAD2), "num3" => Some(VK_NUMPAD3),
        "num4" => Some(VK_NUMPAD4), "num5" => Some(VK_NUMPAD5),
        "num6" => Some(VK_NUMPAD6), "num7" => Some(VK_NUMPAD7),
        "num8" => Some(VK_NUMPAD8), "num9" => Some(VK_NUMPAD9),
        "multiply" | "num*" => Some(VK_MULTIPLY),
        "add"      | "num+" => Some(VK_ADD),
        "subtract" | "num-" => Some(VK_SUBTRACT),
        "decimal"  | "num." => Some(VK_DECIMAL),
        "divide"   | "num/" => Some(VK_DIVIDE),
        // Media
        "volumeup"   => Some(VK_VOLUME_UP),
        "volumedown" => Some(VK_VOLUME_DOWN),
        "volumemute" => Some(VK_VOLUME_MUTE),
        "nexttrack"  => Some(VK_MEDIA_NEXT_TRACK),
        "prevtrack"  => Some(VK_MEDIA_PREV_TRACK),
        "playpause"  => Some(VK_MEDIA_PLAY_PAUSE),
        "stop"       => Some(VK_MEDIA_STOP),
        _ => None,
    }
}

/// Extended flag needed for certain keys (arrows, ins/del/home/end/pgup/pgdn, numlock, right-ctrl/alt)
fn is_extended_key(vk: VIRTUAL_KEY) -> bool {
    matches!(vk, VK_UP | VK_DOWN | VK_LEFT | VK_RIGHT
        | VK_INSERT | VK_DELETE | VK_HOME | VK_END | VK_PRIOR | VK_NEXT
        | VK_NUMLOCK | VK_SNAPSHOT | VK_RWIN
        | VK_DIVIDE)
}

/// Send a single VK key down+up via SendInput
unsafe fn send_vk(vk: VIRTUAL_KEY) {
    let ext = if is_extended_key(vk) { KEYEVENTF_EXTENDEDKEY } else { KEYBD_EVENT_FLAGS(0) };
    let inputs = [
        INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: vk, wScan: 0,
                    dwFlags: ext,
                    time: 0, dwExtraInfo: 0,
                },
            },
        },
        INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: vk, wScan: 0,
                    dwFlags: ext | KEYEVENTF_KEYUP,
                    time: 0, dwExtraInfo: 0,
                },
            },
        },
    ];
    SendInput(&inputs, mem::size_of::<INPUT>() as i32);
}

/// Send a VK modifier key DOWN only
unsafe fn send_vk_down(vk: VIRTUAL_KEY) {
    let ext = if is_extended_key(vk) { KEYEVENTF_EXTENDEDKEY } else { KEYBD_EVENT_FLAGS(0) };
    let input = [INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: vk, wScan: 0,
                dwFlags: ext,
                time: 0, dwExtraInfo: 0,
            },
        },
    }];
    SendInput(&input, mem::size_of::<INPUT>() as i32);
}

/// Send a VK modifier key UP only
unsafe fn send_vk_up(vk: VIRTUAL_KEY) {
    let ext = if is_extended_key(vk) { KEYEVENTF_EXTENDEDKEY } else { KEYBD_EVENT_FLAGS(0) };
    let input = [INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: vk, wScan: 0,
                dwFlags: ext | KEYEVENTF_KEYUP,
                time: 0, dwExtraInfo: 0,
            },
        },
    }];
    SendInput(&input, mem::size_of::<INPUT>() as i32);
}

/// Parse and send a key combo like "ctrl+shift+a" or "enter" or "f5"
/// Supports any combination of modifiers + one main key.
unsafe fn send_key_combo(combo: &str) {
    let parts: Vec<&str> = combo.split('+').map(|s| s.trim()).collect();
    let mut modifiers: Vec<VIRTUAL_KEY> = Vec::new();
    let mut main_key: Option<VIRTUAL_KEY> = None;

    for part in &parts {
        if let Some(vk) = key_to_vk(part) {
            if matches!(vk, VK_CONTROL | VK_MENU | VK_SHIFT | VK_LWIN | VK_RWIN) {
                modifiers.push(vk);
            } else {
                main_key = Some(vk);
            }
        } else {
            log(&format!("key: unknown key '{}'", part));
            return;
        }
    }

    // Press modifiers down
    for &m in &modifiers { send_vk_down(m); }
    // Press main key (or if only modifier, press the last modifier as key)
    if let Some(mk) = main_key {
        send_vk(mk);
    }
    // Release modifiers in reverse
    for &m in modifiers.iter().rev() { send_vk_up(m); }

    log(&format!("key: sent '{}'", combo));
}

/// Click on a UI element by name using UIA. Finds element, gets center, sends mouse click.
unsafe fn click_element(target_hwnd: HWND, element_name: &str) -> bool {
    let uia: IUIAutomation = match CoCreateInstance(
        &CUIAutomation8, None, CLSCTX_INPROC_SERVER,
    ) {
        Ok(u) => u,
        Err(e) => { log(&format!("click: CoCreate FAIL: {e}")); return false; }
    };

    let root = match uia.ElementFromHandle(target_hwnd) {
        Ok(e) => e,
        Err(e) => { log(&format!("click: ElementFromHandle FAIL: {e}")); return false; }
    };

    let cond = match uia.CreatePropertyCondition(
        UIA_NamePropertyId, &VARIANT::from(BSTR::from(element_name)),
    ) {
        Ok(c) => c,
        Err(e) => { log(&format!("click: cond FAIL: {e}")); return false; }
    };

    let elem = match root.FindFirst(TreeScope_Descendants, &cond) {
        Ok(e) => e,
        Err(e) => {
            log(&format!("click: FindFirst FAIL ('{}'): {e}", element_name));
            return false;
        }
    };

    let rect = match elem.CurrentBoundingRectangle() {
        Ok(r) => r,
        Err(e) => { log(&format!("click: rect FAIL: {e}")); return false; }
    };

    // Center of element
    let cx = rect.left + (rect.right - rect.left) / 2;
    let cy = rect.top + (rect.bottom - rect.top) / 2;

    // Convert to absolute coords for SendInput (0-65535 range)
    let screen_w = GetSystemMetrics(SM_CXSCREEN);
    let screen_h = GetSystemMetrics(SM_CYSCREEN);
    let abs_x = (cx as i32 * 65535 / screen_w) as i32;
    let abs_y = (cy as i32 * 65535 / screen_h) as i32;

    let inputs = [
        INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: abs_x,
                    dy: abs_y,
                    mouseData: 0,
                    dwFlags: MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTDOWN,
                    time: 0, dwExtraInfo: 0,
                },
            },
        },
        INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: abs_x,
                    dy: abs_y,
                    mouseData: 0,
                    dwFlags: MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE | MOUSEEVENTF_LEFTUP,
                    time: 0, dwExtraInfo: 0,
                },
            },
        },
    ];
    SendInput(&inputs, mem::size_of::<INPUT>() as i32);
    log(&format!("click: '{}' @ {},{}", element_name, cx, cy));
    true
}

/// Scroll the target window (up/down/left/right)
unsafe fn scroll_window(target_hwnd: HWND, direction: &str) {
    let (dx, dy): (i32, i32) = match direction.to_lowercase().as_str() {
        "up"    => (0, 120),    // WHEEL_DELTA = 120
        "down"  => (0, -120),
        "left"  => (-120, 0),
        "right" => (120, 0),
        _ => { log(&format!("scroll: unknown direction '{}'", direction)); return; }
    };

    // Get center of target window for scroll position
    let mut rect = RECT::default();
    let _ = GetWindowRect(target_hwnd, &mut rect);
    let cx = rect.left + (rect.right - rect.left) / 2;
    let cy = rect.top + (rect.bottom - rect.top) / 2;

    let screen_w = GetSystemMetrics(SM_CXSCREEN);
    let screen_h = GetSystemMetrics(SM_CYSCREEN);
    let abs_x = (cx * 65535 / screen_w) as i32;
    let abs_y = (cy * 65535 / screen_h) as i32;

    if dy != 0 {
        let input = [INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: abs_x, dy: abs_y,
                    mouseData: dy as u32,
                    dwFlags: MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE | MOUSEEVENTF_WHEEL,
                    time: 0, dwExtraInfo: 0,
                },
            },
        }];
        SendInput(&input, mem::size_of::<INPUT>() as i32);
    }
    if dx != 0 {
        let input = [INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx: abs_x, dy: abs_y,
                    mouseData: dx as u32,
                    dwFlags: MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE | MOUSEEVENTF_HWHEEL,
                    time: 0, dwExtraInfo: 0,
                },
            },
        }];
        SendInput(&input, mem::size_of::<INPUT>() as i32);
    }
    log(&format!("scroll: {}", direction));
}

/// Process the action queue. Dispatches: text, key, click, scroll.
/// Only runs when target app has foreground focus — won't steal focus from user.
fn process_injections() {
    static BUSY: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    // Re-entry guard: COM calls in click_element can pump messages,
    // causing WM_TIMER to fire re-entrantly. This prevents double execution.
    if BUSY.swap(true, SeqCst) { return; }

    let db_path = get_db_path();
    if db_path.is_empty() { BUSY.store(false, SeqCst); return; }

    let conn = match Connection::open(&db_path) {
        Ok(c) => c,
        Err(_) => { BUSY.store(false, SeqCst); return; },
    };
    let _ = conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=500;");

    // Read ONE pending action (FIFO)
    let row: Option<(i64, String, String, String)> = conn
        .query_row(
            "SELECT id, COALESCE(action,'text'), text, COALESCE(target,'') \
             FROM inject WHERE done=0 ORDER BY id LIMIT 1",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .ok();

    if let Some((id, action, text, target_name)) = row {
        // Claim IMMEDIATELY — retry up to 3 times if DB is locked.
        let mut claimed = false;
        for _ in 0..3 {
            if conn.execute("UPDATE inject SET done=1 WHERE id=?1", params![id]).is_ok() {
                claimed = true;
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        if !claimed {
            log(&format!("action: FAILED to claim id={} — skipping", id));
            BUSY.store(false, SeqCst);
            return;
        }

        log(&format!("action: id={} type='{}' target='{}' text='{}'",
            id, action, target_name, if text.len() > 50 { &text[..50] } else { &text }));

        // Auto-focus: bring target to foreground ONLY when there's work to do
        unsafe {
            let fg = GetForegroundWindow();
            let target = HWND(TARGET_HW.load(SeqCst) as *mut _);
            if !target.0.is_null() && fg != target && GetAncestor(fg, GA_ROOT) != target {
                send_vk_down(VK_MENU);
                send_vk_up(VK_MENU);
                let _ = SetForegroundWindow(target);
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
        }

        let ok = unsafe {
            let target = HWND(TARGET_HW.load(SeqCst) as *mut _);
            if target.0.is_null() && action != "key" {
                log("action: no target window");
                false
            } else {
                match action.as_str() {
                    "text" => inject_text(target, &text, &target_name),
                    "type" => {
                        for ch in text.chars() {
                            match ch {
                                '\t' => send_vk(VK_TAB),
                                '\n' | '\r' => send_vk(VK_RETURN),
                                _ => inject_char(ch),
                            }
                            std::thread::sleep(std::time::Duration::from_millis(15));
                        }
                        true
                    },
                    "key"  => { send_key_combo(&text); true },
                    "click" => click_element(target, &target_name),
                    "scroll" => { scroll_window(target, &text); true },
                    _ => { log(&format!("action: unknown type '{}'", action)); false }
                }
            }
        };

        if ok {
            log(&format!("action: done id={}", id));
        } else {
            let _ = conn.execute("UPDATE inject SET done=0 WHERE id=?1", params![id]);
            log(&format!("action: FAILED id={} — will retry", id));
        }
    }
    BUSY.store(false, SeqCst);
}

// ── Keyboard Hook (Input Proxy) ─────────────────────

/// Identity transform (POC uppercase removed)
fn transform_char(ch: char) -> char {
    ch
}

/// Inject a single Unicode character into the focused window via SendInput
unsafe fn inject_char(ch: char) {
    let code = ch as u16;
    let inputs = [
        INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: VIRTUAL_KEY(0),
                    wScan: code,
                    dwFlags: KEYEVENTF_UNICODE,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        },
        INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: VIRTUAL_KEY(0),
                    wScan: code,
                    dwFlags: KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        },
    ];
    SendInput(&inputs, mem::size_of::<INPUT>() as i32);
}

/// Low-level keyboard hook callback
/// Intercepts keystrokes when snapped + target has focus.
/// Blocks the original, transforms the character, injects the result.
unsafe extern "system" fn kb_hook_proc(code: i32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    let hook = HHOOK(KB_HOOK.load(SeqCst) as *mut _);

    // Negative code = must pass through per contract
    if code < 0 {
        return CallNextHookEx(hook, code, wp, lp);
    }

    // Only intercept when snapped
    if !snapped() {
        return CallNextHookEx(hook, code, wp, lp);
    }

    let kbd = &*(lp.0 as *const KBDLLHOOKSTRUCT);

    // Skip injected keys (our own output) — LLKHF_INJECTED = 0x10
    if kbd.flags.0 & 0x10 != 0 {
        return CallNextHookEx(hook, code, wp, lp);
    }

    // Only intercept when target app has focus
    let fg = GetForegroundWindow();
    let target = tgt();
    if target.0.is_null() {
        return CallNextHookEx(hook, code, wp, lp);
    }
    if fg != target && GetAncestor(fg, GA_ROOT) != target {
        return CallNextHookEx(hook, code, wp, lp);
    }

    // Preserve Ctrl/Alt shortcuts (copy, paste, undo, etc.)
    if GetAsyncKeyState(VK_CONTROL.0 as i32) < 0 || GetAsyncKeyState(VK_MENU.0 as i32) < 0 {
        return CallNextHookEx(hook, code, wp, lp);
    }

    let msg = wp.0 as u32;
    let vk = kbd.vkCode;

    // Non-character keys — ALWAYS pass through, no ToUnicode needed
    let vk_key = VIRTUAL_KEY(vk as u16);
    if matches!(vk_key,
        VK_RETURN | VK_BACK | VK_TAB | VK_ESCAPE | VK_DELETE | VK_INSERT |
        VK_HOME | VK_END | VK_PRIOR | VK_NEXT |
        VK_UP | VK_DOWN | VK_LEFT | VK_RIGHT |
        VK_F1 | VK_F2 | VK_F3 | VK_F4 | VK_F5 | VK_F6 |
        VK_F7 | VK_F8 | VK_F9 | VK_F10 | VK_F11 | VK_F12
    ) {
        return CallNextHookEx(hook, code, wp, lp);
    }

    // Build keyboard state for ToUnicode
    let mut kb_state = [0u8; 256];
    if GetAsyncKeyState(VK_SHIFT.0 as i32) < 0 { kb_state[0x10] = 0x80; }
    if GetAsyncKeyState(VK_LSHIFT.0 as i32) < 0 { kb_state[0xA0] = 0x80; }
    if GetAsyncKeyState(VK_RSHIFT.0 as i32) < 0 { kb_state[0xA1] = 0x80; }
    if GetAsyncKeyState(VK_CAPITAL.0 as i32) & 1 != 0 { kb_state[0x14] = 0x01; }

    // Try converting virtual key → Unicode character
    let mut buf = [0u16; 4];
    let n = ToUnicode(vk, kbd.scanCode, Some(&kb_state), &mut buf, 0);

    // n <= 0 = dead key or no translation → pass through
    if n <= 0 {
        return CallNextHookEx(hook, code, wp, lp);
    }

    // It's a printable character — intercept it
    if msg == WM_KEYDOWN {
        for i in 0..n as usize {
            if let Some(ch) = char::from_u32(buf[i] as u32) {
                let out = transform_char(ch);
                inject_char(out);
            }
        }
    }
    // Block both WM_KEYDOWN and WM_KEYUP for intercepted keys
    LRESULT(1)
}

// ── Snap-Ziel finden ────────────────────────────────
unsafe fn find_snap(me: HWND) -> Option<HWND> {
    let mut rc = RECT::default();
    let _ = GetWindowRect(me, &mut rc);
    let pt = POINT { x: (rc.left + rc.right) / 2, y: (rc.top + rc.bottom) / 2 };
    let _ = ShowWindow(me, SW_HIDE);
    let hit = WindowFromPoint(pt);
    let _ = ShowWindow(me, SW_SHOWNA);
    if hit.0.is_null() { return None; }
    let top = GetAncestor(hit, GA_ROOT);
    if top.0.is_null() || top == me { return None; }
    if !IsWindowVisible(top).as_bool() { return None; }
    if is_shell(top) { return None; }
    let mut trc = RECT::default();
    let _ = GetWindowRect(top, &mut trc);
    if overlap(&rc, &trc) >= SNAP_THRESH { Some(top) } else { None }
}

// ── Snap / Unsnap ───────────────────────────────────
unsafe fn do_snap(me: HWND, target: HWND) {
    log(&format!("do_snap: me=0x{:X} target=0x{:X}", me.0 as usize, target.0 as usize));
    let mut rc = RECT::default();
    let _ = GetWindowRect(target, &mut rc);
    let (x, y, w, h) = (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top);
    log(&format!("do_snap: target rect x={} y={} w={} h={}", x, y, w, h));
    // Owner setzen: Windows hält owned windows IMMER über ihrem Owner
    let _ = SetWindowLongPtrW(me, WINDOW_LONG_PTR_INDEX(-8), target.0 as isize);
    // TOPMOST entfernen + positionieren
    let _ = SetWindowPos(me, HWND_NOTOPMOST, x, y, w, h, SWP_NOACTIVATE);
    TARGET_HW.store(target.0 as isize, SeqCst);
    IS_SNAPPED.store(true, SeqCst);
    save(x, y, w, h);

    // UIA: TitleBar-Höhe + Button-Position auslesen
    let info = probe_caption(target);
    BTN_OFF_X.store(info.btn_offset, SeqCst);
    DYN_TOP_H.store(info.bar_height, SeqCst);

    // Persistente App-DB: Fenstertitel → Dateiname
    {
        let mut buf = [0u16; 256];
        let len = GetWindowTextW(target, &mut buf);
        let title = String::from_utf16_lossy(&buf[..len as usize]);
        let db_path = db_name_from_title(&title);
        let _ = fs::create_dir_all(DB_DIR);
        set_db_path(&db_path);
        log(&format!("do_snap: app db = {}", db_path));
    }

    // MSAA-Probe: Chromium Accessibility Tree aktivieren
    activate_accessibility(target);

    // Live Event Handlers registrieren (Property/Structure/Automation)
    register_event_handlers(target);

    let _ = KillTimer(me, ANIM_TIMER);
    let _ = SetTimer(me, SYNC_TIMER, TIMER_MS, None);
    let _ = SetTimer(me, TREE_TIMER, TREE_MS, None);
    let _ = SetTimer(me, INJECT_TIMER, INJECT_MS, None);
    log("do_snap: first tree dump...");
    dump_tree();
    log("do_snap: COMPLETE");
    let _ = InvalidateRect(me, None, TRUE);
}

unsafe fn do_unsnap(me: HWND) {
    log("do_unsnap: START");
    let _ = KillTimer(me, SYNC_TIMER);
    let _ = KillTimer(me, TREE_TIMER);
    let _ = KillTimer(me, INJECT_TIMER);
    // Event Handler deregistrieren (separate UIA Instanz)
    unregister_event_handlers();
    // DB bleibt persistent! Nur Pfad leeren.
    set_db_path("");
    write_active_status("");
    IS_SNAPPED.store(false, SeqCst);
    TARGET_HW.store(0, SeqCst);
    DYN_TOP_H.store(DEFAULT_TOP_H, SeqCst);
    // Owner entfernen + TOPMOST wiederherstellen + Startgröße
    let _ = SetWindowLongPtrW(me, WINDOW_LONG_PTR_INDEX(-8), 0);
    let mut rc = RECT::default();
    let _ = GetWindowRect(me, &mut rc);
    let _ = SetWindowPos(me, HWND_TOPMOST, rc.left, rc.top, INIT_W, INIT_H, SWP_NOACTIVATE);
    let _ = SetTimer(me, ANIM_TIMER, ANIM_MS, None);
    log("do_unsnap: COMPLETE");
    let _ = InvalidateRect(me, None, TRUE);
}

// ── Position Sync (60fps) ───────────────────────────
unsafe fn do_sync(me: HWND) {
    if !snapped() { return; }
    let t = tgt();
    if t.0.is_null() || !IsWindow(t).as_bool() { log("do_sync: target gone, unsnapping"); do_unsnap(me); return; }
    if IsIconic(t).as_bool() {
        if IsWindowVisible(me).as_bool() { let _ = ShowWindow(me, SW_HIDE); }
        return;
    }
    if !IsWindowVisible(me).as_bool() { let _ = ShowWindow(me, SW_SHOWNA); }
    let mut trc = RECT::default();
    let _ = GetWindowRect(t, &mut trc);
    let mut prc = RECT::default();
    let _ = GetWindowRect(me, &mut prc);
    let tp = (trc.left, trc.top, trc.right - trc.left, trc.bottom - trc.top);
    let pp = (prc.left, prc.top, prc.right - prc.left, prc.bottom - prc.top);
    let sp = saved();
    if tp != sp {
        // Target hat sich bewegt → DirectShell folgt (Z-Order via Owner automatisch)
        let _ = SetWindowPos(me, HWND::default(), tp.0, tp.1, tp.2, tp.3,
            SWP_NOACTIVATE | SWP_NOZORDER);
        save(tp.0, tp.1, tp.2, tp.3);
    } else if pp != sp {
        // DirectShell hat sich bewegt → Target folgt
        let _ = SetWindowPos(t, HWND::default(), pp.0, pp.1, pp.2, pp.3,
            SWP_NOACTIVATE | SWP_NOZORDER);
        save(pp.0, pp.1, pp.2, pp.3);
    }
}

// ── Lichtreflex mit Gradient (diffus, kein harter Block) ──
unsafe fn draw_light(hdc: HDC, w: i32, h: i32) {
    let th = top_h();
    let t = anim_t();
    let wf = w as f64;
    let sh = (h - th) as f64;
    let perim = 2.0 * wf + 2.0 * sh;
    if perim <= 0.0 { return; }
    let center = t * perim;
    let half = LIGHT_LEN / 2.0;

    // 4 Kanten mit Hintergrundfarbe: (Start, Ende, BG-Farbe)
    let edges: [(f64, f64, COLORREF, i32); 4] = [
        (0.0, wf, TOP_CLR, 0),                  // top
        (wf, wf + sh, SIDE_CLR, 1),             // right
        (wf + sh, 2.0 * wf + sh, BOT_CLR, 2),  // bottom
        (2.0 * wf + sh, perim, SIDE_CLR, 3),    // left
    ];

    // Wrap-Around: 3 Kopien des Zentrums prüfen
    for &seg_center in &[center, center + perim, center - perim] {
        for &(e_s, e_e, bg_clr, edge_idx) in &edges {
            let s = (seg_center - half).max(e_s);
            let e = (seg_center + half).min(e_e);
            if s >= e { continue; }
            let edge_len = e_e - e_s;
            if edge_len <= 0.0 { continue; }
            let seg_len = e - s;
            let step_w = seg_len / LIGHT_STEPS as f64;

            for j in 0..LIGHT_STEPS {
                let ss = s + j as f64 * step_w;
                let se = s + (j + 1) as f64 * step_w;
                let mid = (ss + se) / 2.0;

                // Distanz zum Zentrum → 0.0 (Mitte) bis 1.0 (Rand)
                let dist = ((mid - seg_center) / half).abs().min(1.0);
                // Smooth Falloff: cos²(dist * π/2)
                let c = (dist * std::f64::consts::FRAC_PI_2).cos();
                let intensity = c * c;
                if intensity < 0.02 { continue; }

                let clr = lerp_clr(bg_clr, HL_CLR, intensity);
                let brush = CreateSolidBrush(clr);

                let f0 = (ss - e_s) / edge_len;
                let f1 = (se - e_s) / edge_len;

                let rect = match edge_idx {
                    0 => RECT { // Top: links → rechts
                        left: (f0 * wf) as i32,
                        top: 0,
                        right: (f1 * wf) as i32 + 1,
                        bottom: th,
                    },
                    1 => RECT { // Right: oben → unten
                        left: w - SIDE_W,
                        top: th + (f0 * sh) as i32,
                        right: w,
                        bottom: th + (f1 * sh) as i32 + 1,
                    },
                    2 => RECT { // Bottom: rechts → links
                        left: w - (f1 * wf) as i32 - 1,
                        top: h - SIDE_W,
                        right: w - (f0 * wf) as i32,
                        bottom: h,
                    },
                    _ => RECT { // Left: unten → oben
                        left: 0,
                        top: h - (f1 * sh) as i32 - 1,
                        right: SIDE_W,
                        bottom: h - (f0 * sh) as i32,
                    },
                };

                FillRect(hdc, &rect, brush);
                let _ = DeleteObject(brush);
            }
        }
    }
}

// ── Close-Button (unsnapped, oben rechts) ──────────
fn close_area(w: i32) -> (i32, i32, i32, i32) {
    let th = top_h();
    let btn_h = th - 2;
    let btn_w = (btn_h as f64 * 1.4) as i32;
    let x = w - btn_w - 1;
    let y = 1;
    (x, y, x + btn_w, y + btn_h)
}

unsafe fn draw_close_btn(hdc: HDC, w: i32) {
    let (l, t, r, b) = close_area(w);
    let bw = r - l;
    let bh = b - t;

    // Hintergrund: leicht rötlich
    let bg_brush = CreateSolidBrush(COLORREF(0x004040C0)); // Dezentes Rot (BGR)
    FillRect(hdc, &RECT { left: l, top: t, right: r, bottom: b }, bg_brush);
    let _ = DeleteObject(bg_brush);

    // X-Symbol
    let pen = CreatePen(PS_SOLID, 1, ICON_CLR);
    let old_p = SelectObject(hdc, pen);
    let cx = l + bw / 2;
    let cy = t + bh / 2;
    let cr = bh.min(bw) / 2 - 4;
    let _ = MoveToEx(hdc, cx - cr, cy - cr, None);
    let _ = LineTo(hdc, cx + cr + 1, cy + cr + 1);
    let _ = MoveToEx(hdc, cx + cr, cy - cr, None);
    let _ = LineTo(hdc, cx - cr - 1, cy + cr + 1);
    SelectObject(hdc, old_p);
    let _ = DeleteObject(pen);
}

// ── Unsnap-Button (snapped, neben Caption Buttons) ──
fn btn_area(w: i32) -> (i32, i32, i32, i32) {
    let off = BTN_OFF_X.load(SeqCst);
    let th = top_h();
    // Button quadratisch, so hoch wie Titlebar (minus Padding)
    let btn_h = th - 2;
    let btn_w = (btn_h as f64 * 1.2) as i32; // etwas breiter als hoch (wie Windows)
    let x = w - off - btn_w - 2;
    let y = 1;
    (x, y, x + btn_w, y + btn_h)
}

// Windows-Style Caption Button mit ⊕ Icon
unsafe fn draw_unsnap_icon(hdc: HDC, w: i32) {
    let (l, t, r, b) = btn_area(w);
    let bw = r - l;
    let bh = b - t;

    // Button-Hintergrund: leicht heller als Titlebar
    let btn_bg = lerp_clr(TOP_CLR, HL_CLR, 0.08);
    let bg_brush = CreateSolidBrush(btn_bg);
    FillRect(hdc, &RECT { left: l, top: t, right: r, bottom: b }, bg_brush);
    let _ = DeleteObject(bg_brush);

    // ⊕ Icon zentriert im Button
    let cx = l + bw / 2;
    let cy = t + bh / 2;
    let radius = bh.min(bw) / 2 - 4;
    if radius < 3 { return; }

    let pen = CreatePen(PS_SOLID, 1, ICON_CLR);
    let old_p = SelectObject(hdc, pen);
    let old_b = SelectObject(hdc, GetStockObject(NULL_BRUSH));

    // Kreis
    let _ = Ellipse(hdc, cx - radius, cy - radius, cx + radius + 1, cy + radius + 1);
    // Fadenkreuz
    let cr = radius - 2;
    let _ = MoveToEx(hdc, cx - cr, cy, None);
    let _ = LineTo(hdc, cx + cr + 1, cy);
    let _ = MoveToEx(hdc, cx, cy - cr, None);
    let _ = LineTo(hdc, cx, cy + cr + 1);

    SelectObject(hdc, old_p);
    SelectObject(hdc, old_b);
    let _ = DeleteObject(pen);
}

// ── Paint mit Double Buffering ──────────────────────
unsafe fn paint(hwnd: HWND) {
    let mut ps = PAINTSTRUCT::default();
    let hdc = BeginPaint(hwnd, &mut ps);
    let mut rc = RECT::default();
    let _ = GetClientRect(hwnd, &mut rc);
    let w = rc.right;
    let h = rc.bottom;
    let th = top_h();

    // Double Buffer
    let mem_dc = CreateCompatibleDC(hdc);
    let mem_bmp = CreateCompatibleBitmap(hdc, w, h);
    let old_bmp = SelectObject(mem_dc, mem_bmp);

    // 1. Magenta-Hintergrund
    let bg = CreateSolidBrush(INVIS);
    FillRect(mem_dc, &rc, bg);
    let _ = DeleteObject(bg);

    // 2. Rounded Clip (nur oben abgerundet)
    let clip = CreateRoundRectRgn(0, 0, w + 1, h + CORNER_R * 4, CORNER_R * 2, CORNER_R * 2);
    SelectClipRgn(mem_dc, clip);

    // 3. Anthrazit-Rahmen (3D, dynamische Höhe)
    let tbr = CreateSolidBrush(TOP_CLR);
    let sbr = CreateSolidBrush(SIDE_CLR);
    let bbr = CreateSolidBrush(BOT_CLR);
    FillRect(mem_dc, &RECT { left: 0, top: 0, right: w, bottom: th }, tbr);
    FillRect(mem_dc, &RECT { left: 0, top: th, right: SIDE_W, bottom: h - SIDE_W }, sbr);
    FillRect(mem_dc, &RECT { left: w - SIDE_W, top: th, right: w, bottom: h - SIDE_W }, sbr);
    FillRect(mem_dc, &RECT { left: 0, top: h - SIDE_W, right: w, bottom: h }, bbr);
    let _ = DeleteObject(tbr);
    let _ = DeleteObject(sbr);
    let _ = DeleteObject(bbr);

    // 4. 3D-Linien
    let hl_pen = CreatePen(PS_SOLID, 1, HL_CLR);
    let old = SelectObject(mem_dc, hl_pen);
    let _ = MoveToEx(mem_dc, CORNER_R, 1, None);
    let _ = LineTo(mem_dc, w - CORNER_R, 1);
    SelectObject(mem_dc, old);
    let _ = DeleteObject(hl_pen);

    let sh_pen = CreatePen(PS_SOLID, 1, SH_CLR);
    let old = SelectObject(mem_dc, sh_pen);
    let _ = MoveToEx(mem_dc, 0, h - 1, None);
    let _ = LineTo(mem_dc, w, h - 1);
    SelectObject(mem_dc, old);
    let _ = DeleteObject(sh_pen);

    // 5. Lichtreflex + Close (nur wenn NICHT gesnappt)
    if !snapped() {
        draw_light(mem_dc, w, h);
        draw_close_btn(mem_dc, w);
    }

    // 6. Unsnap-Icon (nur wenn gesnappt)
    if snapped() {
        draw_unsnap_icon(mem_dc, w);
    }

    // Clip reset
    SelectClipRgn(mem_dc, HRGN::default());
    let _ = DeleteObject(clip);

    // BitBlt: Buffer → Screen
    let _ = BitBlt(hdc, 0, 0, w, h, mem_dc, 0, 0, SRCCOPY);

    SelectObject(mem_dc, old_bmp);
    let _ = DeleteObject(mem_bmp);
    let _ = DeleteDC(mem_dc);
    let _ = EndPaint(hwnd, &ps);
}

// ── Window Procedure ────────────────────────────────
unsafe extern "system" fn wndproc(
    hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM,
) -> LRESULT {
    match msg {
        WM_PAINT => {
            paint(hwnd);
            LRESULT(0)
        }

        WM_NCHITTEST => {
            let x = (lp.0 & 0xFFFF) as i16 as i32;
            let y = ((lp.0 >> 16) & 0xFFFF) as i16 as i32;
            let mut rc = RECT::default();
            let _ = GetWindowRect(hwnd, &mut rc);
            let lx = x - rc.left;
            let ly = y - rc.top;
            let w = rc.right - rc.left;
            let h = rc.bottom - rc.top;
            let th = top_h();

            if ly < th {
                if snapped() {
                    // Unsnap-Button → HTCLIENT (clickable)
                    let (bl, bt, br, bb) = btn_area(w);
                    if lx >= bl && lx <= br && ly >= bt && ly <= bb {
                        return LRESULT(HTCLIENT as _);
                    }
                    // Target caption buttons (min/max/close) → pass through
                    let off = BTN_OFF_X.load(SeqCst);
                    if lx >= w - off {
                        return LRESULT(HTTRANSPARENT as _);
                    }
                } else {
                    let (cl, ct, cr, cb) = close_area(w);
                    if lx >= cl && lx <= cr && ly >= ct && ly <= cb {
                        return LRESULT(HTCLIENT as _);
                    }
                }
                return LRESULT(HTCAPTION as _);
            }
            if lx < GRIP || lx > w - GRIP || ly > h - GRIP {
                return LRESULT(HTCAPTION as _);
            }
            LRESULT(HTTRANSPARENT as _)
        }

        WM_LBUTTONDOWN => {
            let cx = (lp.0 & 0xFFFF) as i16 as i32;
            let cy = ((lp.0 >> 16) & 0xFFFF) as i16 as i32;
            let mut wrc = RECT::default();
            let _ = GetClientRect(hwnd, &mut wrc);
            if snapped() {
                let (bl, bt, br, bb) = btn_area(wrc.right);
                if cx >= bl && cx <= br && cy >= bt && cy <= bb {
                    do_unsnap(hwnd);
                    return LRESULT(0);
                }
            } else {
                let (cl, ct, cr, cb) = close_area(wrc.right);
                if cx >= cl && cx <= cr && cy >= ct && cy <= cb {
                    let _ = PostMessageW(hwnd, WM_CLOSE, WPARAM(0), LPARAM(0));
                    return LRESULT(0);
                }
            }
            DefWindowProcW(hwnd, msg, wp, lp)
        }

        WM_EXITSIZEMOVE => {
            if !snapped() {
                if let Some(t) = find_snap(hwnd) {
                    do_snap(hwnd, t);
                }
            }
            LRESULT(0)
        }

        WM_MOVING => {
            if snapped() {
                let new_rc = &*(lp.0 as *const RECT);
                let t = tgt();
                if !t.0.is_null() && IsWindow(t).as_bool() {
                    let nw = new_rc.right - new_rc.left;
                    let nh = new_rc.bottom - new_rc.top;
                    let _ = SetWindowPos(t, HWND::default(),
                        new_rc.left, new_rc.top, nw, nh,
                        SWP_NOACTIVATE | SWP_NOZORDER);
                    save(new_rc.left, new_rc.top, nw, nh);
                }
            }
            DefWindowProcW(hwnd, msg, wp, lp)
        }

        WM_TIMER => {
            match wp.0 {
                SYNC_TIMER => do_sync(hwnd),
                ANIM_TIMER => { let _ = InvalidateRect(hwnd, None, FALSE); },
                TREE_TIMER => { dump_tree(); },
                INJECT_TIMER => { process_injections(); },
                _ => {}
            }
            LRESULT(0)
        }

        WM_CLOSE => {
            log("WM_CLOSE received");
            if snapped() {
                let t = tgt();
                if !t.0.is_null() && IsWindow(t).as_bool() {
                    let _ = PostMessageW(t, WM_CLOSE, WPARAM(0), LPARAM(0));
                }
                do_unsnap(hwnd);
            }
            // DBs bleiben persistent (ds_profiles/). Nur Log aufräumen.
            let _ = DestroyWindow(hwnd);
            LRESULT(0)
        }

        WM_DESTROY => {
            let hk = KB_HOOK.swap(0, SeqCst);
            if hk != 0 {
                let _ = UnhookWindowsHookEx(HHOOK(hk as *mut _));
                log("Keyboard hook removed");
            }
            PostQuitMessage(0);
            LRESULT(0)
        }

        _ => DefWindowProcW(hwnd, msg, wp, lp),
    }
}

fn main() -> Result<()> {
    // Log-Datei bei Start leeren
    let _ = fs::write(LOG_FILE, "");
    log("=== DirectShell START ===");

    unsafe {
        let _ = CoInitializeEx(None, COINIT_MULTITHREADED);
        log("COM initialized");

        // Screen Reader Flag SOFORT setzen — bevor irgendwas passiert.
        // Apps die NACH DirectShell starten sehen das Flag von Anfang an.
        let _ = SystemParametersInfoW(
            SPI_SETSCREENREADER,
            1,
            None,
            SYSTEM_PARAMETERS_INFO_UPDATE_FLAGS(0x0002),
        );
        log("SPI_SETSCREENREADER = TRUE (global, at startup)");

        let inst = GetModuleHandleW(None)?;
        let hinst: HINSTANCE = inst.into();
        let cls = w!("DirectShell");

        let wc = WNDCLASSEXW {
            cbSize: mem::size_of::<WNDCLASSEXW>() as u32,
            style: CS_HREDRAW | CS_VREDRAW,
            lpfnWndProc: Some(wndproc),
            hInstance: hinst,
            hCursor: LoadCursorW(None, IDC_ARROW)?,
            hbrBackground: CreateSolidBrush(INVIS),
            lpszClassName: cls,
            ..Default::default()
        };
        RegisterClassExW(&wc);

        let hwnd = CreateWindowExW(
            WS_EX_LAYERED | WS_EX_TOPMOST,
            cls, w!("DirectShell"),
            WS_POPUP | WS_VISIBLE,
            200, 200, 500, 350,
            HWND::default(), HMENU::default(), hinst, None,
        )?;

        SetLayeredWindowAttributes(hwnd, INVIS, ALPHA, LWA_COLORKEY | LWA_ALPHA)?;
        log(&format!("Window created: 0x{:X}", hwnd.0 as usize));

        let _ = SetTimer(hwnd, ANIM_TIMER, ANIM_MS, None);

        // Keyboard Hook installieren (global, low-level)
        let hook = SetWindowsHookExW(WH_KEYBOARD_LL, Some(kb_hook_proc), hinst, 0)?;
        KB_HOOK.store(hook.0 as isize, SeqCst);
        log(&format!("Keyboard hook installed: 0x{:X}", hook.0 as usize));

        let mut msg = MSG::default();
        while GetMessageW(&mut msg, None, 0, 0).into() {
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }

        log("=== DirectShell EXIT ===");
        CoUninitialize();
        Ok(())
    }
}
