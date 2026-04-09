const vscode = require('vscode');
const https = require('https');
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const querystring = require('querystring');

// ── Config ────────────────────────────────────────────────────────────────────
const USAGE_URL = 'https://api.anthropic.com/api/oauth/usage';
const TOKEN_REFRESH_URL = 'https://claude.ai/api/auth/oauth/token';
const CREDENTIALS_PATH = path.join(os.homedir(), '.claude', '.credentials.json');
const POLL_INTERVAL_MS = 5 * 60 * 1000; // 5 minutes
const WARN_THRESHOLD = 75;
const CRIT_THRESHOLD = 90;

// ── State ─────────────────────────────────────────────────────────────────────
let statusBarItem;
let detailPanel;
let pollTimer;
let backoffMs = 0;
const BACKOFF_MAX_MS = 30 * 60 * 1000;

let lastData = null;
let lastError = null;
let lastUpdated = null;
let lastDataJson = null; // for change detection

let accessToken = null;
let refreshTok = null;
let expiresAt = null; // ms epoch

// ── Activation ────────────────────────────────────────────────────────────────
function activate(context) {
    statusBarItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    statusBarItem.command = 'claudeUsage.showDetail';
    statusBarItem.text = '$(pulse) Claude: loading…';
    statusBarItem.tooltip = 'Claude Code Usage — click for details';
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    context.subscriptions.push(
        vscode.commands.registerCommand('claudeUsage.showDetail', showDetailPanel),
        vscode.commands.registerCommand('claudeUsage.refresh', () => poll(true))
    );

    poll(true);
    pollTimer = setInterval(() => poll(false), POLL_INTERVAL_MS);
    context.subscriptions.push({ dispose: () => clearInterval(pollTimer) });
}

function deactivate() {
    clearInterval(pollTimer);
}

// ── Credentials ───────────────────────────────────────────────────────────────
function loadCredentials() {
    try {
        const raw = JSON.parse(fs.readFileSync(CREDENTIALS_PATH, 'utf8'));
        const oauth = raw.claudeAiOauth || {};
        const token = oauth.accessToken || raw.oauthToken || raw.accessToken;
        return {
            accessToken: token || null,
            refreshToken: oauth.refreshToken || null,
            expiresAt: oauth.expiresAt || null,
        };
    } catch (_) {}
    return {
        accessToken: process.env.ANTHROPIC_AUTH_TOKEN || process.env.CLAUDE_TOKEN || null,
        refreshToken: null,
        expiresAt: null,
    };
}

function persistToken(newToken, newExpiresAt, newRefreshToken) {
    try {
        const raw = JSON.parse(fs.readFileSync(CREDENTIALS_PATH, 'utf8'));
        const oauth = raw.claudeAiOauth || (raw.claudeAiOauth = {});
        oauth.accessToken = newToken;
        if (newExpiresAt) oauth.expiresAt = newExpiresAt;
        if (newRefreshToken) oauth.refreshToken = newRefreshToken;
        fs.writeFileSync(CREDENTIALS_PATH, JSON.stringify(raw, null, 2), 'utf8');
    } catch (_) {}
}

// ── HTTP helpers ──────────────────────────────────────────────────────────────
function httpsRequest(options, body) {
    return new Promise((resolve, reject) => {
        const mod = options.protocol === 'http:' ? http : https;
        const req = mod.request(options, res => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => resolve({ status: res.statusCode, body: data }));
        });
        req.on('error', reject);
        req.setTimeout(10000, () => { req.destroy(); reject(new Error('timeout')); });
        if (body) req.write(body);
        req.end();
    });
}

// ── Token refresh ─────────────────────────────────────────────────────────────
async function doRefreshToken(tok) {
    try {
        const body = querystring.stringify({ grant_type: 'refresh_token', refresh_token: tok });
        const url = new URL(TOKEN_REFRESH_URL);
        const res = await httpsRequest({
            hostname: url.hostname,
            path: url.pathname,
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': Buffer.byteLength(body),
            },
        }, body);
        if (res.status !== 200) return null;
        const json = JSON.parse(res.body);
        if (!json.access_token) return null;
        const newExpiry = json.expires_in ? Date.now() + json.expires_in * 1000 : null;
        persistToken(json.access_token, newExpiry, json.refresh_token || null);
        return { accessToken: json.access_token, expiresAt: newExpiry, refreshToken: json.refresh_token || tok };
    } catch (_) {
        return null;
    }
}

