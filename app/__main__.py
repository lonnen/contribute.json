#!/usr/bin/env python

import os
import json

from werkzeug.contrib.cache import MemcachedCache
from flask import (
    Flask, request, make_response, jsonify, send_file, render_template, abort
)
from flask.views import MethodView
import jsonschema
import requests

MEMCACHE_URL = os.environ.get('MEMCACHE_URL', '127.0.0.1:11211').split(',')
DEBUG = os.environ.get('DEBUG', False) in ('true', '1', 'y', 'yes')

SCHEMA_URL = 'https://raw.githubusercontent.com/mozilla/contribute.json/master/schema.json'

SAMPLE = """
{
  "name": "contribute.json",
  "description": "Standard to describe open source projects",
  "repository": {
    "url": "https://github.com/mozilla/contribute.json",
    "license": "MPL2"
  },
  "keywords": [
    "JSON",
    "Python",
    "Flask"
  ]
}
""".strip()


app = Flask(__name__)
cache = MemcachedCache(MEMCACHE_URL)


def cache_set(key, value, *args, **options):
    if isinstance(value, (dict, list, bool)):
        value = json.dumps(value)
    cache.set(key, value, *args, **options)


def cache_get(key, default=None):
    value = cache.get(key)
    if value is None:
        value = default
    if value is not None and not isinstance(value, (dict, list, bool)):
        value = json.loads(value)
    return value


@app.route('/contribute.json')
def this_contribute_json():
    # See stackato.yml and how it copies this file from the root
    return send_file('contribute.json')


@app.route('/')
def index_html():
    return catch_all('index.html')


@app.route('/<path:path>')
def catch_all(path):
    context = {
        'DEBUG': DEBUG,
        'SAMPLE': SAMPLE,
    }
    # if path == 'favicon.ico':
    #     path = 'static/favicon.ico'
    _, ext = os.path.splitext(path)
    if path and ext in ('.png', '.gif', '.css', '.js'):
        # most likely something's gone wrong
        default = False
    else:
        path = path or 'index.html'
        default = True
    # print "PATH", path, os.path.isfile(path)
    if os.path.isfile(os.path.join('templates', path)):
        return render_template(path, **context)
    elif os.path.isfile(os.path.join('static', path)):
        return send_file(os.path.join('static', path))
    elif os.path.isfile(path):
        return send_file(path)
    elif default:
        return render_template('index.html', **context)
    else:
        abort(404)


class ValidationView(MethodView):

    def post(self):
        if 'url' in request.args:
            url = request.args['url']
            # We need to make an exception. We can't load this sites
            # /contribute.json because since this is running in a single-thread
            # single-worker, we're running into a strange chicken and egg
            # situation.
            if url == request.host_url + 'contribute.json':
                url = (
                    'https://raw.githubusercontent.com/mozilla/contribute.json'
                    '/master/contribute.json'
                )
            try:
                response = requests.get(url)
                content = response.json()
            except (ValueError, requests.exceptions.RequestException) as exp:
                return jsonify({'request_error': str(exp)})
        elif request.data:
            try:
                content = json.loads(request.data)
            except ValueError as exp:
                return jsonify({
                    'request_error': str(exp),
                    'response': request.data,
                })
            url = None

        schema_content = cache_get('schema')
        if schema_content is None:
            schema = requests.get(SCHEMA_URL)
            schema_content = schema.json()
            cache_set('schema', schema_content, 60 * 60)

        context = {
            'schema': schema_content,
            'schema_url': SCHEMA_URL,
            'response': content,
        }
        if url:
            context['url'] = url

        try:
            jsonschema.validate(
                content,
                schema_content
            )
            context['errors'] = None
        except jsonschema.ValidationError as error:
            context['validation_error'] = error.message
        except jsonschema.SchemaError as error:
            context['schema_error'] = error.message

        previous_urls = cache_get('urls_submitted', [])
        if url in previous_urls:
            previous_urls.remove(url)
        previous_urls.insert(0, url)
        cache_set('urls_submitted', previous_urls, 60 * 60 * 24 * 10)

        return jsonify(context)


app.add_url_rule('/validate', view_func=ValidationView.as_view('validate'))



class ValidateUrlView(MethodView):

    def post(self):
        url = request.json['url']
        result = cache_get('validation-%s' % url)
        if result is None:
            result = {
                'url': url,
            }
            try:
                r = requests.get(url)
                result['status_code'] = r.status_code
            except requests.ConnectionError:
                result['status_code'] = 500

            if result['status_code'] >= 200 and result['status_code'] < 500:
                cache_set('validation-%s' % url, result, 60)
        return jsonify(result)


app.add_url_rule('/validateurl',
                 view_func=ValidateUrlView.as_view('validateurl'))

class ExamplesView(MethodView):

    def get(self):
        urls = cache_get('urls_submitted', [])
        this_url = 'https://raw.githubusercontent.com/mozilla/contribute.json/master/contribute.json'
        if this_url not in urls:
            urls.append(this_url)
        return jsonify({'urls': urls})


app.add_url_rule('/examples.json', view_func=ExamplesView.as_view('examples'))


if __name__ == '__main__':
    app.debug = DEBUG
    port = int(os.environ.get('PORT', 5000))
    host = os.environ.get('HOST', '0.0.0.0')
    app.run(host=host, port=port)
