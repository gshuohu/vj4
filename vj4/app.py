import asyncio
import functools
import logging
from os import path

import sockjs
from aiohttp import web

from vj4 import error
from vj4.service import bus
from vj4.service import smallcache
from vj4.util import json
from vj4.util import locale
from vj4.util import options
from vj4.util import tools

options.define('static', default=True, help='Serve static files.')
options.define('ip_header', default='X-Forwarded-For', help='Header name for remote IP.')
options.define('unsaved_session_expire_seconds', default=43200,
               help='Expire time for unsaved session, in seconds.')
options.define('saved_session_expire_seconds', default=2592000,
               help='Expire time for saved session, in seconds.')
options.define('cookie_domain', default=None, help='Cookie domain.')
options.define('cookie_secure', default=False, help='Enable secure cookie flag.')
options.define('registration_token_expire_seconds', default=86400,
               help='Expire time for registration token, in seconds.')
options.define('lostpass_token_expire_seconds', default=3600,
               help='Expire time for lostpass token, in seconds.')
options.define('newmail_token_expire_seconds', default=3600,
               help='Expire time for newmail token, in seconds.')
options.define('url_prefix', default='https://vijos.org', help='URL prefix.')
options.define('cdn_prefix', default='/', help='CDN prefix.')

_logger = logging.getLogger(__name__)


class Application(web.Application):
  def __init__(self):
    super(Application, self).__init__(
      handler_factory=functools.partial(web.RequestHandlerFactory, access_log=None),
      middlewares=[self.error_middleware],
      debug=options.options.debug)
    globals()[self.__class__.__name__] = lambda: self  # singleton

    # Initialize components.
    translation_path = path.join(path.dirname(__file__), 'locale')
    locale.load_translations(translation_path)
    self.loop.run_until_complete(asyncio.gather(tools.ensure_all_indexes(), bus.init()))
    smallcache.init()

    # Load views.
    from vj4.handler import contest
    from vj4.handler import discussion
    from vj4.handler import home
    from vj4.handler import judge
    from vj4.handler import main
    from vj4.handler import problem
    from vj4.handler import record
    from vj4.handler import training
    from vj4.handler import user
    from vj4.handler import i18n
    if options.options.static:
      self.router.add_static('/', path.join(path.dirname(__file__), '.uibuild'), name='static')

  async def error_middleware(self, app, handler):
    async def middleware_handler(request):
      try:
        response = await handler(request)
        return response
      except web.HTTPNotFound as ex:
        from vj4.handler import error
        return await error.NotFoundHandler(request)

    return middleware_handler


def route(url, name):
  def decorate(handler):
    handler.NAME = handler.NAME or name
    handler.TITLE = handler.TITLE or name
    Application().router.add_route('*', url, handler, name=name)
    Application().router.add_route('*', '/d/{domain_id}' + url, handler, name=name + '_with_domain_id')
    return handler

  return decorate


def connection_route(prefix, name):
  def decorate(conn):
    async def handler(msg, session):
      try:
        if msg.tp == sockjs.MSG_OPEN:
          await session.prepare()
          await session.on_open()
        elif msg.tp == sockjs.MSG_MESSAGE:
          await session.on_message(**json.decode(msg.data))
        elif msg.tp == sockjs.MSG_CLOSE:
          await session.on_close()
      except error.UserFacingError as e:
        _logger.warning("Websocket user facing error: %s", repr(e))
        session.close(4000, {'error': e.to_dict()})

    class Manager(sockjs.SessionManager):
      def get(self, id, create=False, request=None):
        if id not in self and create:
          self[id] = self._add(conn(request, id, self.handler,
                                    timeout=self.timeout, loop=self.loop, debug=self.debug))
        return self[id]

    sockjs.add_endpoint(Application(), handler, name=name, prefix=prefix,
                        manager=Manager(name, Application(), handler, Application().loop))
    return conn

  return decorate
