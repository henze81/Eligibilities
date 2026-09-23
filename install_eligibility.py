#!/usr/bin/env python3
"""
install_eligibility.py — UFO Engine Eligibility Feature

Idempotentes Install-Skript. Führe mit --dry-run aus um Änderungen zu prüfen.

Schritte:
  1. Backend-Router eligibility.py anlegen (inkl. cluster-set Endpoint)
  2. main.py: Import ergänzen
  3. main.py: Router registrieren
  4. OBOverview.tsx: Eligibility-State + Funktionen einfügen
  5. OBOverview.tsx: useAllProcessPaths Import ergänzen
  6. OBOverview.tsx: useAllProcessPaths Hook-Call ergänzen
  7. Tampermonkey-Bridge-Skript prüfen + Anleitung ausgeben
"""

from __future__ import annotations
import sys
from pathlib import Path

DRY      = '--dry-run' in sys.argv
ROOT     = Path('C:/UFO-Engine')
UFONEUES = Path('C:/Meiner/Ufoneues')

def step(n, ok, msg=''):
    tag = 'SKIP ✓' if ok else ('DRY  ~' if DRY else 'OK   →')
    print(f'  [{tag}] {n}' + (f' — {msg}' if msg else ''))

def patch_after(path_rel, anchor, insert, check):
    p = ROOT / path_rel; c = p.read_text(encoding='utf-8')
    if check in c: print(f'  [SKIP ✓] {path_rel}: bereits vorhanden'); return
    if anchor not in c: print(f'  [FEHLER] {path_rel}: Ankerpunkt nicht gefunden'); return
    if not DRY: p.write_text(c.replace(anchor, anchor + insert, 1), encoding='utf-8')
    print(f'  [{"DRY  ~" if DRY else "OK   →"}] {path_rel}')

def patch_before(path_rel, anchor, insert, check):
    p = ROOT / path_rel; c = p.read_text(encoding='utf-8')
    if check in c: print(f'  [SKIP ✓] {path_rel}: bereits vorhanden'); return
    if anchor not in c: print(f'  [FEHLER] {path_rel}: Ankerpunkt nicht gefunden'); return
    if not DRY: p.write_text(c.replace(anchor, insert + anchor, 1), encoding='utf-8')
    print(f'  [{"DRY  ~" if DRY else "OK   →"}] {path_rel}')

