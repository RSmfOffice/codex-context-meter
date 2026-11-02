import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DATA_DIR = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'CodexContextMeter');
const STATE_FILE = path.join(DATA_DIR, 'widget-state.json');
const VERSION = '0.1.0';
const VBS = path.join(ROOT, 'scripts', 'launch-hidden.vbs');
const STOP_SCRIPT = path.join(ROOT, 'scripts', 'stop.ps1');

function readJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return fallback; }
}

function launchHidden(script = VBS) {
  return new Promise((resolve, reject) => {
    try { fs.rmSync(path.join(DATA_DIR, 'pause-until-host-exit.json'), { force: true }); } catch {}
    const child = spawn('wscript.exe', [script], {
      cwd: ROOT,
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
    });
    child.once('error', reject);
    child.once('spawn', () => {
      child.unref();
      resolve({ ok: true, action: 'start', script });
    });
  });
}

function stopWorker() {
  return new Promise((resolve, reject) => {
    const child = spawn('powershell.exe', [
      '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
      '-File', STOP_SCRIPT,
    ], { cwd: ROOT, windowsHide: true, stdio: 'ignore' });
    child.once('error', reject);
    child.once('exit', code => resolve({ ok: code === 0, action: 'stop', exitCode: code }));
  });
}

function status() {
  const state = readJson(STATE_FILE, null);
  const ageMs = state?.updatedAt ? Date.now() - Number(state.updatedAt) * 1000 : null;
  return {
    ok: Boolean(state?.ok),
    running: Boolean(state?.ok && ageMs !== null && ageMs < 5000),
    ageMs,
    state,
  };
}

const tools = [
  {
    name: 'context_meter_status',
    description: '读取 Codex 单会话上下文监控状态和当前统计。',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    annotations: { readOnlyHint: true },
  },
  {
    name: 'context_meter_show',
    description: '启动或恢复 Codex 上下文监控悬浮组件。',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
  },
  {
    name: 'context_meter_stop',
    description: '停止当前 Codex 上下文监控悬浮组件。',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    annotations: { readOnlyHint: false, destructiveHint: false, openWorldHint: false },
  },
];

const send = value => process.stdout.write(`${JSON.stringify(value)}\n`);
async function dispatch(message) {
  const { id, method, params = {} } = message;
  if (id === undefined) return;
  try {
    let result;
    if (method === 'initialize') {
      result = {
        protocolVersion: ['2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05'].includes(params.protocolVersion)
          ? params.protocolVersion : '2025-06-18',
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: 'codex-context-meter', version: VERSION },
      };
      void launchHidden();
    } else if (method === 'ping') {
      result = {};
    } else if (method === 'tools/list') {
      result = { tools };
    } else if (method === 'resources/list' || method === 'resources/templates/list') {
      result = method === 'resources/list' ? { resources: [] } : { resourceTemplates: [] };
    } else if (method === 'tools/call') {
      let output;
      if (params.name === 'context_meter_status') output = status();
      else if (params.name === 'context_meter_show') output = await launchHidden();
      else if (params.name === 'context_meter_stop') output = await stopWorker();
      else throw new Error('unknown tool');
      result = { content: [{ type: 'text', text: JSON.stringify(output) }], isError: output.ok === false };
    } else {
      send({ jsonrpc: '2.0', id, error: { code: -32601, message: 'Method not found' } });
      return;
    }
    send({ jsonrpc: '2.0', id, result });
  } catch {
    send({ jsonrpc: '2.0', id, error: { code: -32603, message: 'Context meter action failed' } });
  }
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', line => {
  if (line.length > 1024 * 1024) return;
  if (!line.trim()) return;
  try {
    const message = JSON.parse(line);
    if (message.jsonrpc === '2.0') dispatch(message);
  } catch {
    send({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } });
  }
});
input.on('close', () => process.exit(0));
