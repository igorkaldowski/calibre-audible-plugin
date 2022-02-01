#!/usr/bin/env python
# -*- coding: UTF-8 -*-
# vim:fileencoding=UTF-8:ts=4:sw=4:sta:et:sts=4:ai
from __future__ import (unicode_literals, division, absolute_import,
                        print_function)

__license__   = 'GPL v3'
__copyright__ = '2021, Igor Kaldowski <>'
__docformat__ = 'restructuredtext en'

# import six
from six import text_type as unicode
from six.moves import range

from lxml.html import fromstring, tostring

# from itertools import compress
import socket, re, datetime

# from collections import OrderedDict
from threading import Thread

from calibre.ebooks.metadata.book.base import Metadata
from calibre.library.comments import sanitize_comments_html
from calibre.utils.cleantext import clean_ascii_chars
from calibre.ebooks.metadata import check_isbn
# from calibre.utils.icu import capitalize, lower

import calibre_plugins.audible.config as cfg

import json

class Worker(Thread):  # Get details

    '''
    Get book details from Audnexus in a separate thread
    '''

    def __init__(self, url, result_queue, browser, log, relevance, plugin, timeout=20):
        Thread.__init__(self)
        self.daemon = True
        self.url, self.result_queue = url, result_queue
        self.log, self.timeout = log, timeout
        self.relevance, self.plugin = relevance, plugin
        self.browser = browser.clone_browser()
        self.cover_url = self.audible_id = self.isbn = None

    def run(self):
        try:
            self.get_details()
        except:
            self.log.exception('get_details failed for url: %r'%self.url)

    def get_details(self):
        try:
            self.log.info('Audnexus.us   url: %r'%self.url)
            raw = self.browser.open_novisit(self.url, timeout=self.timeout).read()

        except Exception as e:
            if callable(getattr(e, 'getcode', None)) and \
                    e.getcode() == 404:
                self.log.error('URL malformed: %r'%self.url)
                return
            attr = getattr(e, 'args', [None])
            attr = attr if attr else [None]
            if isinstance(attr[0], socket.timeout):
                msg = 'Audnexus.us timed out. Try again later.'
                self.log.error(msg)
            else:
                msg = 'Failed to make details query: %r'%self.url
                self.log.exception(msg)
            return

        root = json.loads(raw)
        if not root:
            self.log.error('Failed to get json result for query')
            return

        self.parse_details(root)

    def parse_details(self, root):
        try:
            audible_id = root["asin"]
        except:
            self.log.exception('Error parsing audible id for url: %r'%self.url)
            audible_id = None

        try:
            title = root["title"]
        except:
            self.log.exception('Error parsing title for url: %r'%self.url)
            title = None

        try:
            authors = self.parse_authors(root)
        except:
            self.log.exception('Error parsing authors for url: %r'%self.url)
            authors = []

        if not title or not authors or not audible_id:
            self.log.error('Could not find title/authors/audible id for %r'%self.url)
            self.log.error('Audible: %r Title: %r Authors: %r'%(audible_id, title,
                authors))
            return

        mi = Metadata(title, authors)
        self.log.info('parse_details - audible_id: {0}, mi: {1}'.format(audible_id,mi))
        mi.set_identifier('audible', audible_id)
        self.audible_id = audible_id

        try:
            narrators = self.parse_narrators(root)
        except:
            self.log.exception('Error parsing authors for url: %r'%self.url)
            authors = []

        try:
            (series, series_index) = self.parse_series(root)
            if series is not None:
                mi.series = series
                mi.series_index = series_index
        except:
            self.log.exception('Error parsing series for url: %r'%self.url)

        try:
            mi.rating = float(root["rating"])
        except:
            self.log.exception('Error parsing ratings for url: %r'%self.url)

        try:
            summary = root["summary"]
        except:
            self.log.exception('Error parsing comments for url: %r'%self.url)

        try:
            self.cover_url = root["image"]
        except:
            self.log.exception('Error parsing cover for url: %r'%self.url)
        mi.has_cover = bool(self.cover_url)

        try:
            tags = self.parse_tags(root)
            if tags is not None:
                mi.tags = tags
        except:
            self.log.exception('Error parsing tags for url: %r'%self.url)

        try:
            mi.publisher = root["publisherName"]
        except:
            self.log.exception('Error parsing publisher for url: %r'%self.url)

        try:
            mi.pubdate = self._convert_date_text(root["releaseDate"])
        except:
            self.log.exception('Error parsing date for url: %r'%self.url)

        try:
            mi.language = root["language"].capitalize()
        except:
            self.log.exception('Error parsing language for url: %r'%self.url)

        commets = ""
        if narrators is not None:
            commets = '<p id="narrators">Narrators: ' +  ', '.join(narrators)  + '</p>' + commets 
        if summary is not None:
            commets = commets + summary
        
        mi.comments = commets

        mi.source_relevance = self.relevance

        if self.audible_id:
            if self.isbn:
                self.plugin.cache_isbn_to_identifier(self.isbn, self.audible_id)
            if self.cover_url:
                self.plugin.cache_identifier_to_cover_url(self.audible_id, self.cover_url)

        self.plugin.clean_downloaded_metadata(mi)
        self.result_queue.put(mi)

    def _convert_date_text(self, date_text):
        year = int(datetime.datetime.strptime(date_text, '%Y-%m-%dT%H:%M:%S.%fZ').strftime('%Y'))
        month = int(datetime.datetime.strptime(date_text, '%Y-%m-%dT%H:%M:%S.%fZ').strftime('%m'))
        day = int(datetime.datetime.strptime(date_text, '%Y-%m-%dT%H:%M:%S.%fZ').strftime('%d'))
        return datetime.datetime(year, month, day, 0, 0, 0)

    def parse_authors(self, root):
        authors = []
        for author in root["authors"]:
            authors.append(author["name"])
        return authors

    def parse_narrators(self, root):
        narrators = []
        for narrator in root["narrators"]:
            narrators.append(narrator["name"])
        return narrators
        
    def parse_series(self, root):
        series_node = root["seriesPrimary"]
        if not series_node:
            return (None, None)
            
        series_name = series_node["name"]
        series_split = series_node["position"].split(' ')
        series_index = float(series_split[1])
        self.log.error("parse_series: returning - series_name='%s', series_index='%s'" % (series_name, series_index))
        series = series_name
        return (series, series_index)

    def parse_tags(self, root):
        genres_node = root["genres"]
        if genres_node:
            genre_tags = list()
            for genre_node in genres_node:
                genre_tags.append(genre_node["name"])
            calibre_tags = self._convert_genres_to_calibre_tags(genre_tags)
            if len(calibre_tags) > 0:
                return calibre_tags

    def _convert_genres_to_calibre_tags(self, genre_tags):
        calibre_tag_lookup = cfg.plugin_prefs[cfg.STORE_NAME][cfg.KEY_GENRE_MAPPINGS]
        calibre_tag_map = dict((k.lower(),v) for (k,v) in calibre_tag_lookup.items())
        tags_to_add = list()
        for genre_tag in genre_tags:
            tags = calibre_tag_map.get(genre_tag.lower(), None)
            if tags:
                for tag in tags:
                    if tag not in tags_to_add:
                        tags_to_add.append(tag)
        return list(tags_to_add)