ROUTER_SRC  = '"""Eligibility bridge queue — UFO Engine backend.\n\nPuffer zwischen UFO-Frontend (POST /eligibility/set) und dem Tampermonkey-Bridge-Skript\n(GET /eligibility/pending → POST /eligibility/result).\nDer Browser im Eligibility-Portal sendet die eigentliche API-Request mit CFS-Cookies.\n"""\n\nfrom __future__ import annotations\n\nimport threading\nimport uuid\nfrom datetime import datetime, timezone\n\nfrom fastapi import APIRouter\nfrom pydantic import BaseModel\n\nrouter = APIRouter(prefix="/eligibility", tags=["eligibility"])\n\n# ── In-memory queue (kein Neustart nötig für Zustandsreset) ──────────────────\n_lock    = threading.Lock()\n_pending: dict[str, dict] = {}   # job_id → job\n_results: dict[str, dict] = {}   # job_id → result (letzten 100)\n\n\n# ── Models ────────────────────────────────────────────────────────────────────\nclass EligibilitySetPayload(BaseModel):\n    employeeIds:          list[str]\n    eligibilities:        list[str] = []\n    removeEligibilities:  list[str] = []\n    userId:               str       = ""\n\n\nclass EligibilityResultPayload(BaseModel):\n    job_id:   str\n    status:   str           # "success" | "error"\n    response: dict | None = None\n    error:    str  | None = None\n\n\n# ── Endpoints ─────────────────────────────────────────────────────────────────\n@router.post("/set")\ndef set_eligibility(payload: EligibilitySetPayload):\n    """Frontend queues an eligibility change. Returns job_id for status polling."""\n    job_id = str(uuid.uuid4())\n    with _lock:\n        _pending[job_id] = {\n            "job_id":               job_id,\n            "employee_ids":         payload.employeeIds,\n            "eligibilities":        payload.eligibilities,\n            "remove_eligibilities": payload.removeEligibilities,\n            "user_id":              payload.userId,\n            "created_at":           datetime.now(timezone.utc).isoformat(),\n        }\n    return {"job_id": job_id, "status": "pending"}\n\n\n@router.get("/pending")\ndef get_pending():\n    """Tampermonkey bridge polls this. Returns all pending jobs and clears them."""\n    with _lock:\n        jobs = list(_pending.values())\n    return {"jobs": jobs}\n\n\n@router.post("/result")\ndef post_result(payload: EligibilityResultPayload):\n    """Tampermonkey bridge reports success/failure. Moves job from pending → results."""\n    with _lock:\n        job = _pending.pop(payload.job_id, None)\n        if job is None:\n            # Already consumed or unknown — still store the result\n            job = {"job_id": payload.job_id}\n        _results[payload.job_id] = {\n            **job,\n            "status":       payload.status,\n            "response":     payload.response,\n            "error":        payload.error,\n            "completed_at": datetime.now(timezone.utc).isoformat(),\n        }\n        # Keep only the last 100 results\n        if len(_results) > 100:\n            oldest_key = sorted(_results, key=lambda k: _results[k].get("completed_at", ""))[0]\n            del _results[oldest_key]\n    return {"ok": True}\n\n\n@router.get("/status/{job_id}")\ndef get_job_status(job_id: str):\n    """Frontend polls for job completion."""\n    with _lock:\n        if job_id in _pending:\n            return {"job_id": job_id, "status": "pending"}\n        if job_id in _results:\n            return _results[job_id]\n    return {"job_id": job_id, "status": "not_found"}\n\n\n@router.get("/queue")\ndef get_queue_debug():\n    """Debug: show full pending queue + recent results."""\n    with _lock:\n        return {\n            "pending_count": len(_pending),\n            "pending":       list(_pending.values()),\n            "results_count": len(_results),\n            "recent_results": sorted(\n                _results.values(),\n                key=lambda r: r.get("completed_at", ""),\n                reverse=True,\n            )[:10],\n        }\n\n\nclass ClusterSetPayload(BaseModel):\n    employeeIds:    list[str]\n    clusters:       list[str] = []\n    removeClusters: list[str] = []\n    userId:         str       = ""\n\n\n@router.post("/cluster-set")\ndef set_cluster_eligibility(payload: ClusterSetPayload):\n    """Queue a cluster eligibility change (UpdateClusterEligibilitiesToPickers)."""\n    job_id = str(uuid.uuid4())\n    with _lock:\n        _pending[job_id] = {\n            "job_id":           job_id,\n            "type":             "cluster",\n            "employee_ids":     payload.employeeIds,\n            "clusters":         payload.clusters,\n            "remove_clusters":  payload.removeClusters,\n            "user_id":          payload.userId,\n            "created_at":       datetime.now(timezone.utc).isoformat(),\n        }\n    return {"job_id": job_id, "status": "pending"}\n'
ELIG_BLOCK  = "\n\n  // ── Pick Workforce eligibility popover ────────────────────────────────────\n  // H1: Halle 1 + PT-Levels  |  H3: Halle 3 (separate Cluster-Struktur)\n  const PW_CLUSTER_H1 = ['Halle 1', 'PT1-LV1', 'PT1-LV2', 'PT2-LV2', 'PT3-LV1', 'PT3-LV2', 'PT3-LV3']\n  const PW_CLUSTER_H3 = ['Halle 3']\n  const pwClusters = (isH3: boolean) => isH3 ? PW_CLUSTER_H3 : PW_CLUSTER_H1\n  type PwEligAa = { login: string; employee_id: string; _dest: string; _isH3: boolean }\n  const [pwEligPopover, setPwEligPopover] = useState<{ aa: PwEligAa; x: number; y: number } | null>(null)\n  const [pwEligToast,   setPwEligToast]   = useState<{ status: 'pending' | 'success' | 'error'; msg: string } | null>(null)\n  const [selectedPickers, setSelectedPickers] = useState<PwEligAa[]>([])\n\n  // ── Direktzuweisung ───────────────────────────────────────────────────────\n  type DirektEntry = { name: string; login: string; employee_id: string; isH3: boolean }\n  const [direktModal,   setDirektModal]   = useState(false)\n  const [direktLogins,  setDirektLogins]  = useState('')    // raw textarea\n  const [direktPickers, setDirektPickers] = useState<DirektEntry[]>([])\n  const [direktUnknown, setDirektUnknown] = useState<string[]>([])\n  const [direktDest,       setDirektDest]       = useState('')\n  const [direktFallbackH3, setDirektFallbackH3] = useState(false)  // H3-Toggle für nicht-workforce Picker\n  const [namesCache,       setNamesCache]        = useState<DirektEntry[]>([])\n\n  async function loadNamesCache() {\n    if (namesCache.length > 0) return\n    try {\n      const r = await fetch('/api/overview/workforce-names')\n      const d = await r.json()\n      setNamesCache([\n        ...(d.PT  ?? []).map((x: any) => ({ ...x, isH3: false })),\n        ...(d.PUP ?? []).map((x: any) => ({ ...x, isH3: true  })),\n      ])\n    } catch {}\n  }\n\n  async function resolveDirektLogins(raw: string, fallbackIsH3: boolean) {\n    const tokens = raw.toLowerCase().split(/[\\s,;\\n]+/).filter(Boolean)\n    const found: DirektEntry[] = []\n    const unresolved: string[] = []\n    for (const t of tokens) {\n      const match = namesCache.find(p => p.login.toLowerCase() === t)\n      if (match) { if (!found.find(p => p.employee_id === match.employee_id)) found.push(match) }\n      else unresolved.push(t)\n    }\n    // Für nicht-workforce Logins: ANW-Snapshot als Fallback\n    if (unresolved.length > 0) {\n      try {\n        const r = await fetch(`/api/overview/resolve-logins?logins=${encodeURIComponent(unresolved.join(','))}`)\n        const resolved: { login: string; employee_id: string; name: string }[] = await r.json()\n        const resolvedLogins = new Set(resolved.map(x => x.login.toLowerCase()))\n        for (const x of resolved) {\n          if (!found.find(p => p.employee_id === x.employee_id))\n            found.push({ ...x, isH3: fallbackIsH3 })\n        }\n        setDirektUnknown(unresolved.filter(t => !resolvedLogins.has(t)))\n      } catch {\n        setDirektUnknown(unresolved)\n      }\n    } else {\n      setDirektUnknown([])\n    }\n    setDirektPickers(found)\n  }\n\n  // Destination → Eligibility-Name Alias (wenn Rodeo-PP-Name ≠ Eligibility-Name)\n  const DEST_ALIAS: Record<string, string> = { 'Flow': 'VNA' }\n  // Destinations die KEIN HR-Suffix haben (gleicher Pfad für H1 und H3)\n  const NO_HR_DESTS = new Set(['VNA'])\n\n  function direktSubmit() {\n    if (direktPickers.length === 0 || !direktDest) return\n    // Gruppe nach H1 / H3 – jede Gruppe bekommt den passenden Pfad\n    const h1 = direktPickers.filter(p => !p.isH3)\n    const h3 = direktPickers.filter(p =>  p.isH3)\n    const eligDest = DEST_ALIAS[direktDest] ?? direktDest   // z.B. Flow → VNA\n    const jobs: { ids: string[]; path: string; label: string }[] = []\n    const h3Path = NO_HR_DESTS.has(eligDest) ? `Trans${eligDest}Picking` : `Trans${eligDest}HRPicking`\n    if (h1.length > 0) jobs.push({ ids: h1.map(p => p.employee_id), path: `Trans${eligDest}Picking`, label: h1.map(p => p.login).join(', ') + ` → ${direktDest}` })\n    if (h3.length > 0) jobs.push({ ids: h3.map(p => p.employee_id), path: h3Path,                   label: h3.map(p => p.login).join(', ') + ` → ${direktDest}${NO_HR_DESTS.has(eligDest) ? '' : ' (H3)'}` })\n    const totalLabel = `${direktPickers.length} Picker → ${direktDest}`\n    setPwEligToast({ status: 'pending', msg: `${totalLabel} …` })\n    setDirektModal(false); setDirektPickers([]); setDirektUnknown([]); setDirektLogins(''); setDirektDest('')\n    for (const job of jobs) {\n      fetch('/api/eligibility/set', {\n        method: 'POST', headers: { 'Content-Type': 'application/json' },\n        body: JSON.stringify({ employeeIds: job.ids, eligibilities: [job.path], removeEligibilities: [], userId: authStatus?.user ?? '' }),\n      }).then(r => r.json()).then(({ job_id }) => pwPollResult(job_id, job.label))\n        .catch(err => { setPwEligToast({ status: 'error', msg: err.message }); setTimeout(() => setPwEligToast(null), 5000) })\n    }\n  }\n\n  function pwPollResult(jobId: string, label: string, attempts = 0) {\n    if (attempts > 60) {\n      setPwEligToast({ status: 'error', msg: 'Timeout — Bridge läuft?' })\n      setTimeout(() => setPwEligToast(null), 6000)\n      return\n    }\n    setTimeout(async () => {\n      try {\n        const r = await fetch(`/api/eligibility/status/${jobId}`)\n        const d = await r.json()\n        if (d.status === 'pending') {\n          pwPollResult(jobId, label, attempts + 1)\n        } else if (d.status === 'success') {\n          setPwEligToast({ status: 'success', msg: `${label} ✓` })\n          setTimeout(() => setPwEligToast(null), 3000)\n        } else if (d.status === 'not_found') {\n          setPwEligToast({ status: 'error', msg: 'Job nicht gefunden — Backend neugestartet?' })\n          setTimeout(() => setPwEligToast(null), 5000)\n        } else {\n          setPwEligToast({ status: 'error', msg: `Fehler: ${d.error ?? d.status ?? '?'}` })\n          setTimeout(() => setPwEligToast(null), 5000)\n        }\n      } catch { setPwEligToast({ status: 'error', msg: 'Backend nicht erreichbar' }); setTimeout(() => setPwEligToast(null), 4000) }\n    }, 1500)\n  }\n\n  function pwSendDestChange(aa: PwEligAa, targetDest: string) {\n    if (targetDest === aa._dest) return\n    const label = `${aa.login} → ${targetDest}`\n    setPwEligToast({ status: 'pending', msg: `${label} …` })\n    setPwEligPopover(null)\n    const fromPath  = `Trans${aa._dest}${aa._isH3 ? 'HR' : ''}Picking`\n    const eligDest  = DEST_ALIAS[targetDest] ?? targetDest\n    const toIsH3    = aa._isH3 && !NO_HR_DESTS.has(eligDest)\n    const toPath    = `Trans${eligDest}${toIsH3 ? 'HR' : ''}Picking`\n    fetch('/api/eligibility/set', {\n      method: 'POST', headers: { 'Content-Type': 'application/json' },\n      body: JSON.stringify({ employeeIds: [aa.employee_id], eligibilities: [toPath], removeEligibilities: [fromPath], userId: authStatus?.user ?? '' }),\n    }).then(r => r.json()).then(({ job_id }) => pwPollResult(job_id, label))\n      .catch(err => { console.error('[Elig] fetch error', err); setPwEligToast({ status: 'error', msg: err.message + ' | ' + String(err) }); setTimeout(() => setPwEligToast(null), 8000) })\n  }\n\n  function pwSendClusterChange(aa: PwEligAa, cluster: string, action: 'add' | 'remove') {\n    const label = `${aa.login} ${action === 'add' ? '+' : '−'} ${cluster}`\n    setPwEligToast({ status: 'pending', msg: `${label} …` })\n    setPwEligPopover(null)\n    // Beim Hinzufügen: nur Parent-Cluster entfernen (Halle 1 / Halle 3).\n    // Mehrere unbekannte Cluster auf einmal entfernen → HTTP 500 wenn Picker sie nicht hat.\n    const parentCluster  = aa._isH3 ? 'Halle 3' : 'Halle 1'\n    const addClusters    = action === 'add' ? [cluster] : []\n    const removeClusters = action === 'remove'\n      ? [cluster]\n      : (cluster !== parentCluster ? [parentCluster] : [])   // Sub-Cluster hinzufügen → Parent abwählen\n    fetch('/api/eligibility/cluster-set', {\n      method: 'POST', headers: { 'Content-Type': 'application/json' },\n      body: JSON.stringify({ employeeIds: [aa.employee_id], clusters: addClusters, removeClusters, userId: authStatus?.user ?? '' }),\n    }).then(r => r.json()).then(({ job_id }) => pwPollResult(job_id, label))\n      .catch(err => { setPwEligToast({ status: 'error', msg: err.message }); setTimeout(() => setPwEligToast(null), 5000) })\n  }\n\n"

