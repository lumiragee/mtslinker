# mtslinker (форк с рабочей склейкой)

Форк [motattack/mtslinker](https://github.com/motattack/mtslinker): качает записи вебинаров с MTS Link и собирает их в одно нормальное видео.

## чем отличается от оригинала

MTS Link хранит запись кусками: отдельно звук лектора, отдельно трансляция экрана, отдельно микрофоны участников, и многие куски идут одновременно. Оригинальная склейка ставит их друг за другом, поэтому из полуторачасовой лекции выходит видео на 4+ часа, а экрана там может не быть вообще.

В этом форке склейка `mts_merge.py` делает по-другому:
- берёт трансляцию экрана как картинку;
- смешивает звук всех микрофонов;
- ставит каждый кусок на своё время;
- собирает всё через ffmpeg, лекция на полтора часа занимает несколько минут.

Кроме этого, есть батник для винды: запускаешь двойным кликом и вставляешь ссылку.

## быстрый старт (windows)

1. Нужен Python 3.9+. Если его нет, поставь с [python.org](https://www.python.org/downloads/) и **отметь галочку «Add python.exe to PATH»**. Conda тоже подойдёт, батник её найдёт.
2. Скачай репо: зелёная кнопка **Code → Download ZIP**. Распакуй куда удобно.
3. Запусти `mtslinker.bat`. При первом запуске он сам поставит зависимости (`httpx`, `imageio-ffmpeg`), ffmpeg скачается вместе с ними.
4. Вставь ссылку на запись и нажми enter.

Готовое видео появится в `Видео\mtslinker\`. Рядом останется папка с исходными кусками, её можно удалить.

По желанию можно запустить `make_shortcut.bat`. Он добавит mtslinker в меню пуск, и его можно будет закрепить на начальном экране.

## какую ссылку вставлять

Скопируй из адресной строки, пока открыта запись. Подойдёт любой из двух видов:
```
https://my.mts-link.ru/12345678/987654321/record-new/123456789/record-file/1234567890
https://my.mts-link.ru/12345678/987654321/record-new/123456789
```
Хвост после `?` батник обрежет сам.

## закрытые записи (sessionId)

Если запись доступна только после входа, батник попросит `sessionId`:
1. Зайди на my.mts-link.ru под своим аккаунтом.
2. Нажми F12, открой **Application → Cookies → https://my.mts-link.ru**.
3. Скопируй значение `sessionId` и вставь в батник.

Батник запомнит его в `session.txt` и в следующий раз подставит сам. Если начнёт ругаться на доступ, значит id протух: удали `session.txt`, и батник спросит новый.

**Не делись `session.txt` и значением sessionId ни с кем:** это доступ к твоему аккаунту.

## без батника (любая ОС)

```bash
pip install httpx imageio-ffmpeg
python mts_merge.py "ССЫЛКА" --session-id ТВОЙ_ID
```

---

<details>
<summary>оригинальный README</summary>

# mtslinker
`mtslinker` - это инструмент для загрузки и обработки записей вебинаров, предоставляемых сервисом MTS Link. Он автоматически загружает видео и аудиофайлы вебинаров, синхронизирует их и создает единый видеоролик.

## Установка
### poetry:
```bash
poetry add git+https://github.com/motattack/mtslinker.git
```

### pip:
```bash
pip install git+https://github.com/motattack/mtslinker.git
```

## Использование
Для использования `mtslinker`, просто вызовите в терминале mtslinker с URL записи вебинара в качестве аргумента.

Вот несколько примеров использования:
### Пример 1. Загрузка обычной записи:
```bash
mtslinker https://my.mts-link.ru/12345678/987654321/record-new/123456789/record-file/1234567890
```

### Пример 2. Загрузка быстрой встречи:
```bash
mtslinker https://my.mts-link.ru/12345678/987654321/record-new/123456789
```

### Пример 3. Загрузка приватной записи:
```bash
mtslinker https://my.mts-link.ru/12345678/987654321/record-new/123456789/record-file/1234567890 --session-id a1b2c3d4
```

> **Примечание**: Узнать свой `sessionId` можно в кукисах сайта (нужно быть авторизованным). [Пример](https://raw.githubusercontent.com/motattack/mtslinker/refs/heads/master/get_sessionId.mp4) как это можно сделать.

## Использование в проекте

Если вы хотите интегрировать `mtslinker` в свой проект, вы можете использовать функцию `fetch_webinar_data` для загрузки данных вебинара.

Например, для ссылки: https://my.mts-link.ru/12345678/987654321/record-new/123456789/record-file/1234567890, формат будет следующим:
```
https://my.mts-link.ru/{organization_id}/{room_id}/record-new/{event_sessions}/record-file/{record_id}
```

```python
from mtslinker.webinar import fetch_webinar_data

fetch_webinar_data(
  event_sessions='123456789',
  record_id='1234567890',   # Нужен для обычной встречи, не нужен для быстрой.
  session_id='a1b2c3d4'    # Optional
)
```

## Docker
Для запуска через Docker:

1. Соберите образ:
   ```bash
   docker-compose build
   ```
2. Запустите контейнер:
   ```bash
   docker-compose run --rm mtslinker [URL] [--session-id SESSION_ID]
   ```
#### Пример с аргументами   
   ```bash
   docker-compose run --rm mtslinker https://my.mts-link.ru/12345678/987654321/record-new/123456789/record-file/1234567890 --session-id a1b2c3d4
   ```
</details>
