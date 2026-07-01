import asyncio
import contextlib
import io
import json
import os
import re
import aiohttp
from PIL import Image
import numpy as np
import gradio as gr
from modules import scripts, shared, script_callbacks
from scripts.Gel import Gelbooru, GelbooruException, GelbooruNotFoundException

SEEN_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "seen_posts.json")

def load_seen_ids():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set(data)
        except Exception:
            pass
    return set()

def save_seen_ids(ids):
    try:
        with open(SEEN_FILE, 'w') as f:
            json.dump(list(ids), f)
    except Exception as e:
        print(f"Ошибка сохранения просмотренных постов: {e}")

async def fetch_image(session, url, timeout=60):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
        'Referer': 'https://gelbooru.com/'
    }
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status == 200:
                data = await resp.read()
                img = Image.open(io.BytesIO(data))
                if img.mode == 'RGBA':
                    img = img.convert('RGB')
                return np.array(img)
            else:
                print(f"Не удалось загрузить изображение: {resp.status}")
                return None
    except asyncio.TimeoutError:
        print(f"Таймаут загрузки изображения: {url}")
        return None
    except Exception as e:
        print(f"Ошибка при загрузке изображения: {e}")
        return None

def filter_tags(tags, ignore_list):
    if not ignore_list:
        return tags
    ignore_normalized = [tag.strip().replace(' ', '_') for tag in ignore_list if tag.strip()]
    return [tag for tag in tags if tag not in ignore_normalized]

async def get_random_tags(include, exclude):
    include = include.replace(" ", "")
    exclude = exclude.replace(" ", "")
    api_key = getattr(shared.opts, "gpr_api_key", None)
    user_id = getattr(shared.opts, "gpr_user_id", None)
    ignore_tags_raw = getattr(shared.opts, "gpr_ignore_tags", "")
    ignore_list = [t.strip() for t in ignore_tags_raw.split(',')] if ignore_tags_raw else []

    if not api_key or not user_id:
        return "Необходимо войти в аккаунт Gelbooru (укажите API ключ и ID пользователя в настройках)", None, "Необходима авторизация"

    if include == "":
        include = None
    else:
        include = include.split(',')

    if exclude == "":
        exclude = None
    else:
        exclude = exclude.split(',')

    seen_ids = load_seen_ids()
    max_attempts = 20
    attempt = 0
    gel_post = None

    while attempt < max_attempts:
        try:
            # Передаём таймаут 30 секунд (можно увеличить при необходимости)
            gel_post = await Gelbooru(api_key=api_key, user_id=user_id, timeout=60).random_post(tags=include, exclude_tags=exclude)
            if gel_post is None:
                return "Не удалось найти пост с указанными тегами", None, "Пост не найден"
            if gel_post.id not in seen_ids:
                break
        except asyncio.TimeoutError:
            return "Превышен таймаут соединения с Gelbooru. Попробуйте позже или проверьте интернет.", None, "Таймаут"
        except GelbooruException as e:
            if "525" in str(e):
                return "Сайт Gelbooru временно недоступен (ошибка 525). Попробуйте позже.", None, "Ошибка соединения"
            else:
                return f"Ошибка при обращении к Gelbooru: {e}", None, "Ошибка"
        except Exception as e:
            return f"Непредвиденная ошибка: {e}", None, "Ошибка"
        attempt += 1

    if gel_post is None:
        return "Не удалось найти пост с указанными тегами", None, "Пост не найден"

    if attempt == max_attempts and gel_post.id in seen_ids:
        return "Все доступные посты с такими тегами уже были показаны. Попробуйте другие теги или очистите список просмотренных.", None, "Нет новых постов"

    seen_ids.add(gel_post.id)
    save_seen_ids(seen_ids)

    raw_tags = gel_post.get_tags()
    filtered_raw = filter_tags(raw_tags, ignore_list)
    final_tags = []
    for tag in filtered_raw:
        if getattr(shared.opts, "gpr_replaceUnderscores", True):
            exclusion_list = getattr(shared.opts, "gpr_undersocreReplacementExclusionList", "").split(',')
            if tag not in exclusion_list:
                tag = tag.replace("_", " ")
        final_tags.append(tag)

    preview = None
    async with aiohttp.ClientSession() as session:
        preview = await fetch_image(session, gel_post.file_url, timeout=60)

    return ', '.join(final_tags), preview, str(gel_post)

