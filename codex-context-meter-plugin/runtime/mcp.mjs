import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const APP_ROOT = path.join(ROOT, 'app');
const DATA_DIR = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'CodexContextMeter');
const STATE_FILE = path.join(DATA_DIR, 'widget-state.json');
const VERSION = '0.1.3';
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

function handoffPython() {
  const state = status().state || {};
  const configured = process.env.CONTEXT_METER_PYTHON || state.pythonExecutable;
  if (typeof configured === 'string' && configured) {
    const candidate = /pythonw\.exe$/i.test(configured)
      ? configured.replace(/pythonw\.exe$/i, 'python.exe')
      : configured;
    if (fs.existsSync(candidate)) return candidate;
  }
  return 'python.exe';
}

function writeHandoff(params) {
  return new Promise((resolve, reject) => {
    const python = handoffPython();
    const child = spawn(python, ['-m', 'codex_context_meter.handoff'], {
      cwd: APP_ROOT,
      env: {
        ...process.env,
        PYTHONUTF8: '1',
        PYTHONIOENCODING: 'utf-8',
      },
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    const timeout = setTimeout(() => {
      child.kill();
      reject(new Error('handoff writer timed out'));
    }, 15_000);
    child.stdout.on('data', chunk => {
      stdout = (stdout + chunk.toString('utf8')).slice(-1_000_000);
    });
    child.stderr.on('data', chunk => {
      stderr = (stderr + chunk.toString('utf8')).slice(-20_000);
    });
    child.once('error', error => {
      clearTimeout(timeout);
      reject(error);
    });
    child.once('close', code => {
      clearTimeout(timeout);
      if (code !== 0) {
        reject(new Error(stderr.trim() || `handoff writer exited with ${code}`));
        return;
      }
      try {
        const lines = stdout.trim().split(/\r?\n/);
        resolve(JSON.parse(lines[lines.length - 1]));
      } catch {
        reject(new Error('handoff writer returned invalid JSON'));
      }
    });
    child.stdin.end(JSON.stringify(params));
  });
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
  {
    name: 'context_meter_handoff',
    description: '生成结构化交接快照，并把它追加到同一 chainId 的完整历史链和索引中。',
    inputSchema: {
      type: 'object',
      properties: {
        chainId: { type: 'string', description: '跨多个会话保持不变的交接链名称。' },
        summary: { type: 'string', description: '当前目标和状态摘要。' },
        status: { type: 'string', description: 'active、paused、blocked 或 complete。' },
        reason: { type: 'string', description: '触发交接的原因。' },
        completed: { type: 'array', items: { type: 'string' } },
        pending: { type: 'array', items: { type: 'string' } },
        decisions: { type: 'array', items: { type: 'string' } },
        files: { type: 'array', items: { type: 'string' } },
        blockers: { type: 'array', items: { type: 'string' } },
        verification: { type: 'array', items: { type: 'string' } },
        doNotCarry: {
          type: 'array',
          items: { type: 'string' },
          description: '已失效但仍必须保留在历史链中的旧事项。',
        },
        nextAction: { type: 'string', description: '新会话应立即执行的动作。' },
      },
      required: ['chainId', 'summary', 'nextAction'],
      additionalProperties: false,
    },
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
      else if (params.name === 'context_meter_handoff') output = await writeHandoff(params.arguments || {});
      else throw new Error('unknown tool');
      result = { content: [{ type: 'text', text: JSON.stringify(output) }], isError: output.ok === false };
    } else {
      send({ jsonrpc: '2.0', id, error: { code: -32601, message: 'Method not found' } });
      return;
    }
    send({ jsonrpc: '2.0', id, result });
  } catch (error) {
    send({
      jsonrpc: '2.0',
      id,
      error: {
        code: -32603,
        message: 'Context meter action failed',
        data: { detail: String(error?.message || error).slice(0, 2_000) },
      },
    });
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
