"""
mts_merge: скачивает запись mts link и правильно склеивает её через ffmpeg.
картинка = трансляция экрана (потоки от 640px), звук = все микрофоны, каждый кусок на своём времени.
куски уже скачанные в папку записи повторно не качаются.
"""
import argparse
import os
import re
import subprocess
import sys

import httpx
import imageio_ffmpeg

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36'
W, H, FPS = 1920, 1080, 5
SCREEN_MIN_WIDTH = 640


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
    """вернёт (ширина видео или 0, есть ли звук); None если файл битый"""
    p = subprocess.run([FFMPEG, '-hide_banner', '-i', path], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    err = p.stderr
    if 'Stream #' not in err:
        return None
    width = 0
    for m in re.finditer(r'Stream #\d+:\d+.*?: Video: .*?(\d{2,5})x(\d{2,5})', err):
        width = max(width, int(m.group(1)))
    has_audio = bool(re.search(r'Stream #\d+:\d+.*?: Audio:', err))
    return width, has_audio


def build(directory, json_data, output_path):
    duration = float(json_data.get('duration') or 0)
    if not duration:
        sys.exit('в данных записи нет длительности')

    chunks = []
    for ev in json_data.get('eventLogs', []):
        if isinstance(ev, dict) and isinstance(ev.get('data'), dict) and 'url' in ev['data']:
            chunks.append((float(ev.get('relativeTime') or 0), ev['data']['url']))
    if not chunks:
        sys.exit('в записи не нашлось ни одного видео/аудио куска')

    print(f'кусков: {len(chunks)}, длительность записи: {duration / 60:.0f} мин')
    screens, audios, files = [], [], []
    for i, (start, url) in enumerate(chunks, 1):
        print(f'[{i}/{len(chunks)}] ', end='')
        try:
            path = download(url, directory)
        except Exception as e:
            print(f'  не скачался, пропускаю: {e}')
            continue
        info = probe(path)
        if info is None:
            continue
        width, has_audio = info
        idx = len(files)
        files.append(path)
        if width >= SCREEN_MIN_WIDTH:
            screens.append((idx, start))
        if has_audio:
            audios.append((idx, start))

    print(f'трансляций экрана: {len(screens)}, звуковых дорожек: {len(audios)}')
    if not audios:
        sys.exit('звука не нашлось')

    args = [FFMPEG, '-hide_banner', '-loglevel', 'error', '-stats', '-y']
    for f in files:
        args += ['-i', f]
    base = len(files)
    args += ['-f', 'lavfi', '-i', f'color=c=black:s={W}x{H}:r={FPS}:d={duration}']

    fl = []
    cur = f'[{base}:v]'
    for n, (idx, start) in enumerate(sorted(screens, key=lambda s: s[1])):
        trim = f'trim=start={-start},' if start < 0 else ''
        off = max(start, 0)
        fl.append(f'[{idx}:v]{trim}setpts=PTS-STARTPTS,fps={FPS},'
                  f'scale={W}:{H}:force_original_aspect_ratio=decrease,'
                  f'pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,setpts=PTS+{off}/TB[s{n}]')
        fl.append(f'{cur}[s{n}]overlay=eof_action=pass[b{n}]')
        cur = f'[b{n}]'
    fl.append(f'{cur}format=yuv420p[vout]')

    alabels = []
    for n, (idx, start) in enumerate(audios):
        trim = f'atrim=start={-start},asetpts=PTS-STARTPTS,' if start < 0 else ''
        ms = int(max(start, 0) * 1000)
        fl.append(f'[{idx}:a]{trim}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,'
                  f'adelay={ms}|{ms}[a{n}]')
        alabels.append(f'[a{n}]')
    if len(alabels) == 1:
        fl.append(f'{alabels[0]}apad,atrim=0:{duration}[aout]')
    else:
        fl.append(f'{"".join(alabels)}amix=inputs={len(alabels)}:normalize=0:dropout_transition=0,'
                  f'apad,atrim=0:{duration}[aout]')

    script = os.path.join(directory, 'filter.txt')
    with open(script, 'w', encoding='utf-8') as f:
        f.write(';\n'.join(fl))

    args += ['-filter_complex_script', script, '-map', '[vout]', '-map', '[aout]',
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
    a = ap.parse_args()
    ev, rec = parse_url(a.url)
    data = fetch_json(ev, rec, a.session_id)
    name = re.sub(r'[\s/\\:*?"<>|]+', '_', data.get('name') or f'record_{ev}').strip('_.')
    directory = os.path.abspath(name)
    os.makedirs(directory, exist_ok=True)
    build(directory, data, os.path.abspath(name + '.mp4'))


if __name__ == '__main__':
    main()
