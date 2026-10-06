import io
import os
import re
import time
import requests
import telebot
from telebot.types import InputMediaPhoto, InputMediaVideo
from bs4 import BeautifulSoup

TELEGRAM_TOKEN = os.environ['TELEGRAM_TOKEN']
GEMINI_KEY = os.environ['GEMINI_KEY']
GEMINI_MODELS = ['gemini-flash-latest', 'gemini-flash-lite-latest']

CHANNEL_ID = '@barcelonakz1'
SOURCE = 'FCBarcelona_Arabic'
MAX_PER_CHECK = 5
SHOW_SOURCE = False
MAX_VIDEO_MB = 45
SENT_FILE = 'sent_tg.txt'

bot = telebot.TeleBot(TELEGRAM_TOKEN)

PROMPT = """Сен қазақ тіліндегі Барселона жанкүйерлерінің Telegram арнасының редакторысың.
Төмендегі араб тіліндегі жаңалық негізінде қазақ тілінде қысқа, тартымды Telegram посты жаз.

Ережелер:
- Сөзбе-сөз аударма емес, табиғи, жатық қазақша пост жаз.
- Барлық фактіні (аты-жөн, есеп, күн, сан) дәл сақта, өзіңнен ештеңе қоспа.
- Футбол терминдерін қазақ жанкүйері түсінетіндей қолдан.
- Клуб пен ойыншы атауларын дұрыс жаз.
- Бастапқы мәтіндегі эмодзи мен хэштегтерді орынды сақта.
- Арна атауларын, @ атауларын және әшекей сызықтарды қоспа.
- Тек дайын постты жаз, түсініктеме немесе тырнақша қоспа.

Жаңалық:
"""


def load_sent():
    if os.path.exists(SENT_FILE):
        with open(SENT_FILE, encoding='utf-8') as f:
            return set(f.read().split())
    open(SENT_FILE, 'a').close()
    return set()


def mark_sent(sent, post_id):
    sent.add(post_id)
    with open(SENT_FILE, 'a', encoding='utf-8') as f:
        f.write(post_id + '\n')


def fetch_posts():
    try:
        r = requests.get(f'https://t.me/s/{SOURCE}', timeout=20,
                         headers={'User-Agent': 'Mozilla/5.0'})
        r.raise_for_status()
    except Exception as e:
        print(f'Арна беті алынбады: {e}')
        return []
    soup = BeautifulSoup(r.text, 'html.parser')
    posts = []
    for msg in soup.select('div.tgme_widget_message'):
        post_id = msg.get('data-post')
        text_div = msg.select_one('div.tgme_widget_message_text')
        if not post_id or not text_div:
            continue
        for br in text_div.find_all('br'):
            br.replace_with('\n')
        text = text_div.get_text().strip()
        if not text:
            continue
        photos = []
        for a in msg.select('a.tgme_widget_message_photo_wrap'):
            m = re.search(r"url\(['\"]?(.*?)['\"]?\)", a.get('style', ''))
            if m:
                photos.append(m.group(1))
        videos = [v.get('src') for v in msg.select('video') if v.get('src')]
        has_video = bool(msg.select_one(
            '.tgme_widget_message_video_wrap, .tgme_widget_message_roundvideo_wrap'))
        posts.append((post_id, text, photos, videos, has_video and not videos))
    return posts


def strip_footer(text):
    out = []
    for line in text.split('\n'):
        if re.search(r'FCBarcelona_?Arabic', line, re.I):
            continue
        if line.strip() and re.fullmatch(r'[\s━─—\-–_=~•·☆★✦✧*|]+', line):
            continue
        out.append(line)
    return '\n'.join(out).strip()


