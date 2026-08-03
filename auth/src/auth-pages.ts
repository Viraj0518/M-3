/**
 * The three human-facing pages: sign in, authorize an agent, manage access.
 *
 * A class so a host can subclass it to reskin without forking the flow logic —
 * override `styles()` or any single page and the OAuth choreography underneath
 * is untouched.
 *
 * Deliberately dependency-free inline HTML: no build step, no bundle to fail
 * to fetch, and the pages load instantly on a bad connection.
 */
export interface AuthPagesOptions {
  /** Product name shown in the header. */
  brand?: string;
}

export class AuthPages {
  protected readonly brand: string;

  constructor(options: AuthPagesOptions = {}) {
    this.brand = options.brand ?? "Memory";
  }

  // ------------------------------------------------------------------ shell --
  protected shell(title: string, body: string): string {
    return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${title}</title>
<style>${this.styles()}</style>
</head>
<body><div class="card">${body}</div></body>
</html>`;
  }

  protected styles(): string {
    return `
  *, *::before, *::after { box-sizing: border-box; }
  :root {
    --bg: #07080c; --panel: #0e1017; --line: #1d212e; --text: #e6e9f2;
    --muted: #7d859c; --accent: #7cc4ff; --accent-dim: #1b3346; --danger: #ff7c9a;
  }
  html, body { height: 100%; }
  body {
    margin: 0;
    background: radial-gradient(900px 500px at 50% -10%, #101828 0%, transparent 70%), var(--bg);
    color: var(--text);
    font: 15px/1.55 ui-sans-serif, -apple-system, "Segoe UI", Inter, system-ui, sans-serif;
    display: grid; place-items: center; padding: 24px;
  }
  .card {
    width: 100%; max-width: 420px; background: var(--panel);
    border: 1px solid var(--line); border-radius: 14px; padding: 30px 28px;
    box-shadow: 0 24px 70px -30px rgba(0,0,0,.9);
  }
  .card.wide { max-width: 620px; }
  .brand {
    display: flex; align-items: center; gap: 9px; font-size: 12px;
    letter-spacing: .16em; text-transform: uppercase; color: var(--muted);
    margin-bottom: 22px;
  }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent); box-shadow: 0 0 12px var(--accent); }
  h1 { font-size: 21px; margin: 0 0 6px; letter-spacing: -.02em; font-weight: 600; }
  .sub { color: var(--muted); font-size: 13.5px; margin: 0 0 24px; }
  label { display: block; font-size: 11px; letter-spacing: .1em; text-transform: uppercase; color: var(--muted); margin: 0 0 7px; }
  input {
    width: 100%; padding: 11px 13px; margin-bottom: 16px; background: #080a10;
    color: var(--text); border: 1px solid var(--line); border-radius: 9px;
    font-size: 14.5px; font-family: inherit;
  }
  input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-dim); }
  button {
    width: 100%; padding: 11px 16px; border: 0; border-radius: 9px;
    background: var(--accent); color: #04121f; font-size: 14.5px; font-weight: 600;
    font-family: inherit; cursor: pointer;
  }
  button:hover { filter: brightness(1.08); }
  button:disabled { opacity: .55; cursor: default; filter: none; }
  button.ghost { background: transparent; color: var(--muted); border: 1px solid var(--line); margin-top: 9px; }
  button.ghost:hover { color: var(--text); border-color: #2b3244; }
  .msg { min-height: 19px; font-size: 13px; margin-top: 13px; }
  .msg.err { color: var(--danger); } .msg.ok { color: var(--accent); }
  .scopes { list-style: none; padding: 0; margin: 0 0 22px; }
  .scopes li { display: flex; align-items: center; gap: 10px; padding: 9px 0; border-bottom: 1px solid var(--line); font-size: 13.5px; color: #c2c8d8; }
  .scopes li:last-child { border-bottom: 0; }
  .tick { color: var(--accent); font-size: 12px; }
  .client {
    background: #080a10; border: 1px solid var(--line); border-radius: 9px;
    padding: 12px 14px; margin-bottom: 20px;
    font: 12.5px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
    color: var(--muted); word-break: break-all;
  }
  .client b { color: var(--text); font-weight: 600; }
  .foot { margin-top: 22px; padding-top: 16px; border-top: 1px solid var(--line); font-size: 12px; color: var(--muted); }
  .foot code { color: #97a1ba; }
  .who { display: flex; align-items: baseline; gap: 10px; margin-bottom: 4px; }
  .who h1 { margin: 0; } .who .email { color: var(--muted); font-size: 13px; }
  .uid { font: 11.5px/1.4 ui-monospace, Menlo, monospace; color: var(--muted); word-break: break-all; margin-bottom: 24px; }
  .sec { font-size: 11px; letter-spacing: .18em; text-transform: uppercase; color: var(--muted); margin: 26px 0 10px; }
  .row { display: flex; align-items: center; gap: 11px; padding: 11px 0; border-bottom: 1px solid var(--line); }
  .row:last-child { border-bottom: 0; }
  .swatch { width: 9px; height: 9px; border-radius: 50%; flex: 0 0 auto; }
  .row .name { flex: 1; font-size: 14px; }
  .row .kind { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; padding: 3px 7px; border: 1px solid var(--line); border-radius: 4px; color: var(--muted); }
  .row .when { font-size: 11.5px; color: var(--muted); }
  .empty { color: var(--muted); font-size: 13px; padding: 12px 0; }
  .ev { display: flex; gap: 10px; padding: 8px 0; border-bottom: 1px solid var(--line); font-size: 12.5px; }
  .ev:last-child { border-bottom: 0; }
  .ev .t { color: var(--muted); width: 74px; flex: 0 0 auto; font-variant-numeric: tabular-nums; }
  .ev .d { color: #b9c2d6; } .ev .q { color: var(--text); }
  .note { margin-top: 24px; padding: 12px 14px; background: #0a0d15; border: 1px solid var(--line); border-radius: 8px; font-size: 12px; color: var(--muted); line-height: 1.6; }
  .note code { color: var(--accent); }`;
  }

  protected static escape(s: string): string {
    return String(s ?? "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!,
    );
  }

  // ------------------------------------------------------------------ login --
  login(): string {
    return this.shell(
      `Sign in · ${this.brand}`,
      `
  <div class="brand"><span class="dot"></span> ${AuthPages.escape(this.brand)}</div>
  <h1>Sign in</h1>
  <p class="sub">Unlock your context for agents and the CLI.</p>

  <form id="f" autocomplete="on">
    <label for="email">Email</label>
    <input id="email" type="email" required autocomplete="username" placeholder="you@example.com" />
    <label for="password">Password</label>
    <input id="password" type="password" required autocomplete="current-password" placeholder="••••••••" minlength="8" />
    <button id="go" type="submit">Sign in</button>
    <button id="signup" class="ghost" type="button">Create account</button>
  </form>

  <div class="msg" id="msg"></div>
  <div class="foot">Signing in from a terminal instead? Run <code>mem login</code>.</div>

<script>
  const msg = document.getElementById('msg');
  const go = document.getElementById('go');
  const su = document.getElementById('signup');

  // When an agent's authorize request lands here, the server passes the
  // pending request as a SIGNED query string. Echoing it back verbatim is the
  // only link between "the human just signed in" and "an agent is waiting".
  const oauthQuery = location.search.replace(/^\\?/, '');

  const say = (t, k) => { msg.textContent = t; msg.className = 'msg ' + (k || ''); };

  // The continue endpoint dispatches on WHICH step just completed: it wants
  // one of postLogin / created / selected, and rejects the call outright
  // without one, stranding the agent's request with no visible error.
  async function resume(justCreated) {
    if (!oauthQuery) { location.href = '/account'; return; }
    try {
      const body = { oauth_query: oauthQuery };
      if (justCreated) body.created = true; else body.postLogin = true;

      const r = await fetch('/api/auth/oauth2/continue', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        credentials: 'include', body: JSON.stringify(body),
      });
      const data = await r.json().catch(() => ({}));
      const next = data.redirectURI || data.url || data.redirect;
      if (r.ok && next) { location.href = next; return; }
      say((data && data.error_description) || 'Signed in, but could not resume the agent request.', 'err');
    } catch (err) { say(String(err), 'err'); }
  }

  async function submit(path) {
    const email = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;
    if (!email || password.length < 8) { say('Password must be at least 8 characters.', 'err'); return; }

    go.disabled = su.disabled = true;
    say('Working…');

    const isSignup = path.includes('sign-up');
    const body = isSignup ? { email, password, name: email.split('@')[0] } : { email, password };

    try {
      const r = await fetch(path, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        credentials: 'include', body: JSON.stringify(body),
      });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        say(e.message || 'Sign in failed.', 'err');
        go.disabled = su.disabled = false;
        return;
      }
      say(isSignup ? 'Account created — resuming…' : 'Signed in — resuming…', 'ok');
      await resume(isSignup);
    } catch (err) {
      say(String(err), 'err');
      go.disabled = su.disabled = false;
    }
  }

  document.getElementById('f').addEventListener('submit', (e) => {
    e.preventDefault(); submit('/api/auth/sign-in/email');
  });
  su.addEventListener('click', () => submit('/api/auth/sign-up/email'));
</script>`,
    );
  }

  // ---------------------------------------------------------------- consent --
  consent(url: string): string {
    const q = new URL(url).searchParams;
    const clientId = q.get("client_id") ?? "unknown client";
    const clientName = q.get("client_name") ?? clientId;
    const scope = q.get("scope") ?? "openid profile email";

    const copy: Record<string, string> = {
      openid: "Confirm who you are",
      profile: "Read your name and avatar",
      email: "Read your email address",
      offline_access: "Stay connected when you are away",
    };

    const items = scope
      .split(/\s+/)
      .filter(Boolean)
      .map((s) => `<li><span class="tick">●</span> ${AuthPages.escape(copy[s] ?? s)}</li>`)
      .join("");

    return this.shell(
      `Authorize · ${this.brand}`,
      `
  <div class="brand"><span class="dot"></span> ${AuthPages.escape(this.brand)}</div>
  <h1>Authorize agent</h1>
  <p class="sub">An agent is asking to act on your behalf.</p>

  <div class="client"><b>${AuthPages.escape(clientName)}</b><br />${AuthPages.escape(clientId)}</div>
  <ul class="scopes">${items}</ul>

  <button id="allow" type="button">Allow access</button>
  <button id="deny" class="ghost" type="button">Deny</button>
  <div class="msg" id="msg"></div>

  <div class="foot">
    Every read this agent makes is recorded against it. Run <code>mem agents</code>
    to see who holds access, and <code>mem log</code> for the trail.
  </div>

<script>
  const msg = document.getElementById('msg');
  const allow = document.getElementById('allow');
  const deny = document.getElementById('deny');
  const scope = ${JSON.stringify(scope)};

  // The signed query the authorize endpoint handed us: it carries the pending
  // request and its signature. The server refuses to act on a consent
  // decision without it.
  const oauthQuery = location.search.replace(/^\\?/, '');
  const say = (t, k) => { msg.textContent = t; msg.className = 'msg ' + (k || ''); };

  async function decide(accept) {
    allow.disabled = deny.disabled = true;
    say(accept ? 'Authorizing…' : 'Denying…');
    try {
      const r = await fetch('/api/auth/oauth2/consent', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ accept, scope, oauth_query: oauthQuery }),
      });
      const data = await r.json().catch(() => ({}));
      const next = data.redirectURI || data.url || data.redirect;
      if (r.ok && next) { location.href = next; return; }
      if (r.ok) { say('Done. You can close this tab.', 'ok'); return; }
      say(data.error_description || data.message || 'Could not complete authorization.', 'err');
      allow.disabled = deny.disabled = false;
    } catch (err) {
      say(String(err), 'err');
      allow.disabled = deny.disabled = false;
    }
  }

  allow.addEventListener('click', () => decide(true));
  deny.addEventListener('click', () => decide(false));
