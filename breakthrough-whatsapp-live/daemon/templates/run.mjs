#!/usr/bin/env node
// run.mjs: the launchd parent of the WhatsApp Live daemon.
//
// Why node and not python directly: macOS privacy protection (TCC). When launchd starts
// /usr/bin/python3 itself, python may read the WhatsApp database, but its children
// (ffmpeg, whisperkit-cli, claude) stall on a privacy prompt nobody can answer: zero
// output, zero CPU, until a timeout. With a node binary that holds Full Disk Access as
// the top of the process tree, every child inherits the grant. Grant Full Disk Access
// to the exact node binary named in the plist (System Settings, Privacy and Security,
// Full Disk Access). A grant to the terminal does not carry over to launchd.
//
// Nothing is hardcoded here. Installed by `wa_setup.py render-launchd` as
// <home>/launchd/run.mjs, started by the plist as:
//   node run.mjs <python> <wa_live.py> [arguments for wa_live.py ...]
// When the first two arguments are missing, the environment is used instead:
//   WA_LIVE_PYTHON (default /usr/bin/python3), WA_LIVE_SCRIPT, WA_LIVE_CONFIG
// WA_LIVE_CONFIG is appended as `--config <path>` when no --config argument is given.
//
// It forwards SIGTERM, SIGINT and SIGHUP to python and exits only after python exits,
// with python's exit code, so launchd's ExitTimeOut gives running jobs time to finish.
// Its own lines go to stderr, which the plist sends to <live>/stderr.log.

import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';

function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function say(line) {
  try { process.stderr.write(`[${stamp()}] run.mjs: ${line}\n`); } catch (e) { /* stderr gone, nothing to do */ }
}

const argv = process.argv.slice(2);
let python;
let script;
let rest;
if (argv.length >= 2 && !argv[0].startsWith('--') && !argv[1].startsWith('--')) {
  python = argv[0];
  script = argv[1];
  rest = argv.slice(2);
} else {
  python = process.env.WA_LIVE_PYTHON || '/usr/bin/python3';
  script = process.env.WA_LIVE_SCRIPT || '';
  rest = argv.slice();
}
if (!rest.includes('--config') && process.env.WA_LIVE_CONFIG) {
  rest.push('--config', process.env.WA_LIVE_CONFIG);
}

if (!script) {
  say('no daemon script given. Usage: node run.mjs <python> <wa_live.py> [args], or set WA_LIVE_SCRIPT. Rerun wa_setup.py render-launchd.');
  process.exit(2);
}
for (const [what, p] of [['python', python], ['daemon script', script]]) {
  if (!existsSync(p)) {
    say(`${what} not found at ${p}. Rerun wa_setup.py render-launchd after checking bin.python and the payload location.`);
    process.exit(2);
  }
}

const xpc = process.env.XPC_SERVICE_NAME;
const label = xpc && xpc !== '0' ? `launchd label ${xpc}` : 'not under launchd';
say(`start: node ${process.version} pid ${process.pid} (${label}) spawning ${python} ${script} ${rest.join(' ')}`);

const startedAt = Date.now();
const child = spawn(python, [script, ...rest], { stdio: 'inherit', env: process.env });

for (const sig of ['SIGTERM', 'SIGINT', 'SIGHUP']) {
  process.on(sig, () => {
    say(`received ${sig}, forwarding to python pid ${child.pid}`);
    try { child.kill(sig); } catch (e) { /* python already gone */ }
  });
}

child.on('error', (e) => {
  say(`python could not start: ${e.message}`);
  process.exit(1);
});

child.on('exit', (code, signal) => {
  const secs = Math.round((Date.now() - startedAt) / 1000);
  say(`python exited code=${code === null ? 'none' : code} signal=${signal || 'none'} after ${secs}s`);
  process.exit(code === null ? 1 : code);
});
