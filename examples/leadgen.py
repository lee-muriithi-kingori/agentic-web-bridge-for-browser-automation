"""Fix the contact-extraction bug, re-run for the same 5 results."""
import json
import time
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


URLS = [
    ('World Relief Kenya', 'https://www.facebook.com/jobvacancykenya/posts/-were-hiring-web-developerworld-relief-kenya-is-seeking-a-talented-and-creative-/985839527526715/'),
    ('Full-Stack Web Dev', 'https://www.facebook.com/groups/icthubkenya/posts/4323541424455844/'),
    ('EOI FIDA Kenya', 'https://www.facebook.com/OfficialFidaKenya/posts/opportunitywe-are-looking-for-a-web-developer-eoi-document-link/1377881551044200/'),
    ('IPF Kenya Software Dev', 'https://www.facebook.com/IPFKENYA/posts/-opportunity-for-a-software-developer-we-are-seeking-an-individual-or-firm-with-/1684043619931828/'),
    ('KTDA Web Dev', 'https://www.facebook.com/100080376005003/posts/opportunity-alert-%EF%B8%8F-web-developer-1-position-network-administrator-1-position-at/1042790141743510/'),
]

# extract script — fix: explicit parens around match, regex built with new RegExp
EXTRACT_JS = """(() => {
    const text = document.body ? document.body.innerText : '';
    const out = { title: document.title, phones: [], emails: [], whatsapp: [], preview: '' };
    // phones
    const phoneRe = /(\\\\+254|0)\\\\d{8,10}/g;
    let m; while ((m = phoneRe.exec(text)) !== null) out.phones.push(m[0]);
    out.phones = [...new Set(out.phones)].slice(0, 5);
    // emails
    const emailRe = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\\\.[A-Za-z]{2,}/g;
    while ((m = emailRe.exec(text)) !== null) out.emails.push(m[0]);
    out.emails = [...new Set(out.emails)].slice(0, 5);
    // whatsapp mentions
    const waRe = /(whats?app|wa)[^A-Za-z0-9]{0,3}(\\\\+?\\\\d[\\\\d\\\\s\\\\-]{6,15})/gi;
    while ((m = waRe.exec(text)) !== null) out.whatsapp.push(m[2].trim());
    out.whatsapp = [...new Set(out.whatsapp)].slice(0, 5);
    out.preview = text.replace(/\\\\s+/g, ' ').trim().slice(0, 400);
    return out;
})()"""

for label, url in URLS:
    print(f'\n=== {label} ===')
    print('  url:', url)
    nav = run('navigate', {'url': url}, 12000)
    show('navigate', nav, 200)
    time.sleep(4)  # let the FB page render

    info = run('eval', {'code': EXTRACT_JS}, 10000)
    if ok(info) and isinstance(info, dict):
        print('  page title:    ', info.get('title'))
        if info.get('phones'):   print('  phones:        ', info['phones'])
        if info.get('emails'):   print('  emails:        ', info['emails'])
        if info.get('whatsapp'): print('  whatsapp nums: ', info['whatsapp'])
        prev = (info.get('preview') or '').strip()
        if prev:
            print('  preview:', prev[:200])
    else:
        show('info', info)
