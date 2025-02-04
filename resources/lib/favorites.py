# -*- coding: utf-8 -*-
# Copyright: (c) 2019, Dag Wieers (@dagwieers) <dag@wieers.com>
# GNU General Public License v3.0 (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
"""Implementation of Favorites class"""

from __future__ import absolute_import, division, unicode_literals

try:  # Python 3
    from urllib.parse import unquote
except ImportError:  # Python 2
    from urllib2 import unquote

from kodiutils import (container_refresh, get_cache, get_setting_bool, get_url_json,
                       has_credentials, input_down, invalidate_caches, localize,
                       multiselect, notification, ok_dialog, update_cache)
from utils import url_to_program


class Favorites:
    """Track, cache and manage VRT favorites"""

    GRAPHQL_URL = 'https://www.vrt.be/vrtnu-api/graphql/v1'
    FAVORITES_CACHE_FILE = 'favorites.json'

    def __init__(self):
        """Initialize favorites, relies on XBMC vfs and a special VRT token"""
        self._favorites = {}  # Our internal representation

    @staticmethod
    def is_activated():
        """Is favorites activated in the menu and do we have credentials ?"""
        return get_setting_bool('usefavorites', default=True) and has_credentials()

    def refresh(self, ttl=None):
        """Get a cached copy or a newer favorites from VRT, or fall back to a cached file"""
        if not self.is_activated():
            return
        favorites_dict = get_cache(self.FAVORITES_CACHE_FILE, ttl)
        if not favorites_dict:
            favorites_dict = self._generate_favorites_dict(self.get_favorites())
        if favorites_dict is not None:
            from json import dumps
            self._favorites = favorites_dict
            update_cache(self.FAVORITES_CACHE_FILE, dumps(self._favorites))

    def update(self, program_name, title, program_id, is_favorite=True):
        """Set a program as favorite, and update local copy"""

        # Survive any recent updates
        self.refresh(ttl=5)

        if is_favorite is self.is_favorite(program_name):
            # Already followed/unfollowed, nothing to do
            return True

        # Lookup program_id
        if program_id == 'None' or program_id is None:
            program_id = self.get_program_id_graphql(program_name)

        # Update local favorites cache
        if is_favorite is True:
            self._favorites[program_name] = {
                'program_id': program_id,
                'title': title}
        else:
            del self._favorites[program_name]

        # Update cache dict
        from json import dumps
        update_cache(self.FAVORITES_CACHE_FILE, dumps(self._favorites))
        invalidate_caches('my-offline-*.json', 'my-recent-*.json')

        # Update online
        self.set_favorite_graphql(program_id, title, is_favorite)
        return True

    def get_favorites(self):
        """Get favorites using GraphQL API"""
        from tokenresolver import TokenResolver
        access_token = TokenResolver().get_token('vrtnu-site_profile_at')
        favorites_json = {}
        if access_token:
            headers = {
                'Authorization': 'Bearer ' + access_token,
                'Content-Type': 'application/json',
            }
            graphql = """
                query Favs(
                  $listId: ID!
                  $endCursor: ID!
                  $pageSize: Int!
                ) {
                  list(listId: $listId) {
                    __typename
                    ... on PaginatedTileList {
                      paginated: paginatedItems(first: $pageSize, after: $endCursor) {
                        edges {
                          node {
                            __typename
                            ...programTile
                          }
                        }
                      }
                    }
                  }
                }
                fragment programTile on ProgramTile {
                  id
                  title
                  description
                  action {
                    __typename
                    ...action
                  }
                }
                fragment action on Action {
                  __typename
                  ... on LinkAction {
                    link
                    linkType
                    __typename
                  }
                }
            """
            payload = {
                'operationName': 'Favs',
                'variables': {
                    'listId': 'dynamic:/vrtnu.model.json@favorites-list-video',
                    'endCursor': '',
                    'pageSize': 1000,
                },
                'query': graphql,
            }
            from json import dumps
            data = dumps(payload).encode('utf-8')
            favorites_json = get_url_json(url=self.GRAPHQL_URL, cache=None, headers=headers, data=data, raise_errors='all')
        return favorites_json

    def get_program_id_graphql(self, program_name):
        """Get programId from programName using GraphQL API"""
        from tokenresolver import TokenResolver
        access_token = TokenResolver().get_token('vrtnu-site_profile_at')
        program_id = None
        if access_token:
            headers = {
                'Authorization': 'Bearer ' + access_token,
                'Content-Type': 'application/json',
            }
            graphql = """
                query Page($id: ID!) {
                  page(id: $id) {
                    ... on IPage {
                      id
                    }
                  }
                }
            """
            payload = {
                'variables': {
                    'id': '/vrtnu/a-z/{}.model.json'.format(program_name)
                },
                'query': graphql,
            }
            from json import dumps
            data = dumps(payload).encode('utf-8')
            page_json = get_url_json(url=self.GRAPHQL_URL, cache=None, headers=headers, data=data, raise_errors='all')
            program_id = page_json.get('data').get('page').get('id')
        return program_id

    def set_favorite_graphql(self, program_id, title, is_favorite=True):
        """Set favorite using GraphQL API"""
        from tokenresolver import TokenResolver
        access_token = TokenResolver().get_token('vrtnu-site_profile_at')
        result_json = {}
        if access_token:
            headers = {
                'Authorization': 'Bearer ' + access_token,
                'Content-Type': 'application/json',
            }
            graphql_query = """
                mutation setFavorite($input: FavoriteActionInput!) {
                  setFavorite(input: $input) {
                    __typename
                    id
                    favorite
                  }
                }
            """
            payload = {
                'operationName': 'setFavorite',
                'variables': {
                    'input': {
                        'id': program_id,
                        'title': title,
                        'favorite': is_favorite,
                    },
                },
                'query': graphql_query,
            }
            from json import dumps
            data = dumps(payload).encode('utf-8')
            result_json = get_url_json(url=self.GRAPHQL_URL, cache=None, headers=headers, data=data, raise_errors='all')
        return result_json

    def is_favorite(self, program_name):
        """Is a program a favorite ?"""
        return program_name in self._favorites

    def follow(self, program_name, title, program_id=None):
        """Follow your favorite program"""
        succeeded = self.update(program_name, title, program_id, True)
        if succeeded:
            notification(message=localize(30411, title=title))
            container_refresh()

    def unfollow(self, program_name, title, program_id=None, move_down=False):
        """Unfollow your favorite program"""
        succeeded = self.update(program_name, title, program_id, False)
        if succeeded:
            notification(message=localize(30412, title=title))
            # If the current item is selected and we need to move down before removing
            if move_down:
                input_down()
            container_refresh()

    def programs(self):
        """Return all favorite programs"""
        return list(self._favorites.keys())

    @staticmethod
    def _generate_favorites_dict(favorites_json):
        """Generate a simple favorites dict with programIds and programNames"""
        favorites_dict = {}
        try:
            if favorites_json:
                edges = favorites_json.get('data', {}).get('list', {}).get('paginated', {}).get('edges', {})
                for item in edges:
                    program_name = url_to_program(item.get('node').get('action').get('link'))
                    program_id = item.get('node').get('id')
                    title = item.get('node').get('title')
                    favorites_dict[program_name] = {
                        'program_id': program_id,
                        'title': title}
        except AttributeError:
            pass
        return favorites_dict

    def manage(self):
        """Allow the user to unselect favorites to be removed from the listing"""
        self.refresh(ttl=0)
        if not self._favorites:
            ok_dialog(heading=localize(30418), message=localize(30419))  # No favorites found
            return

        def by_title(tup):
            """Sort by title"""
            _, value = tup
            return value.get('title')

        items = [{'program_id': value.get('program_id'), 'program_name': key,
                  'title': unquote(value.get('title'))} for key, value in sorted(list(self._favorites.items()), key=by_title)]
        titles = [item['title'] for item in items]
        preselect = list(range(0, len(items)))
        selected = multiselect(localize(30420), options=titles, preselect=preselect)  # Please select/unselect to follow/unfollow
        if selected is not None:
            for idx in set(preselect).difference(set(selected)):
                self.unfollow(program_name=items[idx]['program_name'], title=items[idx]['title'], program_id=items[idx]['program_id'])
            for idx in set(selected).difference(set(preselect)):
                self.follow(program_name=items[idx]['program_name'], title=items[idx]['title'], program_id=items[idx]['program_id'])
