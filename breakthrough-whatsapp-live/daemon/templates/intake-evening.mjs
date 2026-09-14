#!/usr/bin/env node
// intake-evening.mjs · the evening intake pre-sort push (template; the installer copies it to <home>/launchd/intake-evening.mjs).
//
// launchd is the clock (StartCalendarInterval at intake.evening_at). `wa_intake.py --evening-status` is the eye: it applies
// every config default and says whether to run, how many entries are open, the prompt, the claude binary, model and
// settings. One `claude -p` run suggests a place for each open entry. `wa_common.py --notify --kind digest` is the mouth,
// the same push outlet the daemon uses.
// It writes nothing: not the vault, not an entry's status. Zero open entries means nothing is sent. intake.enabled or
// intake.evening_push false, no outlet, or notify.mode off: one log line and exit 0.
//
// Where things come from:
//   config       argv[2] · env WA_LIVE_CONFIG · the rendered CONFIG constant · <this folder>/../live/config.json ·
//                ~/WhatsApp-Live/live/config.json
//   daemon dir   env WA_LIVE_DAEMON_DIR · the rendered DAEMON_DIR constant · the first ~/.claude/skills/*/daemon folder
//                that holds wa_intake.py and wa_common.py
//   python       env WA_LIVE_PYTHON · bin.python in the config · /usr/bin/python3
// The installer may replace {CONFIG} and {DAEMON_DIR} below with absolute paths; left as they are, they are ignored.
// `claude -p` only works from the launchd gui domain (an SSH session has no keychain): test with
// `launchctl kickstart -k gui/$(id -u)/<intake label>` and read the log file named in the plist.

import { spawn } from 'node:child_process';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const RENDERED_CONFIG = '{CONFIG}';
const RENDERED_DAEMON_DIR = '{DAEMON_DIR}';
const HERE = dirname(fileURLToPath(import.meta.url));
const HOME = process.env.HOME || homedir();

// The cage, same lists as the daemon: only Read, Glob and Grep; anything else loaded is reported as a breach.
const DENY_TOOLS = ['Bash', 'Write', 'Edit', 'NotebookEdit', 'WebFetch', 'WebSearch', 'Task', 'Agent', 'Skill', 'Workflow',
  'CronCreate', 'CronDelete', 'CronList', 'SendMessage', 'ListAgents', 'RemoteTrigger', 'PushNotification', 'Monitor',
  'TaskOutput', 'TaskStop', 'ScheduleWakeup', 'EnterWorktree', 'ExitWorktree', 'DesignSync', 'ReportFindings', 'ToolSearch',
  'Artifact'];
const EXPECTED_TOOLS = new Set(['Read', 'Glob', 'Grep', 'TodoWrite']);

const log = (...a) => console.log(`[${new Date().toISOString()}]`, ...a);
const rendered = (v) => (v && !v.includes('{') ? v : '');
const expand = (p) => (typeof p === 'string' && p.startsWith('~/') ? join(HOME, p.slice(2)) : p);
const fmt = (template, value) => String(template || '').replace(/%[ds]/, String(value));

function pickConfig() {
  const candidates = [process.argv[2], process.env.WA_LIVE_CONFIG, rendered(RENDERED_CONFIG),
    resolve(HERE, '..', 'live', 'config.json'), join(HOME, 'WhatsApp-Live', 'live', 'config.json')];
  for (const c of candidates.map(expand)) {
    if (c && existsSync(c)) return c;
  }
  return '';
}

function pickDaemonDir() {
  const ok = (d) => d && existsSync(join(d, 'wa_intake.py')) && existsSync(join(d, 'wa_common.py'));
  for (const d of [process.env.WA_LIVE_DAEMON_DIR, rendered(RENDERED_DAEMON_DIR)].map(expand)) {
    if (ok(d)) return d;
  }
  const skills = join(HOME, '.claude', 'skills');
  try {
    for (const name of readdirSync(skills).sort()) {
      const d = join(skills, name, 'daemon');
      if (ok(d)) return d;
    }
  } catch {}
  return '';
}

const run = (bin, args, { timeoutMs = 120_000, input, cwd = HOME, env = process.env } = {}) => new Promise((done) => {
  let child;
  try {
    child = spawn(bin, args, { cwd, env, stdio: ['pipe', 'pipe', 'pipe'] });
  } catch (e) {
    done({ code: -1, out: '', err: e.message });
    return;
  }
  let out = '';
  let err = '';
  const timer = setTimeout(() => { try { child.kill('SIGKILL'); } catch {} }, timeoutMs);
  child.stdout.on('data', (d) => { out += d; });
  child.stderr.on('data', (d) => { err += d; });
  child.stdin.on('error', () => {});
  child.on('close', (code) => { clearTimeout(timer); done({ code, out, err }); });
  child.on('error', (e) => { clearTimeout(timer); done({ code: -1, out, err: e.message }); });
  if (input !== undefined) child.stdin.end(input); else child.stdin.end();
});

function extractJson(text) {
  const start = text.indexOf('{');
  if (start < 0) return null;
  let depth = 0;
  let inStr = false;
  let esc = false;
  for (let k = start; k < text.length; k++) {
    const ch = text[k];
    if (esc) { esc = false; continue; }
    if (ch === '\\') { esc = true; continue; }
    if (ch === '"') { inStr = !inStr; continue; }
    if (inStr) continue;
    if (ch === '{') depth++;
    else if (ch === '}') { depth--; if (depth === 0) return text.slice(start, k + 1); }
  }
  return null;
}

