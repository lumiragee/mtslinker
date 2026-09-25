"""
mts_merge: скачивает запись mts link и правильно склеивает её через ffmpeg.
звук = все микрофоны, каждый кусок на своём времени.
картинка = трансляция экрана (потоки screensharing). когда экран не транслируется:
  --mode screen  чёрный кадр (удобно для конспектов и анализа кадров)
  --mode full    включённые вебки, как в плеере (до 4 штук сеткой, выключенные отсеиваются)
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
MAX_CAMS = 4
MIN_SEGMENT = 2.0  # короче этого вебку не показываем, чтобы не мигало


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


def active_parts(path, a, b):
    """куски [a, b] (время внутри файла), где на видео не чёрный кадр"""
    p = subprocess.run([FFMPEG, '-hide_banner', '-nostats', '-ss', f'{a:.3f}', '-t', f'{b - a:.3f}', '-i', path,
                        '-an', '-vf', 'setpts=PTS-STARTPTS,fps=2,scale=160:-2,blackdetect=d=1:pix_th=0.10',
                        '-f', 'null', '-'],
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    blacks = [(float(x), float(y)) for x, y in
              re.findall(r'black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)', p.stderr)]
    parts, t = [], 0.0
    for bs, be in blacks:
        if bs - t >= MIN_SEGMENT:
            parts.append((a + t, a + bs))
        t = max(t, be)
    if (b - a) - t >= MIN_SEGMENT:
        parts.append((a + t, b))
    return parts


def subtract(span, cover):
    """span минус объединение cover -> список промежутков"""
    out, t = [], span[0]
    for x, y in sorted(cover):
        if y <= t or x >= span[1]:
            continue
        if x > t:
            out.append((t, min(x, span[1])))
        t = max(t, y)
    if t < span[1]:
        out.append((t, span[1]))
    return out


def camera_segments(cams, gaps):
    """cams: [(idx, start, dur, path)], gaps: где нет экрана.
    вернёт [(a, b, [(idx, start), ...])] - какие вебки показывать в каждом отрезке"""
    active = {}  # idx -> [(abs_a, abs_b)]
    for idx, start, dur, path in cams:
        for ga, gb in gaps:
            a, b = max(ga, start), min(gb, start + dur)
            if b - a < MIN_SEGMENT:
                continue
            for x, y in active_parts(path, a - start, b - start):
                active.setdefault(idx, []).append((x + start, y + start))
    if not active:
        return []
    starts = {idx: st for idx, st, _, _ in cams}
    total = {idx: sum(y - x for x, y in iv) for idx, iv in active.items()}
    points = sorted({p for iv in active.values() for x, y in iv for p in (x, y)})
    segs = []
    for a, b in zip(points, points[1:]):
        mid = (a + b) / 2
        on = [idx for idx, iv in active.items() if any(x <= mid < y for x, y in iv)]
        on = sorted(on, key=lambda i: -total[i])[:MAX_CAMS]
        if not on:
            continue
        on = sorted(on)
        if segs and segs[-1][1] == a and [i for i, _ in segs[-1][2]] == on:
            segs[-1] = (segs[-1][0], b, segs[-1][2])
        else:
            segs.append((a, b, [(i, starts[i]) for i in on]))
    return [s for s in segs if s[1] - s[0] >= MIN_SEGMENT]


def build_timeline(duration, screens, segs):
    """делит запись на отрезки: (a, b, 'экран'|'вебки'|'чёрный', источник).
    экран важнее вебок; если экранов несколько сразу - берём тот, что включили позже"""
    pts = {0.0, duration}
    for _, st, d in screens:
        pts.update((max(0.0, st), min(duration, st + d)))
    for a, b, _ in segs:
        pts.update((a, b))
    pts = sorted(p for p in pts if 0 <= p <= duration)
    out = []
    for a, b in zip(pts, pts[1:]):
        if b - a < 0.05:
            continue
        mid = (a + b) / 2
        cover = [(st, idx) for idx, st, d in screens if st <= mid < st + d]
        if cover:
            st, idx = max(cover)
            item = ('экран', (idx, st))
        else:
            on = next((c for x, y, c in segs if x <= mid < y), None)
            item = ('вебки', tuple(on)) if on else ('чёрный', None)
        if out and out[-1][2:] == item and abs(out[-1][1] - a) < 1e-6:
            out[-1] = (out[-1][0], b) + item
        else:
            out.append((a, b) + item)
    return out


def build(directory, json_data, output_path, mode='screen'):
    duration = float(json_data.get('duration') or 0)
    if not duration:
        sys.exit('в данных записи нет длительности')

    chunks = []
    for ev in json_data.get('eventLogs', []):
        if isinstance(ev, dict) and isinstance(ev.get('data'), dict) and 'url' in ev['data']:
            stream = ev['data'].get('stream')
            kind = None
            if isinstance(stream, dict):
                kind = 'screen' if 'screensharing' in stream else 'camera' if 'conference' in stream else None
            chunks.append((float(ev.get('relativeTime') or 0), ev['data']['url'], kind))
    marked = any(k is not None for _, _, k in chunks)
    if not chunks:
        sys.exit('в записи не нашлось ни одного видео/аудио куска')

    print(f'кусков: {len(chunks)}, длительность записи: {duration / 60:.0f} мин')
    screens, audios, files, cams = [], [], [], []
    has_video = set()
    for i, (start, url, kind) in enumerate(chunks, 1):
        print(f'[{i}/{len(chunks)}] ', end='')
        try:
            path = download(url, directory)
        except Exception as e:
            print(f'  не скачался, пропускаю: {e}')
            continue
        info = probe(path)
        if info is None:
            continue
        width, has_audio, dur = info
        idx = len(files)
        files.append(path)
        if width:
            has_video.add(idx)
        is_screen = (kind == 'screen') if marked else (width >= FALLBACK_MIN_WIDTH)
        if is_screen and width:
            screens.append((idx, start, dur))
        elif width:
            cams.append((idx, start, dur, path))
        if has_audio:
            audios.append((idx, start))

    print(f'трансляций экрана: {len(screens)}, звуковых дорожек: {len(audios)}')
    if not audios:
        sys.exit('звука не нашлось')

    segs = []
    if mode == 'full' and cams:
        gaps = subtract((0.0, duration), [(st, st + d) for _, st, d in screens])
        print('ищу включённые вебки там, где нет экрана...')
        segs = camera_segments(cams, gaps)
        shown = sum(b - a for a, b, _ in segs)
        print(f'вебки покажу в {len(segs)} местах, всего {shown / 60:.1f} мин')

    timeline = build_timeline(duration, screens, segs)
    print('картинка: ' + ', '.join(f'{k} {sum(b - a for a, b, kk, _ in timeline if kk == k) / 60:.0f} мин'
                                   for k in ('экран', 'вебки', 'чёрный')))

    # звук: каждый файл целиком со своей задержкой
    args = [FFMPEG, '-hide_banner', '-loglevel', 'error', '-stats', '-y']
    fl = []
    alabels = []
    for n, (idx, start) in enumerate(audios):
        args += ['-i', files[idx]]
        trim = f'atrim=start={-start},asetpts=PTS-STARTPTS,' if start < 0 else ''
        ms = int(max(start, 0) * 1000)
        fl.append(f'[{n}:a]{trim}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
                  f'adelay={ms}|{ms}[a{n}]')
        alabels.append(f'[a{n}]')
    n_in = len(audios)

    # картинка: отрезки по очереди, каждый со своего места в файле (ничего не копится в памяти)
    def seg_input(path, offset, length):
        nonlocal n_in
        # размер кадра может меняться посреди записи - не пересобираем фильтры, а приводим к одному размеру
        args.extend(['-reinit_filter', '0', '-ss', f'{max(offset, 0):.3f}', '-t', f'{length + 1:.3f}', '-i', path])
        n_in += 1
        return n_in - 1

    def fit(label_in, w, h, length, label_out):
        fl.append(f'{label_in}setpts=PTS-STARTPTS,fps={FPS},scale={w}:{h}:force_original_aspect_ratio=decrease,'
                  f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,'
                  f'tpad=stop=-1:stop_mode=add:color=black,trim=duration={length:.3f},setpts=PTS-STARTPTS{label_out}')

    vlabels = []
    tw, th = W // 2, H // 2
    for k, (a, b, kind, src) in enumerate(timeline):
        length = b - a
        if kind == 'чёрный':
            fl.append(f'color=c=black:s={W}x{H}:r={FPS}:d={length:.3f},setsar=1,format=yuv420p[v{k}]')
        elif kind == 'экран':
            idx, start = src
            i = seg_input(files[idx], a - start, length)
            fit(f'[{i}:v]', W, H, length, f'[v{k}]')
        else:
            if len(src) == 1:
                idx, start = src[0]
                i = seg_input(files[idx], a - start, length)
                fit(f'[{i}:v]', W, H, length, f'[v{k}]')
            else:
                tiles = []
                for j, (idx, start) in enumerate(src):
                    i = seg_input(files[idx], a - start, length)
                    fit(f'[{i}:v]', tw, th, length, f'[t{k}_{j}]')
                    tiles.append(f'[t{k}_{j}]')
                if len(src) == 2:
                    fl.append(f'{"".join(tiles)}hstack=inputs=2,pad={W}:{H}:0:{th // 2}[v{k}]')
                else:
                    layout = '|'.join(['0_0', f'{tw}_0', f'0_{th}', f'{tw}_{th}'][:len(src)])
                    fl.append(f'{"".join(tiles)}xstack=inputs={len(src)}:layout={layout}:fill=black[v{k}]')
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
    ap.add_argument('--mode', choices=['screen', 'full'], default='screen',
                    help='screen: без экрана чёрный кадр; full: без экрана включённые вебки')
    a = ap.parse_args()
    ev, rec = parse_url(a.url)
    data = fetch_json(ev, rec, a.session_id)
    name = re.sub(r'[\s/\\:*?"<>|]+', '_', data.get('name') or f'record_{ev}').strip('_.')
    directory = os.path.abspath(name)
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, 'record.json'), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    build(directory, data, os.path.abspath(name + '.mp4'), a.mode)


if __name__ == '__main__':
    main()