# ── 1. Backend-Router ─────────────────────────────────────────────────────────
ROUTER_DST = ROOT / 'backend/routers/eligibility.py'
already = ROUTER_DST.exists() and 'cluster-set' in ROUTER_DST.read_text(encoding='utf-8')
step('1. eligibility.py (inkl. cluster-set)', already)
if not already and not DRY:
    ROUTER_DST.write_text(ROUTER_SRC, encoding='utf-8')

# ── 2. main.py Import ─────────────────────────────────────────────────────────
MAIN = ROOT / 'backend/main.py'
main_txt = MAIN.read_text(encoding='utf-8')
import_ok = 'eligibility' in main_txt
step('2. main.py Import', import_ok)
if not import_ok and not DRY:
    OLD = ('from backend.routers import capacity, workflow, einteilung, ant, status, auth, '
           'storage, performance, ubergabe, overview, learn, reporting, daily_plan, dashboard, '
           'fast_start, eos, absence, preassign, admin, ship_tracker, stowmap, ob_capacity, '
           'ib_capacity, hourly_plan, development')
    MAIN.write_text(main_txt.replace(OLD, OLD + ', eligibility', 1), encoding='utf-8')

# ── 3. main.py Router ─────────────────────────────────────────────────────────
main_txt = MAIN.read_text(encoding='utf-8')
router_ok = 'eligibility.router' in main_txt
step('3. main.py Router registrieren', router_ok)
if not router_ok and not DRY:
    main_txt = main_txt.replace(
        'app.include_router(development.router)',
        'app.include_router(development.router)\napp.include_router(eligibility.router)', 1)
    MAIN.write_text(main_txt, encoding='utf-8')

