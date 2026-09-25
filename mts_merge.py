"""
mts_merge: скачивает запись mts link и правильно склеивает её через ffmpeg.
звук = все микрофоны, каждый кусок на своём времени.
картинка = трансляция экрана (потоки screensharing), когда экрана нет - чёрный кадр.
куски уже скачанные в папку записи повторно не качаются.
"""
import argparse
import json
import os
import re
import subprocess
import sys

import httpx
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36'
W, H, FPS = 1920, 1080, 5
FALLBACK_MIN_WIDTH = 1000  # если в данных нет пометки screensharing


def parse_url(url):
    m = re.match(r'^https://my\.mts-link\.ru/(?:[^/]+/)?\d+/\d+/record-new/(\d+)(?:/record-file/(\d+))?', url)
    if not m:
        sys.exit('ссылка не похожа на запись mts link')
    return m.group(1), m.group(2)


def fetch_json(event_session, record_id, session_id):
    if record_id:
        api = f'https://my.mts-link.ru/api/event-sessions/{event_session}/record-files/{record_id}/flow?withoutCuts=false'
    else:
        api = f'https://my.mts-link.ru/api/eventsessions/{event_session}/record?withoutCuts=false'
    cookies = {'sessionId': session_id} if session_id else {}
    r = httpx.get(api, headers={'User-Agent': UA}, cookies=cookies, timeout=60)
    if r.status_code in (401, 403):
        sys.exit('доступ запрещён: запись закрытая или sessionId протух. удали session.txt и введи новый')
    r.raise_for_status()
    return r.json()


def download(url, directory):
    path = os.path.join(directory, os.path.basename(url.split('?')[0]))
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    tmp = path + '.part'
    with httpx.stream('GET', url, headers={'User-Agent': UA}, timeout=None, follow_redirects=True) as r:
        r.raise_for_status()
        total = int(r.headers.get('content-length', 0))
        done = 0
        with open(tmp, 'wb') as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f'\r  {os.path.basename(path)[:16]}  {done * 100 // total:3d}%', end='', flush=True)
    print()
    os.replace(tmp, path)
    return path


