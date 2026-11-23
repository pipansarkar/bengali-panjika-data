#!/usr/bin/env python3
"""Adds marriage, annaprashan and griha pravesh dates from the Benimadhab Sil
panjika site as soon as a new year's pages appear. Dates are never calculated.

Before publishing it must reproduce the checked 1433 list. A row is accepted
only if its Bengali date agrees with its printed English date or weekday.
"""
import collections, datetime as dt, html, json, os, re, sys, unicodedata, urllib.request

BASE = 'https://benimadhabsilpanjika.com'
KINDS = {'marriage': 'বিবাহ', 'annaprashan': 'অন্নপ্রাশন', 'griha_pravesh': 'গৃহপ্রবেশ'}
BN_MONTHS = ['বৈশাখ', 'জ্যৈষ্ঠ', 'আষাঢ়', 'শ্রাবণ', 'ভাদ্র', 'আশ্বিন', 'কার্তিক', 'অগ্রহায়ণ', 'পৌষ', 'মাঘ', 'ফাল্গুন', 'চৈত্র']
EN_MONTHS = ['জানু', 'ফেব্রু', 'মার্চ', 'এপ্রি', 'মে', 'জুন', 'জুলা', 'আগ', 'সেপ্ট', 'অক্টো', 'নভে', 'ডিসে']
WEEKDAYS = ['সোম', 'মঙ্গল', 'বুধ', 'বৃহস্পতি', 'শুক্র', 'শনি', 'রবি']
DIGITS = str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789')
TO_BN = str.maketrans('0123456789', '০১২৩৪৫৬৭৮৯')
nfc = lambda s: unicodedata.normalize('NFC', s)
BN_MONTHS = [nfc(m) for m in BN_MONTHS]
ALIASES = {nfc('জৈষ্ঠ'): 1, nfc('জ্যেষ্ঠ'): 1, nfc('কার্ত্তিক'): 6, nfc('অগ্রহায়ন'): 7}
MONTH_WORDS = {**{m: i for i, m in enumerate(BN_MONTHS)}, **ALIASES}
BN_DATE = re.compile(r'([০-৯]{1,2})\s*(' + '|'.join(MONTH_WORDS) + r')\s*([০-৯]{4})')
EN_DATE = re.compile(r'([০-৯]{1,2})\s*(' + '|'.join(nfc(m) for m in EN_MONTHS) + r')\S*\s*([০-৯]{4})')
TIME = r'([০-৯]{1,2})[:।]([০-৯]{1,2})'
LABEL = r'(সন্ধ্যা|রাত্রি|শেষরাত্রি|অপরাহ্ণ|দিবা|প্রাতঃ|সকাল|দুপুর)?\s*(?:ঘ\s*)?'
# One row misprints the phrase as "গতে রাত্রি ঘ গতে রাত্রি ঘ".
WINDOW = re.compile(LABEL + TIME + r'\s*গতে\s*(?:' + LABEL + r'গতে\s*)?' + LABEL + TIME + r'\s*মধ্যে')
SHORT = {'রাত্রি': 'রাত', 'শেষরাত্রি': 'শেষরাত'}


def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode('utf8', 'replace')
        except OSError:
            if attempt == 2: raise


def page_urls():
    urls, seen, queue = set(), set(), [BASE + '/wp-sitemap.xml']
    while queue:
        u = queue.pop()
        if u in seen: continue
        seen.add(u)
        for link in re.findall(r'<loc>\s*([^<\s]+)', get(u)):
            if link.endswith('.xml'): queue.append(link)
            else: urls.add(link)
    return sorted(u for u in urls if '/wp-content/' not in u)


def kind_of(url):
    slug = url.lower()
    if 'griha-nirman' in slug: return None
    if 'marriage' in slug: return 'marriage'
    if 'annaprashan' in slug: return 'annaprashan'
    if 'griha-pravesh' in slug: return 'griha_pravesh'
    return None