// ── Fetch usage ───────────────────────────────────────────────────────────────
async function fetchUsage(token) {
    const url = new URL(USAGE_URL);
    const res = await httpsRequest({
        hostname: url.hostname,
        path: url.pathname,
        method: 'GET',
        headers: {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
            'User-Agent': 'claude-code/2.0.32',
            'Authorization': `Bearer ${token}`,
            'anthropic-beta': 'oauth-2025-04-20',
        },
    });
    if (res.status === 200) return JSON.parse(res.body);
    if (res.status === 401) throw Object.assign(new Error('Token expired'), { code: 401 });
    if (res.status === 429) throw Object.assign(new Error('Rate limited'), { code: 429 });
    throw new Error(`HTTP ${res.status}`);
}

function applyRefreshed(refreshed) {
    accessToken = refreshed.accessToken;
    expiresAt = refreshed.expiresAt;
    refreshTok = refreshed.refreshToken;
}

function notifyIfChanged(newData, newError) {
    const newJson = JSON.stringify(newData);
    if (newJson === lastDataJson && newError === lastError) return;
    lastDataJson = newJson;
    updateStatusBar();
    updateDetailPanel();
}

// ── Poll ──────────────────────────────────────────────────────────────────────
async function poll(force) {
    const creds = loadCredentials();
    if (creds.accessToken) {
        accessToken = creds.accessToken;
        refreshTok = creds.refreshToken;
        expiresAt = creds.expiresAt;
    }

    if (!accessToken) {
        const err = 'No token found — open Claude Code to sign in';
        notifyIfChanged(null, err);
        lastError = err;
        lastData = null;
        return;
    }

    // Proactive token refresh if expiring within 60s
    if (expiresAt && refreshTok && Date.now() >= expiresAt - 60000) {
        const refreshed = await doRefreshToken(refreshTok);
        if (refreshed) applyRefreshed(refreshed);
    }

    try {
        let data;
        try {
            data = await fetchUsage(accessToken);
        } catch (err) {
            if (err.code === 401 && refreshTok) {
                const refreshed = await doRefreshToken(refreshTok);
                if (refreshed) {
                    applyRefreshed(refreshed);
                    data = await fetchUsage(accessToken);
                } else {
                    throw err;
                }
            } else {
                throw err;
            }
        }

        const prevError = lastError;
        lastData = data;
        lastError = null;
        lastUpdated = new Date();
        backoffMs = 0;
        notifyIfChanged(data, null);
    } catch (err) {
        const msg = err.message || 'Unknown error';
        notifyIfChanged(null, msg);
        lastError = msg;
        lastData = null;

        if (err.code === 429) {
            backoffMs = backoffMs === 0 ? 60000 : Math.min(backoffMs * 2, BACKOFF_MAX_MS);
            setTimeout(() => poll(false), backoffMs);
        }
    }
}

// ── Status bar ────────────────────────────────────────────────────────────────
function updateStatusBar() {
    if (lastError) {
        statusBarItem.text = '$(warning) Claude: error';
        statusBarItem.tooltip = `Claude Code Usage\n${lastError}\nClick for details`;
        statusBarItem.backgroundColor = undefined;
        statusBarItem.color = new vscode.ThemeColor('statusBarItem.warningForeground');
        return;
    }
    if (!lastData) {
        statusBarItem.text = '$(pulse) Claude: loading…';
        statusBarItem.tooltip = 'Claude Code Usage — loading…';
        statusBarItem.backgroundColor = undefined;
        statusBarItem.color = undefined;
        return;
    }

    const fh = lastData.five_hour;
    const sd = lastData.seven_day;
    const fhPct = fh ? Math.round(fh.utilization) : null;
    const sdPct = sd ? Math.round(sd.utilization) : null;

    const parts = [];
    if (fhPct !== null) parts.push(`5h: ${fhPct}%`);
    if (sdPct !== null) parts.push(`7d: ${sdPct}%`);

    statusBarItem.text = `$(pulse) ${parts.join(' │ ')}`;

    const maxPct = Math.max(fhPct ?? 0, sdPct ?? 0);
    if (maxPct >= CRIT_THRESHOLD) {
        statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
        statusBarItem.color = undefined;
    } else if (maxPct >= WARN_THRESHOLD) {
        statusBarItem.backgroundColor = new vscode.ThemeColor('statusBarItem.warningBackground');
        statusBarItem.color = undefined;
    } else {
        statusBarItem.backgroundColor = undefined;
        statusBarItem.color = undefined;
    }

    const lines = ['Claude Code Usage — click for details'];
    if (fh?.resets_at) lines.push(`5h resets: ${fmtResetFull(fh.resets_at)}`);
    if (sd?.resets_at) lines.push(`7d resets: ${fmtResetFull(sd.resets_at)}`);
    if (lastUpdated) lines.push(`Updated: ${fmtRelative(lastUpdated)}`);
    statusBarItem.tooltip = lines.join('\n');
}