def probe(path):
    """вернёт (ширина видео или 0, есть ли звук, длительность); None если файл битый"""
    p = subprocess.run([FFMPEG, '-hide_banner', '-i', path], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    err = p.stderr
    if 'Stream #' not in err:
        return None
    width = 0
    for m in re.finditer(r'Stream #\d+:\d+.*?: Video: .*?(\d{2,5})x(\d{2,5})', err):
        width = max(width, int(m.group(1)))
    has_audio = bool(re.search(r'Stream #\d+:\d+.*?: Audio:', err))
    m = re.search(r'Duration: (\d+):(\d+):([\d.]+)', err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    return width, has_audio, dur


class Piece:
    """кусок файла, стоящий на своём месте в записи"""
    def __init__(self, pid, fidx, path, start, offset, length, kind, width, has_audio):
        self.pid, self.fidx, self.path = pid, fidx, path
        self.start, self.offset, self.length = start, offset, length
        self.kind, self.width, self.has_audio = kind, width, has_audio

    @property
    def end(self):
        return self.start + self.length


def stream_kind(stream):
    if isinstance(stream, dict):
        if 'screensharing' in stream:
            return 'screen'
        if 'conference' in stream:
            return 'camera'
    return None


def collect_items(json_data):
    """все упоминания файлов в записи: (время в записи, позиция в файле, url, тип).
    потоки, начатые до начала записи или до конца вырезанного куска, лежат не в mediasession.add,
    а в снимке состояния (snapshot) с upTime - сколько секунд поток уже шёл к этому моменту"""
    items, cuts = {}, []
    for ev in json_data.get('eventLogs', []):
        if not isinstance(ev, dict):
            continue
        rel = float(ev.get('relativeTime') or 0)
        if ev.get('module') == 'cut.end':
            cuts.append(rel)
        data = ev.get('data')
        if isinstance(data, dict) and data.get('url'):
            items.setdefault((data['url'], round(rel, 3)), (rel, 0.0, data['url'], stream_kind(data.get('stream'))))
        snap = ev.get('snapshot')
        if isinstance(snap, dict) and isinstance(snap.get('data'), dict):
            for m in snap['data'].get('mediasession') or []:
                if isinstance(m, dict) and m.get('url'):
                    items[(m['url'], round(rel, 3))] = (rel, float(m.get('upTime') or 0), m['url'],
                                                         stream_kind(m.get('stream')))
    return sorted(items.values()), sorted(cuts)


def make_pieces(items, cuts, duration, info):
    """info: url -> (fidx, path, width, has_audio, dur). кусок длится до конца файла,
    до следующего появления того же файла или до следующего вырезанного места - что раньше"""
    by_url = {}
    for rel, off, url, kind in items:
        if url in info:
            by_url.setdefault(url, []).append((rel, off, kind))
    pieces = []
    for url, lst in by_url.items():
        fidx, path, width, has_audio, fdur = info[url]
        lst.sort()
        for i, (rel, off, kind) in enumerate(lst):
            end = min(duration, rel + max(fdur - off, 0))
            if i + 1 < len(lst):
                end = min(end, lst[i + 1][0])
            nxt = [c for c in cuts if c > rel + 1e-3]
            if nxt:
                end = min(end, nxt[0])
            if end - rel > 0.05:
                pieces.append(Piece(len(pieces), fidx, path, rel, off, end - rel, kind, width, has_audio))
    return pieces


def build_timeline(duration, screens):
    """делит запись на отрезки: (a, b, 'экран'|'чёрный', кусок).
    если экранов несколько сразу - берём тот, что включили позже"""
    pts = {0.0, duration}
    for sc in screens:
        pts.update((max(0.0, sc.start), min(duration, sc.end)))
    pts = sorted(p for p in pts if 0 <= p <= duration)
    out = []
    for a, b in zip(pts, pts[1:]):
        if b - a < 0.05:
            continue
        mid = (a + b) / 2
        cover = [sc for sc in screens if sc.start <= mid < sc.end]
        item = ('экран', max(cover, key=lambda sc: sc.start)) if cover else ('чёрный', None)
        if out and out[-1][2:] == item and abs(out[-1][1] - a) < 1e-6:
            out[-1] = (out[-1][0], b) + item
        else:
            out.append((a, b) + item)
    return out


def build(directory, json_data, output_path):
    duration = float(json_data.get('duration') or 0)
    if not duration:
        sys.exit('в данных записи нет длительности')

    items, cuts = collect_items(json_data)
    urls = list(dict.fromkeys(url for _, _, url, _ in items))
    if not urls:
        sys.exit('в записи не нашлось ни одного видео/аудио куска')
    kinds = {url: kind for _, _, url, kind in items if kind}
    marked = bool(kinds)

    print(f'файлов: {len(urls)}, длительность записи: {duration / 60:.0f} мин')
    info = {}
    for i, url in enumerate(urls, 1):
        print(f'[{i}/{len(urls)}] ', end='')
        try:
            path = download(url, directory)
        except Exception as e:
            print(f'  не скачался, пропускаю: {e}')
            continue
        pr = probe(path)
        if pr is None:
            continue
        width, has_audio, fdur = pr
        info[url] = (len(info), path, width, has_audio, fdur)

    pieces = make_pieces(items, cuts, duration, info)
    screens, audios = [], []
    for pc in pieces:
        is_screen = (pc.kind == 'screen') if marked else (pc.width >= FALLBACK_MIN_WIDTH)
        if pc.width and is_screen:
            screens.append(pc)
        if pc.has_audio:
            audios.append(pc)

    print(f'\nтрансляций экрана: {len(screens)}, звуковых дорожек: {len(audios)}')
    if not audios:
        sys.exit('звука не нашлось')

    timeline = build_timeline(duration, screens)
    print('картинка: ' + ', '.join(f'{k} {sum(b - a for a, b, kk, _ in timeline if kk == k) / 60:.0f} мин'
                                   for k in ('экран', 'чёрный')))

    args = [FFMPEG, '-hide_banner', '-loglevel', 'error', '-stats', '-y']
    fl = []
    n_in = 0

    def add_input(path, offset, length, video=False):
        # каждый кусок читается со своего места в файле - ничего не копится в памяти.
        # у видео размер кадра может меняться посреди записи - не пересобираем фильтры
        nonlocal n_in
        opts = ['-reinit_filter', '0'] if video else []
        if offset > 0.001:
            opts += ['-ss', f'{offset:.3f}']
        args.extend(opts + ['-t', f'{length + (1 if video else 0):.3f}', '-i', path])
        n_in += 1
        return n_in - 1

    # звук: каждый кусок со своей задержкой
    alabels = []
    for n, pc in enumerate(audios):
        i = add_input(pc.path, pc.offset, pc.length)
        ms = int(pc.start * 1000)
        fl.append(f'[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
                  f'adelay={ms}|{ms}[a{n}]')
        alabels.append(f'[a{n}]')

    def fit(label_in, w, h, length, label_out):
        fl.append(f'{label_in}setpts=PTS-STARTPTS,fps={FPS},scale={w}:{h}:force_original_aspect_ratio=decrease,'
                  f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,'
                  f'tpad=stop=-1:stop_mode=add:color=black,trim=duration={length:.3f},setpts=PTS-STARTPTS{label_out}')

    # картинка: отрезки по очереди
    vlabels = []
    for k, (a, b, kind, pc) in enumerate(timeline):
        length = b - a
        if kind == 'чёрный':
            fl.append(f'color=c=black:s={W}x{H}:r={FPS}:d={length:.3f},setsar=1,format=yuv420p[v{k}]')
        else:
            i = add_input(pc.path, pc.offset + a - pc.start, length, video=True)
            fit(f'[{i}:v]', W, H, length, f'[v{k}]')
        vlabels.append(f'[v{k}]')
    if len(vlabels) == 1:
        fl.append(f'{vlabels[0]}fps={FPS}[vout]')
    else:
        fl.append(f'{"".join(vlabels)}concat=n={len(vlabels)}:v=1:a=0,fps={FPS}[vout]')

    if len(alabels) == 1:
        fl.append(f'{alabels[0]}apad,atrim=0:{duration}[aout]')
    else:
        fl.append(f'{"".join(alabels)}amix=inputs={len(alabels)}:normalize=0:dropout_transition=0,'
                  f'apad,atrim=0:{duration}[aout]')

    script = os.path.join(directory, 'filter.txt')
    with open(script, 'w', encoding='utf-8') as f:
        f.write(';\n'.join(fl))

    args += ['-filter_complex_script', script, '-map', '[vout]', '-map', '[aout]', '-r', str(FPS),
             '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '26', '-tune', 'stillimage',
             '-c:a', 'aac', '-b:a', '128k', '-t', str(duration), '-movflags', '+faststart', output_path]
    print('склеиваю, это несколько минут...')
    res = subprocess.run(args)
    if res.returncode != 0:
        sys.exit('ffmpeg упал, скинь скрин окна')
    print(f'\nготово: {output_path}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('url')
    ap.add_argument('--session-id')
    ap.add_argument('--mode', help=argparse.SUPPRESS)  # из старых версий батника, больше не используется
    a = ap.parse_args()
    ev, rec = parse_url(a.url)
    data = fetch_json(ev, rec, a.session_id)
    name = re.sub(r'[\s/\\:*?"<>|]+', '_', data.get('name') or f'record_{ev}').strip('_.')
    directory = os.path.abspath(name)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, 'record.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    build(directory, data, os.path.abspath(name + '.mp4'))


if __name__ == '__main__':
    main()