</script>`,
    );
  }

  // ---------------------------------------------------------------- account --
  account(): string {
    return this.shell(
      `Account · ${this.brand}`,
      `
  <div class="brand"><span class="dot"></span> ${AuthPages.escape(this.brand)}</div>

  <div class="who"><h1 id="name">…</h1><span class="email" id="email"></span></div>
  <div class="uid" id="uid"></div>

  <div class="sec">Agents with access</div>
  <div id="agents"><div class="empty">Loading…</div></div>

  <div class="sec">Recent activity</div>
  <div id="events"><div class="empty">Loading…</div></div>

  <button id="out" class="ghost" type="button" style="margin-top:26px">Sign out</button>

  <div class="note">
    An agent gains access by completing the OAuth flow — it registers itself,
    you approve it here, and it receives a token scoped to you. Every read and
    write it makes is attributed.
  </div>

<script>
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));

  function ago(iso) {
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return Math.floor(s) + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }

  async function load() {
    const r = await fetch('/api/me', { credentials: 'include' });
    if (r.status === 401) { location.href = '/login'; return; }
    const d = await r.json();

    $('name').textContent = d.user.name || d.user.email;
    $('email').textContent = d.user.name ? d.user.email : '';
    $('uid').textContent = d.user.id;

    $('agents').innerHTML = d.agents.length
      ? d.agents.map((a) => \`
          <div class="row">
            <span class="swatch" style="background:\${esc(a.color)}"></span>
            <span class="name">\${esc(a.name)}</span>
            <span class="kind">\${esc(a.kind)}</span>
            <span class="when">\${ago(a.lastSeen)}</span>
          </div>\`).join('')
      : '<div class="empty">No agents yet. Connect one over MCP to see it here.</div>';

    const pr = await fetch('/api/provenance?limit=12', { credentials: 'include' });
    const p = pr.ok ? await pr.json() : { events: [] };

    $('events').innerHTML = p.events.length
      ? p.events.map((e) => {
          const n = (() => { try { return JSON.parse(e.resourceIds).length; } catch { return 0; } })();
          const what = e.eventType === 'resource.read'
            ? 'read ' + n + (e.query ? ' for <span class="q">"' + esc(e.query) + '"</span>' : '')
            : e.eventType === 'resource.written'
              ? 'wrote ' + n
              : esc(e.eventType).replace('.', ' ');
          return \`<div class="ev">
            <span class="t">\${ago(e.createdAt)}</span>
            <span class="d"><b style="color:\${esc(e.color || '#7d859c')}">\${esc(e.agentName || '—')}</b> \${what}</span>
          </div>\`;
        }).join('')
      : '<div class="empty">Nothing recorded yet.</div>';
  }

  $('out').addEventListener('click', async () => {
    await fetch('/api/auth/sign-out', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      credentials: 'include', body: '{}',
    });
    location.href = '/login';
  });

  load();
</script>`,
    );
  }
}
