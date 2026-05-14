const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const url = require('url');

const PORT = 3000;

function httpsGet(reqUrl) {
  return new Promise((resolve, reject) => {
    https.get(reqUrl, { headers: { 'User-Agent': 'Mozilla/5.0' } }, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve({ status: res.statusCode, body: data }));
    }).on('error', reject);
  });
}

function httpsPost(reqUrl, body, headers) {
  return new Promise((resolve, reject) => {
    const parsed = url.parse(reqUrl);
    const options = {
      hostname: parsed.hostname,
      path: parsed.path,
      method: 'POST',
      headers: { ...headers, 'Content-Length': Buffer.byteLength(body) }
    };
    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => resolve({ status: res.statusCode, body: data }));
    });
    req.on('error', reject);
    req.write(body);
    req.end();
  });
}

const server = http.createServer(async (req, res) => {
  // CORS headers for all responses
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, x-api-key, anthropic-version');

  if (req.method === 'OPTIONS') {
    res.writeHead(204); res.end(); return;
  }

  const parsedUrl = url.parse(req.url, true);

  // ── Serve the HTML file ──
  if (parsedUrl.pathname === '/' || parsedUrl.pathname === '/index.html') {
    const html = fs.readFileSync(path.join(__dirname, 'index.html'));
    res.writeHead(200, { 'Content-Type': 'text/html' });
    res.end(html); return;
  }

  // ── Yahoo Finance proxy ──
  if (parsedUrl.pathname === '/yahoo') {
    const ticker = parsedUrl.query.ticker;
    if (!ticker) { res.writeHead(400); res.end('Missing ticker'); return; }
    try {
      const yahooUrl = `https://query1.finance.yahoo.com/v8/finance/chart/${ticker}?interval=1d&range=5d`;
      const result = await httpsGet(yahooUrl);
      res.writeHead(result.status, { 'Content-Type': 'application/json' });
      res.end(result.body);
    } catch(e) {
      // try query2
      try {
        const result = await httpsGet(`https://query2.finance.yahoo.com/v8/finance/chart/${ticker}?interval=1d&range=5d`);
        res.writeHead(result.status, { 'Content-Type': 'application/json' });
        res.end(result.body);
      } catch(e2) {
        res.writeHead(502); res.end(JSON.stringify({ error: e2.message }));
      }
    }
    return;
  }

  // ── Anthropic API proxy ──
  if (parsedUrl.pathname === '/claude' && req.method === 'POST') {
    let body = '';
    req.on('data', chunk => body += chunk);
    req.on('end', async () => {
      const apiKey = req.headers['x-api-key'];
      if (!apiKey) { res.writeHead(401); res.end(JSON.stringify({ error: 'No API key' })); return; }
      try {
        const result = await httpsPost(
          'https://api.anthropic.com/v1/messages',
          body,
          { 'Content-Type': 'application/json', 'x-api-key': apiKey, 'anthropic-version': '2023-06-01' }
        );
        res.writeHead(result.status, { 'Content-Type': 'application/json' });
        res.end(result.body);
      } catch(e) {
        res.writeHead(502); res.end(JSON.stringify({ error: e.message }));
      }
    });
    return;
  }

  res.writeHead(404); res.end('Not found');
});

server.listen(PORT, () => {
  console.log('');
  console.log('  ✅ Trading Analysis Desk is running!');
  console.log(`  👉 Open this in your browser: http://localhost:${PORT}`);
  console.log('');
  console.log('  Press Ctrl+C to stop.');
  console.log('');
});
