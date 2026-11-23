#!/usr/bin/env python3
"""Publishes a new West Bengal holiday list when the official PDF appears.

The PDF is a scan, so a date is accepted only if OCR reads a day that really
falls on the printed weekday. Any doubt stops the run and publishes nothing.
"""
import calendar, collections, datetime as dt, difflib, glob, json, os, re, subprocess, sys, tempfile, time, urllib.error, urllib.request

MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}
DAYS = list(calendar.day_name)
ANY_DATE = re.compile(r'\b(' + '|'.join(MONTHS) + r')\b\s*[,.]?\s*\(?(' + '|'.join(DAYS) + r')', re.I)
URL = 'https://wb.gov.in/pdf/Holiday-{}.pdf'


def key(name):
    return re.sub(r'[^a-z]', '', name.lower())


def known_names():
    names = {}
    for f in glob.glob('holidays/*.json'):
        d = json.load(open(f, encoding='utf8'))
        for kind in ('public', 'state'):
            for _, bn, en in d.get(kind, []):
                names[key(en)] = (bn, en)
    return names


# Scans read differently at each setting, so every line gets several attempts.
PASSES = [(300, 6), (400, 6), (200, 6), (300, 4), (400, 4), (250, 6), (350, 6)]


def ocr(pdf):
    texts = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (dpi, psm) in enumerate(PASSES):
            prefix = f'{tmp}/p{dpi}'
            if not glob.glob(prefix + '-*.png'):
                subprocess.run(['pdftoppm', '-r', str(dpi), '-gray', '-png', pdf, prefix],
                               check=True, stderr=subprocess.DEVNULL)
            texts.append('\n'.join(
                subprocess.run(['tesseract', page, '-', '--psm', str(psm)], check=True,
                               capture_output=True, text=True).stdout
                for page in sorted(glob.glob(prefix + '-*.png'))))
    return texts


def clean_name(text):
    text = re.sub(r'^[^A-Za-z(]*(\(\s*\*+\s*\)\s*)?', '', text)
    text = re.split(r'\s*[|_—–=~]|\s[:.]+(?:\s|$)', text)[0]
    words = text.split()
    while words and (len(words[-1]) <= 2 and words[-1].lower() not in ('of', 'day') or not re.search('[A-Za-z]', words[-1])):
        words.pop()
    return ' '.join(words)


def read_pass(text, year):
    section, rows = None, []
    for raw in text.splitlines():
        line = raw.strip()
        low = line.lower()
        if 'sectional holidays in' in low: section = None; continue
        if 'public holidays in' in low: section = 'public'; continue
        if 'under the order of state' in low: section = 'state'; continue
        if not section or 'falls' in low or 'banks only' in low or 'notified later' in low: continue
        found = ANY_DATE.search(line)
        if not found: continue
        month, weekday = MONTHS[found[1].title()], found[2].title()
        before = line[:found.start()]
        num = re.search(r'(\d{1,2})[^\dA-Za-z]{0,3}(?:st|nd|rd|th|[a-z]{1,3})?[^\dA-Za-z]{0,3}$', before)
        date = None
        if num:
            try:
                candidate = dt.date(year, month, int(num[1]))
                if DAYS[candidate.weekday()] == weekday: date = candidate
            except ValueError:
                pass
        name = clean_name(before[:num.start()] if num else before)
        rows.append((section, month, weekday, name, date))
    return rows


def similar(a, b, at_least=0.92):
    return difflib.SequenceMatcher(None, key(a), key(b)).ratio() >= at_least


def parse(texts, year):
    names = known_names()
    merged = []  # one entry per holiday line, gathered across OCR passes
    for text in texts:
        for section, month, weekday, name, date in read_pass(text, year):
            if len(key(name)) < 3: continue
            for e in merged:
                if (e['section'], e['month'], e['weekday']) == (section, month, weekday) and similar(e['name'], name):
                    break
            else:
                e = {'section': section, 'month': month, 'weekday': weekday, 'name': name, 'dates': collections.Counter(), 'seen': 0}
                merged.append(e)
            e['seen'] += 1
            if date: e['dates'][date] += 1
    out, problems = {'public': [], 'state': []}, []
    for e in merged:
        if e['seen'] < 2:  # stray OCR noise appears in a single pass only
            if not e['dates']: continue
        label = f"{e['name']} ({e['weekday']}, {calendar.month_name[e['month']]})"
        if not e['dates']:
            # A noisy reading of a line another pass already read cleanly.
            if any(o is not e and o['dates'] and (o['section'], o['month'], o['weekday']) ==
                   (e['section'], e['month'], e['weekday']) and similar(o['name'], e['name'], 0.8)
                   for o in merged):
                continue
            problems.append(f'unreadable date: {label}'); continue
        if len(e['dates']) > 1:
            problems.append(f"conflicting dates: {label}: {sorted(map(str, e['dates']))}"); continue
        date = next(iter(e['dates']))
        match = max(names, key=lambda n: difflib.SequenceMatcher(None, n, key(e['name'])).ratio(), default=None)
        known = match and difflib.SequenceMatcher(None, match, key(e['name'])).ratio() >= 0.8
        bn, en = names[match] if known else (e['name'], e['name'])
        out[e['section']].append([date.isoformat(), bn, en])
    for kind in out:  # the same holiday read two ways in different passes
        unique = []
        for row in sorted(out[kind]):
            if not any(u[0] == row[0] and similar(u[2], row[2], 0.7) for u in unique):
                unique.append(row)
        out[kind] = unique
    if len(out['public']) < 15: problems.append(f"only {len(out['public'])} public holidays")
    if len(out['state']) < 10: problems.append(f"only {len(out['state'])} state holidays")
    if not any(str(year) in t for t in texts): problems.append('year not found in the notification')
    return out, problems


def fetch(year, path):
    req = urllib.request.Request(URL.format(year), headers={'User-Agent': 'Mozilla/5.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 404: return False
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2: raise
            time.sleep(20)
    if not data.startswith(b'%PDF'): return False  # the site's "not found" page
    open(path, 'wb').write(data)
    return True


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--check':  # parse a local PDF, print JSON
        year = int(re.search(r'20\d\d', sys.argv[2])[0])
        out, problems = parse(ocr(sys.argv[2]), year)
        print(json.dumps({'holidays': out, 'problems': problems}, ensure_ascii=False))
        return 0
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    failed = False
    for year in (now.year, now.year + 1):
        target = f'holidays/{year}.json'
        if os.path.exists(target): continue
        with tempfile.TemporaryDirectory() as tmp:
            pdf = f'{tmp}/h.pdf'
            if not fetch(year, pdf):
                print(f'{year}: not published yet'); continue
            out, problems = parse(ocr(pdf), year)
        if problems:
            failed = True
            print(f'{year}: NOT published\n  ' + '\n  '.join(problems)); continue
        data = {'year': year, 'source': f'Government of West Bengal, Finance (Audit) Department holiday notification ({URL.format(year)}), read automatically. Moon-dependent dates may be changed by later order.', **out}
        json.dump(data, open(target, 'w', encoding='utf8'), ensure_ascii=False, indent=1)
        print(f"{year}: published {len(out['public'])} public, {len(out['state'])} state")
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