async def get_post_by_url(post_input):
    api_key = getattr(shared.opts, "gpr_api_key", None)
    user_id = getattr(shared.opts, "gpr_user_id", None)
    ignore_tags_raw = getattr(shared.opts, "gpr_ignore_tags", "")
    ignore_list = [t.strip() for t in ignore_tags_raw.split(',')] if ignore_tags_raw else []

    if not api_key or not user_id:
        return "Необходимо войти в аккаунт Gelbooru (укажите API ключ и ID пользователя в настройках)", None, None, "Необходима авторизация"

    post_id = None
    match = re.search(r'id=(\d+)', post_input)
    if match:
        post_id = int(match.group(1))
    else:
        if post_input.isdigit():
            post_id = int(post_input)

    if post_id is None:
        return "Не удалось распознать ID поста. Укажите ссылку вида https://gelbooru.com/index.php?page=post&s=view&id=123456 или просто число", None, None, "Ошибка"

    try:
        gel_post = await Gelbooru(api_key=api_key, user_id=user_id, timeout=60).get_post(post_id)
    except asyncio.TimeoutError:
        return "Превышен таймаут соединения с Gelbooru. Попробуйте позже.", None, None, "Таймаут"
    except GelbooruNotFoundException:
        return f"Пост с ID {post_id} не найден", None, None, "Ошибка"
    except GelbooruException as e:
        if "525" in str(e):
            return "Сайт Gelbooru временно недоступен (ошибка 525). Попробуйте позже.", None, None, "Ошибка соединения"
        else:
            return f"Ошибка при обращении к Gelbooru: {e}", None, None, "Ошибка"
    except Exception as e:
        return f"Непредвиденная ошибка: {e}", None, None, "Ошибка"

    raw_tags = gel_post.get_tags()
    filtered_raw = filter_tags(raw_tags, ignore_list)
    final_tags = []
    for tag in filtered_raw:
        if getattr(shared.opts, "gpr_replaceUnderscores", True):
            exclusion_list = getattr(shared.opts, "gpr_undersocreReplacementExclusionList", "").split(',')
            if tag not in exclusion_list:
                tag = tag.replace("_", " ")
        final_tags.append(tag)

    preview = None
    async with aiohttp.ClientSession() as session:
        preview = await fetch_image(session, gel_post.file_url, timeout=60)

    post_url = str(gel_post)
    return ', '.join(final_tags), preview, post_url, "Успешно"

