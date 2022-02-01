#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__   = 'GPL v3'
__copyright__ = '2021, Igor Kaldowski <>'
__docformat__ = 'restructuredtext en'

import time, json, re

try:
    from urllib.parse import quote
except ImportError:
    from urllib import quote

try:
    from queue import Empty, Queue
except ImportError:
    from Queue import Empty, Queue
import six
from six import text_type as unicode

from lxml.html import fromstring

from calibre import as_unicode
from calibre import prints
from calibre.ebooks.metadata.sources.base import Source
from calibre.utils.icu import lower
from calibre.utils.cleantext import clean_ascii_chars
from calibre.utils.localization import get_udc
# from calibre.ebooks.metadata import check_isbn

from calibre.constants import DEBUG

import sys
from PyQt5 import Qt as QtGui
from PyQt5.QtCore import *
from PyQt5.Qt import QLabel, QTableWidget,  QIcon,  QPixmap
from PyQt5.QtWidgets import *

class Audible(Source):

    name                    = 'Audible'
    description             = _('Download book metadata and covers from Audible')
    author                  = 'Igor Kaldowski'
    version                 = (0,  0, 1)
    minimum_calibre_version = (3, 41, 0)

    capabilities = frozenset(['identify', 'cover'])
    touched_fields = frozenset(['title', 'authors', 'identifier:audible',
        'rating', 'comments', 'publisher', 'pubdate',
        'series', 'tags', 'languages'])
    has_html_comments = True
    supports_gzip_transfer_encoding = True
    prefer_results_with_isbn = False

    ID_NAME   = 'audible'
    AUDIBLE_URL  = 'https://www.audible.com'
    AUDIBLE_API_URL  = 'https://api.audible.com'
    AUDNEXUS_URL  = 'https://api.audnex.us'
    
    AUDIBLE_PATH = '/pd/'
    AUDIBLE_API_PATH = '/1.0/catalog/products?'
    AUDNEXUS_PATH = '/books/'
    
    AUDIBLE_QUERY = '?ipRedirectOverride=true'
    AUDIBLE_API_QUERY = 'num_results=25&products_sort_by=Relevance'
    AUDIBLE_API_TITLE_QUERY = '&title='
    AUDIBLE_API_AUTHOR_QUERY = '&author='

    def config_widget(self):
        '''
        Overriding the default configuration screen for our own custom configuration
        '''
        from calibre_plugins.audible.config import ConfigWidget
        return ConfigWidget(self)

    def get_book_url(self, identifiers):
        audible_id = identifiers.get(self.ID_NAME, None)
        if audible_id:
            return (self.ID_NAME, audible_id,
                    '%s%s%s%s'%(Audible.AUDIBLE_URL, Audible.AUDIBLE_PATH, audible_id, Audible.AUDIBLE_QUERY))

    def create_query(self, log, title=None, authors=None, identifiers={}, asin=None):
        audible_id = identifiers.get(self.ID_NAME, None)
        if asin:
            audible_id = asin
        
        q = ''
        if audible_id:
            q = self.AUDNEXUS_URL + self.AUDNEXUS_PATH + audible_id
        elif title or authors:
            q = self.AUDIBLE_API_URL + self.AUDIBLE_API_PATH + self.AUDIBLE_API_QUERY
            if title:
                q = q + self.AUDIBLE_API_TITLE_QUERY + title
            if authors:
                q = q + self.AUDIBLE_API_AUTHOR_QUERY + authors[0]

        if not q:
            return None
        return q

    def get_cached_cover_url(self, identifiers):
        url = None
        audible_id = identifiers.get(self.ID_NAME, None)
        if audible_id is not None:
            url = self.cached_identifier_to_cover_url(audible_id)

        return url

    def identify(self, log, result_queue, abort, title=None, authors=None, identifiers={}, timeout=30):

        '''
        Note this method will retry without identifiers automatically if no
        match is found with identifiers.
        '''
        audible_id = identifiers.get(self.ID_NAME, None)

        matches = []
        audible_id = identifiers.get(self.ID_NAME, None)
        log.info('\nTitle: %s\nAuthors: %s\n'%(title, authors))
        br = self.browser

        if audible_id:
            matches.append(self.create_query(log, title=title, authors=authors, identifiers=identifiers))
        else:
            query = self.create_query(log, title=title, authors=authors, identifiers=identifiers)

            response = None
            if query is None:
                log.error('Insufficient metadata to construct query')
                return

            try:
                log.info('Query: %s'%query)
                response = br.open_novisit(query, timeout=timeout)

            except Exception as e:
                if callable(getattr(e, 'getcode', None)) and e.getcode() == 404:
                    log.error('No matches for identify query')
                    return as_unicode(e)

            if response:
                try:
                    raw = response.read()
                    raw = json.loads(raw)
                    if not raw:
                        log.error('Failed to get raw result for query')
                        return
                    for product in raw["products"]:
                        matches.append(self.create_query(log, title=title, authors=authors, identifiers=identifiers, asin=product["asin"]))
                except:
                    msg = 'Failed to get Audible results for query'
                    log.exception(msg)
                    return msg

        if abort.is_set():
            return
        
        from calibre_plugins.audible.worker import Worker
        workers = [Worker(url, result_queue, br, log, i, self) for i, url in
                enumerate(matches)]

        for w in workers:
            w.start()
            # Don't send all requests at the same time
            time.sleep(0.1)

        while not abort.is_set():
            a_worker_is_alive = False
            for w in workers:
                w.join(0.2)
                if abort.is_set():
                    break
                if w.is_alive():
                    a_worker_is_alive = True
            if not a_worker_is_alive:
                break

        return None

    # To Do
    def download_cover(self, log, result_queue, abort,
            title=None, authors=None, identifiers={}, timeout=30):
        cached_url = self.get_cached_cover_url(identifiers)
        if cached_url is None:
            log.info('No cached cover found, running identify')
            rq = Queue()
            self.identify(log, rq, abort, title=title, authors=authors, hiddenauthors=authors,
                    identifiers=identifiers)
            if abort.is_set():
                return
            results = []
            while True:
                try:
                    results.append(rq.get_nowait())
                except Empty:
                    break
            results.sort(key=self.identify_results_keygen(
                title=title, authors=authors, identifiers=identifiers))
            for mi in results:
                cached_url = self.get_cached_cover_url(mi.identifiers)
                if cached_url is not None:
                    break
        if cached_url is None:
            log.info('No cover found')
            return

        if abort.is_set():
            return
        br = self.browser
        log.info('Downloading cover from:', cached_url)
        try:
            cdata = br.open_novisit(cached_url, timeout=timeout).read()
            result_queue.put((self, cdata))
        except:
            log.exception('Failed to download cover from:', cached_url)


if __name__ == '__main__':  # tests
    # To run these test use:
    # calibre-debug -e __init__.py
    from calibre.ebooks.metadata.sources.test import (test_identify_plugin, title_test, authors_test, series_test)

    atests = [
        (
          {
             'identifiers':{'audible': 'B002V19RO6'},
             'title':'1984',
             'authors':['George Orwell']
          },
          [
             title_test('1984'),
             authors_test(['George Orwell']),
          ]
        ),

    ]

    def do_test(atests, start=0, stop=None):
        if stop is None:
            stop = len(atests)
        atests = atests[start:stop]
        test_identify_plugin(Audible.name, atests)

    do_test(atests)

# series_test('Opowieści z meekhańskiego pogranicza', 1.0),
