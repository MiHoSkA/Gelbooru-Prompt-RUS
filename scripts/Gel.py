import asyncio
import os
import reprlib
import xml
from datetime import datetime
from random import randint
from typing import *
from urllib.parse import urlparse

import aiohttp
import xmltodict
from furl import furl


class GelbooruException(Exception):
    pass


class GelbooruNotFoundException(GelbooruException):
    pass


class GelbooruImage:

    def __init__(self, payload: dict, gelbooru):
        self._gelbooru = gelbooru 

        payload = {k.strip('@'): v for k, v in payload.items()}

        self.id             = int(payload.get('id', 0) or 0)                     
        self.creator_id     = int(payload.get('creator_id', 0) or 0) or None          
        self.created_at     = _datetime(payload.get('created_at'))                     
        self.file_url       = payload.get('file_url')                                   
        self.filename       = os.path.basename(urlparse(self.file_url).path)            
        self.source         = payload.get('source') or None                             
        self.hash           = payload.get('md5')                
        self.height         = int(payload.get('height'))          
        self.width          = int(payload.get('width'))                                 
        self.rating         = payload.get('rating')                    
        self.has_sample     = payload.get('has_sample', 'false').lower() == 'true'      
        self.has_comments   = payload.get('has_comments', 'false').lower() == 'true'    
        self.has_notes      = payload.get('has_notes', 'false').lower() == 'true'       
        self.has_children   = payload.get('has_children', 'false').lower() == 'true' 
        self.tags           = str(payload.get('tags')).split(' ')                       
        self.change         = datetime.fromtimestamp(int(payload.get('change', 0)))     
        self.directory      = payload.get('directory')                                  
        self.status         = payload.get('status')       
        self.locked         = bool(int(payload.get('post_locked', 0) or 0))     
        self.score          = int(payload.get('score', 0) or 0)  
        self._payload       = payload                                                   

    def __str__(self):
        return f"https://gelbooru.com/index.php?page=post&s=view&id={self.id}"

    def __int__(self):
        return self.id

    def __repr__(self):
        rep = reprlib.Repr()
        return f"<GelbooruImage(id={self.id}, filename={rep.repr(self.filename)}, owner={rep.repr(self.creator_id)})>"
    
    def get_tags(self):
        return self.tags


API_GELBOORU = 'https://gelbooru.com/'

class Gelbooru:
    SORT_COUNT = 'count'
    SORT_DATE = 'date'
    SORT_NAME = 'name'

    SORT_ASC = 'ASC'
    SORT_DESC = 'DESC'

    def __init__(self, api_key: Optional[str] = None,
                 user_id: Optional[str] = None,
                 loop: Optional[asyncio.AbstractEventLoop] = None,
                 api: Optional[str] = API_GELBOORU,
                 timeout: int = 60):  # Добавлен параметр таймаута

        self._api_key = api_key
        self._user_id = user_id
        self._loop = loop
        self._base_url = api
        self._timeout = aiohttp.ClientTimeout(total=timeout)  # Таймаут на весь запрос

    async def get_post(self, post_id: int) -> Optional[GelbooruImage]:

        endpoint = self._endpoint('post')
        endpoint.args['id'] = post_id

        payload = await self._request(str(endpoint))
        payload = xmltodict.parse(payload)

        payload = {k.strip('@'): v for k, v in payload.items()}

        if 'posts' not in payload:
            raise GelbooruNotFoundException(f"Не удалось найти сообщение с таким идентификатором: {post_id}")

        return GelbooruImage(payload['posts']['post'], self)

    async def random_post(self, *, tags: Optional[List[str]] = None,
                          exclude_tags: Optional[List[str]] = None) -> Optional[List[GelbooruImage]]:

        endpoint = self._endpoint('post')
        endpoint.args['limit'] = 1

        tags = self._format_tags(tags, exclude_tags)
        if tags:
            endpoint.args['tags'] = ' '.join(tags)

        payload = await self._request(str(endpoint))
        try:
            payload = xmltodict.parse(payload)

            payload = {k.strip('@'): v for k, v in payload.items()}
        except xml.parsers.expat.ExpatError:
            raise GelbooruException("Gelbooru вернул искаженный ответ")

        count = int(payload['posts']['@count'])
        if not count:
            return None

        offset = randint(0, min(count, 20000))

        return await self.search_posts(tags=tags, exclude_tags=exclude_tags, limit=1, page=offset)

    async def search_posts(self, *, tags: Optional[List[str]] = None,
                           exclude_tags: Optional[List[str]] = None,
                           limit: int = 100,
                           page: int = 0) -> Union[List[GelbooruImage], GelbooruImage]:

        endpoint = self._endpoint('post')
        endpoint.args['limit'] = limit
        endpoint.args['pid'] = page

        tags = self._format_tags(tags, exclude_tags)
        if tags:
            endpoint.args['tags'] = ' '.join(tags)

        payload = await self._request(str(endpoint))
        try:
            payload = xmltodict.parse(payload)
        except xml.parsers.expat.ExpatError:
            raise GelbooruException("Gelbooru вернул искаженный ответ")
        if 'posts' not in payload or 'post' not in payload["posts"]:
            return []

        result = [GelbooruImage(p, self) for p in payload['posts']['post']] \
            if isinstance(payload['posts']['post'], list) \
            else [GelbooruImage(payload['posts']['post'], self)]

        if limit == 1:
            return result[0]
        else:
            return result

    def _endpoint(self, s: str) -> furl:
        endpoint = furl(self._base_url)
        endpoint.args['page'] = 'dapi'
        endpoint.args['s'] = s
        endpoint.args['q'] = 'index'

        if self._api_key:
            endpoint.args['api_key'] = self._api_key
        if self._user_id:
            endpoint.args['user_id'] = self._user_id

        return endpoint

    def _format_tags(self, tags: list, exclude_tags: list):

        tags = [tag.strip().lower().replace(' ', '_') for tag in tags] if tags else []
        exclude_tags = ['-' + tag.strip().lstrip('-').lower().replace(' ', '_') for tag in
                        exclude_tags] if exclude_tags else []

        return tags + exclude_tags

    async def _request(self, url: str) -> bytes:
        # Заголовки для имитации браузера
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        async with aiohttp.ClientSession(loop=self._loop, headers=headers) as session:
            status_code, response = await self._fetch(session, url)

        if status_code == 401:
            raise GelbooruException("Gelbooru код ошибкий 401, вам необходимо войти в свою учетную запись")
        elif status_code not in [200, 201]:
            raise GelbooruException(f"Gelbooru код ошибки, отличный от 200: {response}, этот код: {status_code}")

        return response

    async def _fetch(self, session: aiohttp.ClientSession, url) -> Tuple[int, bytes]:
        async with session.get(url, timeout=self._timeout) as response:
            return response.status, await response.read()


def _datetime(date: str, format='%a %b %d %H:%M:%S %z %Y') -> Optional[datetime]:

    try:
        return datetime.strptime(date, format)
    except ValueError:
        return None