class GPRScript(scripts.Script):

    def __init__(self) -> None:
        super().__init__()

    def title(self):
        return "Gelbooru Prompt"

    def show(self, is_img2img):
        return scripts.AlwaysVisible

    def ui(self, is_img2img):
        with gr.Accordion("Gelbooru", open=False):
            with gr.Accordion("Случайный пост", open=True):
                include_tags_textbox = gr.Textbox(label='Включить теги', placeholder="например: 1girl, blue_hair, solo")
                exclude_tags_textbox = gr.Textbox(label='Исключить теги', placeholder="например: nsfw, text, watermark")

                with gr.Row():
                    send_text_button = gr.Button(value='Случайный', variant='primary', size='sm')
                    clear_button = gr.Button(value='Очистить', size='sm')

                result_tags_textbox = gr.Textbox(label='Теги', show_copy_button=True, interactive=False)

                preview_image = gr.Image(interactive=False, show_label=False, height=400)

                url_textbox = gr.Textbox(label='Ссылка на пост', show_copy_button=True, interactive=False)

            with gr.Accordion("Загрузить пост по ссылке или ID", open=False):
                post_input_textbox = gr.Textbox(label='Ссылка на пост или ID', placeholder="https://gelbooru.com/index.php?page=post&s=view&id=123456 или просто 123456")
                with gr.Row():
                    load_post_button = gr.Button(value='Загрузить пост', variant='secondary', size='sm')
                    clear_manual_button = gr.Button(value='Очистить', size='sm')

                manual_tags_textbox = gr.Textbox(label='Теги поста', show_copy_button=True, interactive=False)
                manual_preview_image = gr.Image(interactive=False, show_label=False, height=400)
                manual_url_textbox = gr.Textbox(label='Ссылка на пост', show_copy_button=True, interactive=False)
                manual_status_textbox = gr.Textbox(label='Статус', interactive=False)

        with contextlib.suppress(AttributeError):
            send_text_button.click(
                fn=get_random_tags,
                inputs=[include_tags_textbox, exclude_tags_textbox],
                outputs=[result_tags_textbox, preview_image, url_textbox]
            )
            clear_button.click(
                fn=lambda: (None, None, None),
                inputs=None,
                outputs=[preview_image, url_textbox, result_tags_textbox]
            )

            load_post_button.click(
                fn=get_post_by_url,
                inputs=[post_input_textbox],
                outputs=[manual_tags_textbox, manual_preview_image, manual_url_textbox, manual_status_textbox]
            )
            clear_manual_button.click(
                fn=lambda: (None, None, None, None),
                inputs=None,
                outputs=[manual_tags_textbox, manual_preview_image, manual_url_textbox, manual_status_textbox]
            )

        return [include_tags_textbox, exclude_tags_textbox, send_text_button, clear_button,
                result_tags_textbox, preview_image, url_textbox,
                post_input_textbox, load_post_button, clear_manual_button,
                manual_tags_textbox, manual_preview_image, manual_url_textbox, manual_status_textbox]

    def on_ui_settings():
        GPR_SECTION = ("gpr", "Gelbooru Prompt")

        gpr_options = {
            "gpr_api_key": shared.OptionInfo(
                "", "API ключ", gr.Textbox
            ).info("<a href=\"https://gelbooru.com/index.php?page=account&s=options\" target=\"_blank\">Настройки аккаунта</a>"),
            "gpr_user_id": shared.OptionInfo(
                "", "ID пользователя", gr.Textbox
            ).info("<a href=\"https://gelbooru.com/index.php?page=account&s=options\" target=\"_blank\">Настройки аккаунта</a>"),
            "gpr_replaceUnderscores": shared.OptionInfo(True, "Заменять подчёркивания на пробелы при вставке"),
            "gpr_undersocreReplacementExclusionList": shared.OptionInfo(
                "0_0,(o)_(o),+_+,+_-,._.,<o>_<o>,<|>_<|>,=_=,>_<,3_3,6_9,>_o,@_@,^_^,o_o,u_u,x_x,|_|,||_||",
                "Список исключений для замены подчёркиваний"
            ).info("Укажите теги, в которых не нужно заменять подчёркивания на пробелы, через запятую."),
            "gpr_ignore_tags": shared.OptionInfo(
                "", "Игнорируемые теги (не показывать)", gr.Textbox
            ).info("Укажите теги, которые будут удалены из результата (не влияют на поиск). Через запятую, можно с пробелами или подчёркиваниями."),
        }

        for key, opt in gpr_options.items():
            opt.section = GPR_SECTION
            shared.opts.add_option(key, opt)

    script_callbacks.on_ui_settings(on_ui_settings)

    def after_component(self, component, **kwargs):
        if kwargs.get("elem_id") == "txt2img_prompt":
            self.text2img = component
        if kwargs.get("elem_id") == "img2img_prompt":
            self.img2img = component