// ── Detail panel ──────────────────────────────────────────────────────────────
function showDetailPanel() {
    if (detailPanel) {
        detailPanel.reveal();
        return;
    }
    detailPanel = vscode.window.createWebviewPanel(
        'claudeUsage.detail',
        'Claude Code Usage',
        vscode.ViewColumn.Beside,
        { enableScripts: false, retainContextWhenHidden: false }
    );
    detailPanel.onDidDispose(() => { detailPanel = null; });
    renderDetailPanel();
}

function updateDetailPanel() {
    if (detailPanel) renderDetailPanel();
}

function renderDetailPanel() {
    if (!detailPanel) return;
    detailPanel.webview.html = buildHtml();
}

// ── HTML ──────────────────────────────────────────────────────────────────────
function buildHtml() {
    const fh = lastData?.five_hour;
    const sd = lastData?.seven_day;

    const fhPct = fh ? Math.round(fh.utilization) : null;
    const sdPct = sd ? Math.round(sd.utilization) : null;

    function barColor(pct) {
        if (pct >= CRIT_THRESHOLD) return '#f14c4c';
        if (pct >= WARN_THRESHOLD) return '#cca700';
        return '#4ec9b0';
    }

    function barSection(title, pct, resetStr) {
        if (pct === null) return '';
        const color = barColor(pct);
        const resetLabel = resetStr ? fmtResetFull(resetStr) : '—';
        return `
        <div class="section">
            <div class="row">
                <span class="title">${title}</span>
                <span class="pct" style="color:${color}">${pct}%</span>
            </div>
            <div class="track">
                <div class="fill" style="width:${Math.min(pct, 100)}%;background:${color}"></div>
            </div>
            <div class="caption">Resets: ${resetLabel}</div>
        </div>`;
    }

    let content;
    if (lastError) {
        content = `<div class="error">${escHtml(lastError)}</div>`;
    } else if (!lastData) {
        content = `<div class="loading">Loading…</div>`;
    } else {
        content = barSection('5h Session', fhPct, fh?.resets_at)
                + barSection('7d Weekly', sdPct, sd?.resets_at);
    }

    const updatedLine = lastUpdated
        ? `<div class="updated">Updated ${fmtRelative(lastUpdated)}</div>`
        : '';

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--vscode-font-family, 'Segoe UI', sans-serif);
    font-size: var(--vscode-font-size, 13px);
    background: var(--vscode-editor-background, #1e1e1e);
    color: var(--vscode-editor-foreground, #cccccc);
    padding: 24px;
  }
  h2 {
    font-size: 14px;
    font-weight: 600;
    margin-bottom: 20px;
    opacity: 0.7;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .section { margin-bottom: 24px; }
  .row { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
  .title { font-size: 13px; font-weight: 500; }
  .pct { font-size: 15px; font-weight: 700; }
  .track {
    height: 8px;
    border-radius: 4px;
    background: var(--vscode-input-background, #3c3c3c);
    overflow: hidden;
  }
  .fill {
    height: 100%;
    border-radius: 4px;
    transition: width 0.4s ease;
  }
  .caption {
    margin-top: 5px;
    font-size: 11px;
    opacity: 0.55;
  }
  .error {
    color: #f14c4c;
    font-size: 12px;
    padding: 12px;
    border: 1px solid #f14c4c44;
    border-radius: 4px;
    background: #f14c4c11;
  }
  .loading { opacity: 0.5; font-size: 12px; }
  .updated { margin-top: 12px; font-size: 11px; opacity: 0.4; }
</style>
</head>
<body>
  <h2>Claude Code Usage</h2>
  ${content}
  ${updatedLine}
</body>
</html>`;
}

function escHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── Time helpers ──────────────────────────────────────────────────────────────
function fmtResetFull(isoStr) {
    try {
        return new Date(isoStr).toLocaleString();
    } catch (_) {
        return isoStr;
    }
}

function fmtRelative(date) {
    const delta = Math.round((Date.now() - date.getTime()) / 1000);
    if (delta < 5) return 'just now';
    if (delta < 60) return `${delta}s ago`;
    if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
    return `${Math.floor(delta / 3600)}h ago`;
}

module.exports = { activate, deactivate };
