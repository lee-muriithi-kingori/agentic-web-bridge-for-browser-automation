"""Find open GitHub bounties worth solving."""
import json
import time
import urllib.parse
import urllib.request


def get(p, t=10):
    return json.loads(urllib.request.urlopen('http://127.0.0.1:9876' + p, timeout=t).read())


def post(p, b, t=10):
    req = urllib.request.Request(
        'http://127.0.0.1:9876' + p,
        data=json.dumps(b).encode(),
        method='POST',
        headers={'Content-Type': 'application/json'},
    )
    return json.loads(urllib.request.urlopen(req, timeout=t).read())


def run(ctype, args=None, ms=15000):
    cid = f'{ctype}-{int(time.time() * 1000000)}'
    post('/cmd', {'id': cid, 'type': ctype, 'args': args or {}})
    r = get('/result?id=' + cid + '&wait=' + str(ms), t=ms / 1000 + 1)
    if not r.get('ok'):
        return {'_err': 'no_result', 'raw': r}
    inner = r.get('result') or {}
    if not inner.get('ok'):
        return {'_err': inner.get('error')}
    return inner.get('value')


def ok(v):
    return not (isinstance(v, dict) and '_err' in v)


def show(label, v, maxlen=300):
    if isinstance(v, dict) and '_err' in v:
        print(f'  {label}: ERR — {v["_err"]}')
    else:
        s = json.dumps(v, default=str)
        if len(s) > maxlen:
            s = s[:maxlen] + '…'
        print(f'  {label}: {s}')


# 1) GitHub bounty-tagged issues, sorted by recent activity
URL = 'https://github.com/issues?q=is%3Aopen+is%3Aissue+label%3Abounty+sort%3Aupdated-desc'
print('=== STEP 1: navigate to bounty-tagged issues ===')
print('  url:', URL)
r = run('navigate', {'url': URL}, 12000)
show('navigate', r, 200)
time.sleep(5)

# 2) extract the issue list from the DOM
EXTRACT = """(() => {
    const items = document.querySelectorAll('div[role="listitem"].js-issue-row, div.js-issue-row, a[data-hovercard-type="issue"]');
    const out = [];
    const seen = new Set();
    for (const it of items) {
        const a = it.matches('a') ? it : it.querySelector('a[href*="/issues/"]');
        if (!a) continue;
        const href = a.href;
        if (seen.has(href) || !href.includes('/issues/')) continue;
        seen.add(href);
        // Issue title is the link text
        const title = (a.innerText || '').trim().split('\\n')[0];
        // Try to find repo from the issue list layout
        const repoEl = it.querySelector('[data-hovercard-type="repository"]') || document.querySelector('a[data-hovercard-type="repository"][href*="' + href.split('/issues/')[0].split('github.com/')[1] + '"]');
        let repo = '';
        const m = href.match(/github\\.com\\/([^\\/]+\\/[^\\/]+)\\/issues\\/(\\d+)/);
        if (m) repo = m[1];
        // Labels
        const labels = Array.from(it.querySelectorAll('.IssueLabel, [class*="label"]')).map(e => (e.innerText || '').trim()).filter(Boolean);
        // Money mentioned?
        const moneyMatch = title.match(/\\$\\d+/);
        out.push({
            title: title.slice(0, 200),
            url: href,
            repo,
            issue_num: m ? m[2] : null,
            labels: labels.slice(0, 6),
            has_money: !!moneyMatch || labels.some(l => l.toLowerCase().includes('bounty'))
        });
        if (out.length >= 25) break;
    }
    return out;
})()"""

print('\n=== STEP 2: extract bounty issues ===')
issues = run('eval', {'code': EXTRACT}, 15000)
if not ok(issues) or not isinstance(issues, list) or not issues:
    show('issues', issues)
    print('\n--- github layout may have changed; fallback: try simpler selector ---')
    EXTRACT2 = """(() => {
        const links = document.querySelectorAll('a[href*="/issues/"]');
        const out = [];
        const seen = new Set();
        for (const a of links) {
            const href = a.href;
            if (seen.has(href) || !/\\/issues\\/\\d+$/.test(href)) continue;
            seen.add(href);
            const m = href.match(/github\\.com\\/([^\\/]+\\/[^\\/]+)\\/issues\\/(\\d+)/);
            out.push({
                title: (a.innerText || '').trim().split('\\n')[0].slice(0, 200),
                url: href,
                repo: m ? m[1] : '',
                issue_num: m ? m[2] : null
            });
            if (out.length >= 25) break;
        }
        return out;
    })()"""
    issues = run('eval', {'code': EXTRACT2}, 15000)
    if not ok(issues) or not isinstance(issues, list):
        show('fallback', issues)
        raise SystemExit(1)

print(f'  found {len(issues)} issues')
for i, iss in enumerate(issues, 1):
    money = ' [MONEY]' if iss.get('has_money') else ''
    print(f"\n  [{i:>2}] {iss.get('repo')}#{iss.get('issue_num')}{money}")
    print(f"       {iss.get('title')}")
    if iss.get('labels'):
        print(f"       labels: {', '.join(iss['labels'])}")
    print(f"       {iss.get('url')}")

# 3) for the top 5, click in and read the body
print('\n\n=== STEP 3: read first 3 issue bodies ===')
for iss in (issues or [])[:3]:
    print(f"\n--- {iss.get('repo')}#{iss.get('issue_num')}: {iss.get('title')[:80]} ---")
    nav = run('navigate', {'url': iss['url']}, 12000)
    show('navigate', nav, 200)
    time.sleep(3)

    body = run('eval', {
        'code': """(() => {
            const article = document.querySelector('td.comment-body, .comment-body, [data-testid=\"issue-body\"]');
            if (!article) return '(no body)';
            return article.innerText.replace(/\\s+/g, ' ').trim().slice(0, 800);
        })()"""
    }, 8000)
    if ok(body):
        print('  body:')
        for line in (body or '').splitlines():
            print('   ', line[:200])
    else:
        show('body', body)