def has_arabic(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    arabic = sum('\u0600' <= c <= '\u06FF' for c in letters)
    return arabic / len(letters) > 0.3


def ask_gemini(text, model):
    url = ('https://generativelanguage.googleapis.com/v1beta/models/'
           f'{model}:generateContent')
    body = {'contents': [{'parts': [{'text': PROMPT + text}]}]}
    r = requests.post(url, json=body, timeout=60,
                      headers={'x-goog-api-key': GEMINI_KEY})
    r.raise_for_status()
    return r.json()['candidates'][0]['content']['parts'][0]['text'].strip()


def make_post(text):
    text = strip_footer(text)
    if not text:
        return None
    for model in GEMINI_MODELS:
        for wait in (5, 15):
            try:
                result = ask_gemini(text, model)
                if result and not has_arabic(result):
                    return strip_footer(result)
                print('Жауап жарамсыз, қайталаймыз.')
            except Exception as e:
                print(f'Gemini ({model}) қатесі: {str(e)[:70]}')
            time.sleep(wait)
    return None


def download(url, max_mb=None):
    try:
        r = requests.get(url, timeout=60, stream=True,
                         headers={'User-Agent': 'Mozilla/5.0'})
        r.raise_for_status()
        limit = max_mb * 1024 * 1024 if max_mb else None
        size = int(r.headers.get('Content-Length', 0))
        if limit and size > limit:
            print('Файл тым үлкен.')
            return None
        data = b''
        for chunk in r.iter_content(256 * 1024):
            data += chunk
            if limit and len(data) > limit:
                print('Файл тым үлкен.')
                return None
        return data
    except Exception as e:
        print(f'Жүктеу қатесі: {str(e)[:60]}')
        return None


def as_video_file(data):
    f = io.BytesIO(data)
    f.name = 'video.mp4'
    return f


def send_post(text, photo_urls, video_urls):
    media = []
    for u in photo_urls[:10]:
        d = download(u)
        if d:
            media.append(('photo', d))
    for u in video_urls[:5]:
        d = download(u, MAX_VIDEO_MB)
        if d:
            media.append(('video', d))
    media = media[:10]
    fits = len(text) <= 1024

    if not media:
        bot.send_message(CHANNEL_ID, text[:4000])
        return

    if len(media) == 1:
        kind, data = media[0]
        cap = text if fits else None
        if kind == 'photo':
            bot.send_photo(CHANNEL_ID, data, caption=cap)
        else:
            bot.send_video(CHANNEL_ID, as_video_file(data), caption=cap,
                           supports_streaming=True, timeout=180)
    else:
        group = []
        for i, (kind, data) in enumerate(media):
            cap = text if (i == 0 and fits) else None
            if kind == 'photo':
                group.append(InputMediaPhoto(data, caption=cap))
            else:
                group.append(InputMediaVideo(as_video_file(data), caption=cap,
                                             supports_streaming=True))
        bot.send_media_group(CHANNEL_ID, group, timeout=180)

    if not fits:
        bot.send_message(CHANNEL_ID, text[:4000])


def main():
    sent = load_sent()
    posts = fetch_posts()
    print(f'Арнадан {len(posts)} пост табылды.')
    if not posts:
        return

    # Бірінші іске қосу: ескі посттарды жібермей, тек белгілейміз
    if not sent:
        for p in posts:
            mark_sent(sent, p[0])
        print('Ескі посттар белгіленді.')
        return

    new = [p for p in posts if p[0] not in sent]
    if not new:
        print('Жаңа пост жоқ.')
        return

    for post_id, text, photos, videos, missing_video in new[-MAX_PER_CHECK:]:
        post = make_post(text)
        if not post:
            print(f'Пост жасалмады, кейін қайталаймыз: {post_id}')
            continue
        if missing_video:
            post += f'\n\n🎬 Видео: https://t.me/{post_id}'
        elif SHOW_SOURCE:
            post += f'\n\n🔗 https://t.me/{post_id}'
        try:
            mark_sent(sent, post_id)
            send_post(post, photos, videos)
            print(f'Жіберілді: {post_id} (сурет: {len(photos)}, видео: {len(videos)})')
            time.sleep(5)
        except Exception as ex:
            print(f'Жіберу қатесі: {ex}')


main()