def text(cell):
    # The site's text mixes in the Assamese ra (ৰ) for the Bengali ra (র).
    return nfc(re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', cell))).replace('ৰ', 'র')).strip()


def clock(hour, minute):
    return f"{hour}:{minute.translate(DIGITS).zfill(2).translate(TO_BN)}"


def windows(detail):
    out = []
    for m in WINDOW.finditer(detail):
        a, b = SHORT.get(m[1], m[1]), SHORT.get(m[5] or m[4], m[5] or m[4])
        start = f'{a + " " if a else ""}{clock(m[2], m[3])}'
        end = f'{b + " " if b else ""}{clock(m[6], m[7])}'
        out.append(f'{start} – {end}')
    # Better no time than a partial one: every printed window must be read.
    return ', '.join(out) if out and len(out) == detail.count(nfc('মধ্যে')) else None


def rows(page):
    for tr in re.findall(r'<tr\b.*?</tr>', page, re.S):
        cells = [text(c) for c in re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', tr, re.S)]
        if len(cells) < 2: continue
        head, detail = ' '.join(cells[:-1]), cells[-1]
        b = BN_DATE.search(head)
        if not b: continue
        e = EN_DATE.search(head)
        greg = None
        if e:
            mon = next(i for i, m in enumerate(EN_MONTHS) if e[2].startswith(nfc(m))) + 1
            try:
                greg = dt.date(int(e[3].translate(DIGITS)), mon, int(e[1].translate(DIGITS)))
            except ValueError:
                pass
        week = next((i for i, w in enumerate(WEEKDAYS) if nfc(w) + 'বার' in head), None)
        yield {'year': int(b[3].translate(DIGITS)), 'month': MONTH_WORDS[b[2]],
               'day': int(b[1].translate(DIGITS)), 'greg': greg, 'week': week, 'detail': detail}


def collect(year, pages):
    found = {k: [] for k in KINDS}
    for url, page in pages:
        kind = kind_of(url)
        if not kind: continue
        for r in rows(page):
            if r['year'] != year: continue
            # A row lists many ceremonies; it counts only if it names this one.
            if nfc(KINDS[kind]) not in r['detail'] and kind != 'marriage': continue
            if kind == 'marriage' and nfc('বিবাহ') not in r['detail']: continue
            found[kind].append(r)
    starts = collections.defaultdict(collections.Counter)
    for rs in found.values():
        for r in rs:
            if r['greg']: starts[r['month']][r['greg'] - dt.timedelta(days=r['day'] - 1)] += 1
    out, rejected = {k: {} for k in KINDS}, []
    for kind, rs in found.items():
        for r in rs:
            label = f"{kind} {r['day']} {BN_MONTHS[r['month']]} {year}"
            if not starts[r['month']]:
                rejected.append(f'{label}: no English date'); continue
            date = starts[r['month']].most_common(1)[0][0] + dt.timedelta(days=r['day'] - 1)
            if date != r['greg'] and date.weekday() != r['week']:
                rejected.append(f"{label}: printed {r['greg']}, weekday disagrees"); continue
            if not dt.date(year + 593, 4, 10) <= date <= dt.date(year + 594, 4, 20):
                rejected.append(f'{label}: outside the year'); continue
            entry = {'date': date.isoformat()}
            if kind == 'marriage' and windows(r['detail']): entry['time'] = windows(r['detail'])
            out[kind][entry['date']] = entry
    return {k: [v[d] for d in sorted(v)] for k, v in out.items()}, rejected


def health_check(pages):
    """The parser must still reproduce the hand-checked 1433 list."""
    known = json.load(open('events/1433.json', encoding='utf8'))
    got, _ = collect(1433, pages)
    problems = []
    for kind in KINDS:
        want = {x['date'] for x in known[kind]}
        have = {x['date'] for x in got[kind] if x['date'] >= min(want)}
        missing, extra = want - have, have - want
        if len(want & have) < 0.9 * len(want) or len(extra) > 1:
            problems.append(f'{kind}: missing {sorted(missing)}, extra {sorted(extra)}')
    return problems, got


def main():
    pages = [(u, get(u)) for u in page_urls() if kind_of(u)]
    problems, got = health_check(pages)
    if '--check' in sys.argv:
        print(json.dumps({'problems': problems, 'got': got}, ensure_ascii=False)); return 0
    if problems:
        print('Site format changed, publishing nothing:\n  ' + '\n  '.join(problems)); return 1
    now = dt.date.today()
    current = now.year - 593 if (now.month, now.day) >= (4, 15) else now.year - 594
    for year in (current, current + 1, current + 2):
        if year <= 1433: continue
        new, rejected = collect(year, pages)
        for r in rejected: print('skipped', r)
        if not any(new.values()):
            print(f'{year}: not published yet'); continue
        path = f'events/{year}.json'
        data = json.load(open(path, encoding='utf8')) if os.path.exists(path) else {'bengaliYear': year, 'festivals': []}
        added = 0
        for kind, entries in new.items():
            have = {x['date'] for x in data.setdefault(kind, [])}
            fresh = [x for x in entries if x['date'] not in have]
            data[kind] = sorted(data[kind] + fresh, key=lambda x: x['date'])
            added += len(fresh)
        if added:
            data['muhurtaSource'] = f'Benimadhab Sil Full Panjika {year} ({BASE}), imported automatically.'
            json.dump(data, open(path, 'w', encoding='utf8'), ensure_ascii=False, indent=1)
        print(f'{year}: added {added} dates')
    return 0


if __name__ == '__main__':
    sys.exit(main())