// ---- 0. where everything is ------------------------------------------------------------
const CONFIG = pickConfig();
if (!CONFIG) { log('no live config found (pass it as the first argument or set WA_LIVE_CONFIG)'); process.exit(1); }
const DAEMON = pickDaemonDir();
if (!DAEMON) { log('daemon folder not found (set WA_LIVE_DAEMON_DIR)'); process.exit(1); }
let rawConfig = {};
try { rawConfig = JSON.parse(readFileSync(CONFIG, 'utf8')); } catch (e) { log('config unreadable:', CONFIG, e.message); process.exit(1); }
const PYTHON = [process.env.WA_LIVE_PYTHON, rawConfig?.bin?.python].map(expand)
  .find((p) => typeof p === 'string' && p && !p.startsWith('<') && existsSync(p)) || '/usr/bin/python3';
const baseEnv = { ...process.env, HOME, LANG: process.env.LANG || 'en_US.UTF-8', WA_LIVE_CONFIG: CONFIG };

// ---- 1. the eye: should we run, and what is open ----------------------------------------
const st = await run(PYTHON, [join(DAEMON, 'wa_intake.py'), '--config', CONFIG, '--evening-status'], { timeoutMs: 60_000, env: baseEnv });
let status;
try { status = JSON.parse(st.out); } catch {
  log(`evening status unreadable (exit ${st.code}):`, (st.err || st.out).replace(/\s+/g, ' ').slice(0, 300));
  process.exit(1);
}
if (!status.run) { log(status.why || 'evening push not enabled'); process.exit(0); }
if (!status.open) { log('open entries: 0, nothing sent'); process.exit(0); }
log(`open entries: ${status.open}${status.why ? ` (${status.why})` : ''}`);

// ---- 2. the brain: one suggestion per entry, read only ---------------------------------
const runEnv = { ...baseEnv, PATH: status.path_env || process.env.PATH };
const cwd = status.cwd && existsSync(status.cwd) ? status.cwd : HOME;
if (cwd !== status.cwd) log('brain cwd missing, using HOME instead:', status.cwd);
let raw = '';
let problem = '';
let breach = [];
if (!status.claude) {
  problem = 'claude binary not found (bin.claude)';
} else if (!status.settings_exists) {
  problem = `claude settings missing: ${status.settings}`;
} else {
  const args = ['-p', status.prompt, '--output-format', 'stream-json', '--verbose', '--model', status.model,
    '--settings', status.settings, '--strict-mcp-config', '--no-session-persistence', '--permission-mode', 'default',
    '--allowedTools', 'Read Glob Grep', '--disallowedTools', DENY_TOOLS.join(' '), '--max-turns', String(status.max_turns || 4)];
  const r = await run(status.claude, args, { timeoutMs: (status.timeout_seconds || 300) * 1000, env: runEnv, cwd });
  for (const line of r.out.split('\n')) {
    let ev;
    try { ev = JSON.parse(line); } catch { continue; }
    if (ev.type === 'system' && ev.subtype === 'init' && Array.isArray(ev.tools)) breach = ev.tools.filter((t) => !EXPECTED_TOOLS.has(t));
    if (ev.type === 'result') raw = String(ev.result || '').trim();
  }
  if (breach.length) log('CAGE BREACH: the pre-sort run loaded', breach.join(', '));
  if (!raw) problem = `no result from claude (exit ${r.code}): ${(r.err || '').replace(/\s+/g, ' ').slice(-200)}`;
}

// ---- 3. the message -----------------------------------------------------------------------
const labels = status.labels || {};
let body;
try {
  if (problem) throw new Error(problem);
  const obj = JSON.parse(extractJson(raw) || 'null');
  if (!obj || !Array.isArray(obj.items) || !obj.items.length) throw new Error('the answer had no items');
  const lines = [fmt(labels.title, status.open), ''];
  for (const it of obj.items.slice(0, status.max_items || 12)) {
    lines.push(`${it.n}. ${it.who || '?'} · ${it.what || ''}\n   → ${it.suggest || labels.ask || '?'}${it.why ? ` (${it.why})` : ''}`);
  }
  lines.push('', labels.reply || '');
  body = lines.join('\n');
} catch (e) {
  log('pre-sort unusable, sending the raw list:', e.message, '| answer head:', raw.slice(0, 160));
  body = `${fmt(labels.fallback, status.open)}\n\n${String(status.listing || '').slice(0, 3000)}\n\n${labels.reply || ''}`;
}
if (breach.length) body += `\n\n${fmt(labels.breach, breach.join(', '))}`;

// ---- 4. the mouth: the one push outlet ------------------------------------------------------
const sent = await run(PYTHON, [join(DAEMON, 'wa_common.py'), '--config', CONFIG, '--notify', '--kind', 'digest'],
  { input: body, timeoutMs: 60_000, env: baseEnv });
log('push:', (sent.out || sent.err || '').trim().slice(0, 200), `(exit ${sent.code})`);
process.exit(sent.code === 0 ? 0 : 1);
