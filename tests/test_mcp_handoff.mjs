import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'codex-meter-handoff-'));
const localAppData = path.join(dataDir, 'LocalAppData');
const meterDir = path.join(localAppData, 'CodexContextMeter');
const projectDir = path.join(dataDir, 'project');
const codexHome = path.join(localAppData, 'codex-home');
fs.mkdirSync(meterDir, { recursive: true });
fs.mkdirSync(projectDir, { recursive: true });
fs.mkdirSync(codexHome, { recursive: true });
fs.writeFileSync(
  path.join(meterDir, 'widget-state.json'),
  JSON.stringify({
    ok: true,
    title: 'MCP handoff test',
    threadId: '01a187b3-00ff-7270-909e-c60b1d3e814f',
    pythonExecutable: process.env.CONTEXT_METER_TEST_PYTHON || '',
    estimatedTokens: 1_000,
    contextWindow: 10_000,
    remainingTokens: 9_000,
    percent: 10,
    cacheHitRate: 95,
    payloadRisk: 'normal',
    workspacePath: projectDir,
  }),
  'utf8',
);

const child = spawn(
  process.execPath,
  [path.join(ROOT, 'codex-context-meter-plugin', 'runtime', 'mcp.mjs')],
  {
    cwd: ROOT,
    env: {
      ...process.env,
      LOCALAPPDATA: localAppData,
      CODEX_HOME: codexHome,
    },
    windowsHide: true,
    stdio: ['pipe', 'pipe', 'pipe'],
  },
);
const lines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
const pending = new Map();
const closed = new Promise(resolve => child.once('close', resolve));
let stderr = '';
child.stderr.on('data', chunk => {
  stderr += chunk.toString('utf8');
});
lines.on('line', line => {
  const message = JSON.parse(line);
  const resolve = pending.get(message.id);
  if (resolve) {
    pending.delete(message.id);
    resolve(message);
  }
});

function request(id, method, params = {}) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`MCP request timed out: ${method}\n${stderr}`));
    }, 20_000);
    pending.set(id, message => {
      clearTimeout(timeout);
      resolve(message);
    });
    child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
  });
}

try {
  const listed = await request(1, 'tools/list');
  const names = listed.result.tools.map(tool => tool.name);
  if (!names.includes('context_meter_handoff')) {
    throw new Error('context_meter_handoff is missing');
  }

  const firstMessage = await request(2, 'tools/call', {
    name: 'context_meter_handoff',
    arguments: {
      chainId: 'continuous-transfer',
      summary: '第一段必须保留',
      completed: ['阶段一完成'],
      pending: ['阶段二待办'],
      nextAction: '进入阶段二',
    },
  });
  if (!firstMessage.result?.content?.[0]?.text) {
    throw new Error(`first handoff failed: ${JSON.stringify(firstMessage)}`);
  }
  const first = JSON.parse(firstMessage.result.content[0].text);
  if (!first.ok || first.handoffCount !== 1) {
    throw new Error(`unexpected first handoff: ${JSON.stringify(first)}`);
  }

  const secondMessage = await request(3, 'tools/call', {
    name: 'context_meter_handoff',
    arguments: {
      chainId: 'continuous-transfer',
      summary: '第二段必须保留',
      completed: ['阶段二完成'],
      pending: ['阶段三待办'],
      nextAction: '进入阶段三',
    },
  });
  if (!secondMessage.result?.content?.[0]?.text) {
    throw new Error(`second handoff failed: ${JSON.stringify(secondMessage)}`);
  }
  const second = JSON.parse(secondMessage.result.content[0].text);
  if (!second.ok || second.handoffCount !== 2) {
    throw new Error(`unexpected second handoff: ${JSON.stringify(second)}`);
  }

  const archive = fs.readFileSync(second.chainContextPath, 'utf8');
  if (!archive.includes('第一段必须保留') || !archive.includes('第二段必须保留')) {
    throw new Error('chain context omitted an earlier handoff');
  }
  const index = JSON.parse(fs.readFileSync(second.indexPath, 'utf8'));
  if (
    index.handoffVersion !== 2 ||
    !index.coverageComplete ||
    index.handoffCount !== 2 ||
    index.handoffs.length !== 2
  ) {
    throw new Error('handoff index is incomplete');
  }
  if (!fs.existsSync(path.join(projectDir, 'HANDOFF.md'))) {
    throw new Error('project HANDOFF.md was not created');
  }
  const projectArchive = fs.readFileSync(
    path.join(
      projectDir,
      '.codex',
      'handoffs',
      'codex-context-meter',
      'chain-context.md',
    ),
    'utf8',
  );
  if (!projectArchive.includes('第一段必须保留') || !projectArchive.includes('第二段必须保留')) {
    throw new Error('project handoff mirror omitted history');
  }

  const fallbackState = {
    ok: true,
    title: 'Projectless handoff test',
    threadId: '01a187b3-00ff-7270-909e-c60b1d3e8160',
    pythonExecutable: process.env.CONTEXT_METER_TEST_PYTHON || '',
    estimatedTokens: 500,
    contextWindow: 10_000,
    remainingTokens: 9_500,
    percent: 5,
    payloadRisk: 'normal',
  };
  fs.writeFileSync(
    path.join(meterDir, 'widget-state.json'),
    JSON.stringify(fallbackState),
    'utf8',
  );
  const fallbackMessage = await request(4, 'tools/call', {
    name: 'context_meter_handoff',
    arguments: {
      chainId: 'projectless-transfer',
      summary: '无项目目录也必须可发现',
      nextAction: '从 CODEX_HOME 读取完整交接',
    },
  });
  if (!fallbackMessage.result?.content?.[0]?.text) {
    throw new Error(`projectless handoff failed: ${JSON.stringify(fallbackMessage)}`);
  }
  const fallback = JSON.parse(fallbackMessage.result.content[0].text);
  const globalArchive = fs.readFileSync(
    path.join(codexHome, 'handoffs', 'codex-context-meter', 'chain-context.md'),
    'utf8',
  );
  if (!globalArchive.includes('无项目目录也必须可发现')) {
    throw new Error('CODEX_HOME fallback omitted handoff content');
  }
  if (!fs.existsSync(path.join(codexHome, 'handoffs', 'HANDOFF.md'))) {
    throw new Error('CODEX_HOME HANDOFF.md pointer was not created');
  }

  console.log(JSON.stringify({
    ok: true,
    handoffCount: second.handoffCount,
    chainContextPath: second.chainContextPath,
    indexPath: second.indexPath,
    projectPointer: path.join(projectDir, 'HANDOFF.md'),
    fallbackPointer: path.join(codexHome, 'handoffs', 'HANDOFF.md'),
    fallbackChainId: fallback.chainId,
  }));
} finally {
  child.stdin.end();
  child.kill();
  await Promise.race([
    closed,
    new Promise(resolve => setTimeout(resolve, 1_500)),
  ]);
  try {
    fs.rmSync(dataDir, {
      recursive: true,
      force: true,
      maxRetries: 8,
      retryDelay: 100,
    });
  } catch {
    // Windows may retain a short-lived lock after the child exits.
  }
}