# ── 4. OBOverview.tsx: Eligibility State + Funktionen ────────────────────────
print('\n4. OBOverview.tsx: Eligibility State + Funktionen')
patch_after(
    'frontend/src/pages/OBOverview.tsx',
    "  const [subShift, setSubShift] = useState('ES_SOS')",
    ELIG_BLOCK,
    'pwEligPopover',
)

# ── 5. OBOverview.tsx: useAllProcessPaths Import ─────────────────────────────
print('5. OBOverview.tsx: useAllProcessPaths Import')
patch_before(
    'frontend/src/pages/OBOverview.tsx',
    'usePickVolume, useStageVolumes',
    'useAllProcessPaths, ',
    'useAllProcessPaths',
)

# ── 6. OBOverview.tsx: useAllProcessPaths Hook-Call ──────────────────────────
print('6. OBOverview.tsx: useAllProcessPaths Hook-Call')
patch_after(
    'frontend/src/pages/OBOverview.tsx',
    '  const { data: pickerData } = usePickerCounts()',
    '\n  const { data: allPathsData  } = useAllProcessPaths()',
    'allPathsData',
)

# ── 7. Tampermonkey-Bridge ────────────────────────────────────────────────────
TM_DST = UFONEUES / 'ufo_eligibility_bridge.user.js'
tm_ok  = TM_DST.exists()
step('7. Tampermonkey-Bridge vorhanden', tm_ok, str(TM_DST) if tm_ok else 'fehlt')

print()
print('─' * 60)
print('✓ Install abgeschlossen. Backend-Neustart nötig.' if not DRY else 'Dry-run — keine Dateien geändert.')
print()
print('Tampermonkey-Bridge installieren:')
print('  1. Firefox öffnen → Tampermonkey-Icon → Dashboard')
print(f'  2. Datei hierher ziehen: {TM_DST}')
print('     ODER: Tampermonkey → "Neue Skriptdatei importieren"')
print('  3. Eligibility-Portal öffnen:')
print('     https://eligibility.eu-south-2-prod.picking.aft.amazon.dev/DRS8/picker-eligibilities')
print('  4. Badge "🔌 UFO Bridge: bereit" erscheint unten rechts')
