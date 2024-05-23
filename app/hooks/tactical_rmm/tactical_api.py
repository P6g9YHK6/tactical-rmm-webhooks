import sys, getopt
import requests
import json
import copy
import uuid
import hashlib
from os import environ as env
from time import sleep
from requests.exceptions import SSLError, ConnectionError

TRMM_TOKEN = env.get('TRMM_TOKEN', None)
TRMM_URL = env.get('TRMM_URL', None)
GITLAB_TOKEN = env.get('GITLAB_TOKEN', None)
GITLAB_URL = env.get('GITLAB_URL', None)
GITLAB_PROJECT_ID = env.get('GITLAB_PROJECT_ID', None)

api = {
    "auth": {
        "url": "",
        "key": ""
    },
    "queries": {
        "get_all_scripts": {
            "url": ["scripts"],
            "url_mods": {
                "keys": {},
            },
            "method": "GET",
            "headers": {},
            "params": {},
            "data": {}
        },
        "get_script_content": {
            "url": ["scripts", "", "download"],
            "url_mods": {
                "keys": {
                    "1": "script_id"
                },
                "script_id": 0
            },
            "method": "GET",
            "headers": {},
            "params": {
                "with_snippets": False
            },
            "data": {}
        },
        "publish_script": {
            "url": ["scripts/"],
            "url_mods": {
                "keys": {},
            },
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "charset": "utf8"
            },
            "params": {},
            "data": {
                "name": "test",
                "shell": "python",
                "default_timeout": 90,
                "args": [],
                "script_body": "",
                "run_as_user": False,
                "env_vars": [],
                "description": "",
                "supported_platforms": ["windows"],
            }
        }
    }
}

def build_query(query_key: str, url_mods: dict = {}):
    query = copy.deepcopy(api['queries'][query_key])
    query['headers']['User-Agent'] = "Mozilla/5.0 (Windows NT 10.0; rv:91.0) Gecko/20100101 Firefox/91.0"
    query['headers']['X-API-KEY'] = api['auth']['key']
    query['url_mods'].update(url_mods)

    final_url = api['auth']['url']

    for idx in range(len(query['url'])):
        tmp = query['url_mods'][query['url_mods']['keys'][str(idx)]] if str(idx) in query['url_mods']['keys'] else query['url'][idx]
        final_url = final_url + '/' + str(tmp)
    query['url'] = final_url
    return query

def request_with_retry(query: dict):
    for _ in range(10):
        try:
            response = requests.request(query['method'], query['url'], data=json.dumps(query['data']), headers=query['headers'])
            return response
        except (ConnectionError, SSLError) as e:
            pass
        sleep(0.5)
    return None

def api_call(query: dict):
    if 'data' in query and 'webhook_hash' in query['data']:
        del query['data']['webhook_hash']
    response = request_with_retry(query)
    if response is None:
        return {
            "status": None,
            "content": {}
        }
    if response.status_code != 200:
        print(response.status_code)
        return {
            "status": response.status_code,
            "content": {}
        }
    return {
        "status": response.status_code,
        "content": json.loads(response.text) if query['method'] == 'GET' else {}
    }

def get_scripts():
    query = build_query('get_all_scripts')
    ret = api_call(query)
    return ret['content'], ret['status']

def get_script_content(script_id):
    query = build_query('get_script_content', {"script_id": script_id})
    ret = api_call(query)
    return ret['content'], ret['status']

def create_script(script_body: str, script_metadata: dict):
    query = build_query('publish_script')
    if script_metadata is None:
        query['data']['name'] = 'unnamed script - %s' % uuid.uuid4()
    else:
        query['data'] = script_metadata

    if script_body is not None:
        query['data']['script_body'] = script_body
    ret = api_call(query)
    return ret['content'], ret['status']

def update_script(script: dict):
    query = build_query('publish_script')
    query['method'] = 'PUT'
    query['url'] = query['url'] + '%s/' % script['id']
    query['data'] = script
    ret = api_call(query)
    return ret['content'], ret['status']

def get_scripts_with_content():
    all_scripts, _ = get_scripts()
    scripts = []
    for s in all_scripts:
        if s['script_type'] == 'userdefined' and 'hidden' in s and not s['hidden']:
            code, _ = get_script_content(s['id'])
            new_script = {
                "name": s['name'],
                "shell": s["shell"],
                "default_timeout": s['default_timeout'],
                "args": s['args'],
                "script_body": code['code'],
                "run_as_user": s['run_as_user'],
                "env_vars": s['env_vars'],
                "description": s['description'],
                "supported_platforms": s['supported_platforms'],
                "category": s['category'],
                "id": int(s['id']),
                "script_type": 'userdefined'
            }
            new_script['webhook_hash'] = str((hashlib.sha1(new_script['script_body'].encode())).hexdigest())
            scripts.append(new_script)
    return scripts

def get_gh_script(name, scripts):
    for s in scripts:
        if s['script']['name'] == name:
            return s
    return None

def get_trmm_script(name, scripts):
    for s in scripts:
        if s['name'] == name:
            return s
    return None

def patch_script_from_gh(gh_script, trmm_script):
    new_script = trmm_script
    new_script['script_body'] = gh_script['script_body']
    for k in gh_script.keys():
        trmm_script[k] = gh_script[k]
    update_script(new_script)

def recursive_diff(a, b, prop):
    if type(a) != dict and type(b) != dict:
        if a != b:
            print('property mismatch: %s [%s] [%s]' % (prop, repr(a), repr(b)))
        return True if a != b else False
    for k in a.keys():
        if k not in b:
            print(f"property mismatch: {prop} - {k} missing in b")
            return True
        if recursive_diff(a[k], b[k], k):
            return True
    return False

def diff_script(gh_script, trmm_script):
    if 'hash' in gh_script['script'] and gh_script['script']['hash'] != trmm_script['webhook_hash']:
        print('hash mismatch')
        return True
    return recursive_diff(gh_script['script'], trmm_script, 'obj')



def get_script_hashes():
    headers = {
        "PRIVATE-TOKEN": GITLAB_TOKEN
    }
    api_url = f"{GITLAB_URL}/api/v4/projects/{GITLAB_PROJECT_ID}/repository/tree?recursive=true"
    
    response = requests.get(api_url, headers=headers)
    
    if response.status_code != 200:
        print(f"Failed to fetch scripts from GitLab: {response.status_code}")
        return []
    
    files = response.json()
    scripts = []
    
    for file in files:
        if file['type'] == 'blob':
            script_url = f"{GITLAB_URL}/api/v4/projects/{GITLAB_PROJECT_ID}/repository/files/{file['path'].replace('/', '%2F')}/raw?ref=main"
            script_response = requests.get(script_url, headers=headers)
            if script_response.status_code == 200:
                script_content = script_response.text
                script_hash = hashlib.sha1(script_content.encode()).hexdigest()
                scripts.append({
                    'script': {
                        'name': file['name'],
                        'script_body': script_content,
                        'webhook_hash': script_hash  # Changed key to 'webhook_hash'
                    }
                })
    return scripts

def compare_scripts():
    if not TRMM_URL or not TRMM_TOKEN:
        print("Tactical RMM URL or Token not provided")
        return

    api['auth']['url'] = TRMM_URL
    api['auth']['key'] = TRMM_TOKEN

    updated_scripts = []

    trmm_scripts = get_scripts_with_content()
    github_scripts = get_script_hashes()

    if len(trmm_scripts) == 0 or len(github_scripts) == 0:
        print('error getting scripts!')
        return

    gh_list = [s['script']['name'] for s in github_scripts]
    trmm_list = [s['name'] for s in trmm_scripts]
    common_items = list(set(gh_list) & set(trmm_list))

    new_scripts = list(set(gh_list) - set(common_items))

    for c in common_items:
        gh_script = get_gh_script(c, github_scripts)
        trmm_script = get_trmm_script(c, trmm_scripts)

        if gh_script is None or trmm_script is None:
            print('error matching script!')
            continue

        if diff_script(gh_script, trmm_script):
            print(f"{trmm_script['name']} is different!")
            updated_scripts.append((gh_script['script'], trmm_script))
        else:
            print(f"{trmm_script['name']} is same!")
    for g, t in updated_scripts:
        patch_script_from_gh(g, t)
    for n in new_scripts:
        new_script = get_gh_script(n, github_scripts)['script']
        print(f"{new_script['name']} is new!")
        create_script(script_body=None, script_metadata=new_script)
    print('done')

def main(argv):
    global TRMM_TOKEN
    global TRMM_URL
    global GITLAB_TOKEN
    global GITLAB_URL
    global GITLAB_PROJECT_ID

    api_key = None
    base_url = None
    gitlab_token = None
    gitlab_url = None
    gitlab_project_id = None

    try:
        opts, args = getopt.getopt(argv, "hk:u:t:g:p:")
    except getopt.GetoptError:
        print('usage: -h (help) -k <api key> -u <tactical rmm URL> -t <gitlab token> -g <gitlab url> -p <gitlab project id>')
        sys.exit(2)

    for opt, arg in opts:
        if opt == '-h':
            print('usage: -h (help) -k <api key> -u <tactical rmm URL> -t <gitlab token> -g <gitlab url> -p <gitlab project id>')
            sys.exit()
        elif opt == '-k':
            api_key = arg
        elif opt == '-u':
            base_url = arg
        elif opt == '-t':
            gitlab_token = arg
        elif opt == '-g':
            gitlab_url = arg
        elif opt == '-p':
            gitlab_project_id = arg

    if api_key is None or base_url is None or gitlab_token is None or gitlab_url is None or gitlab_project_id is None:
        if not TRMM_URL or not TRMM_TOKEN or not GITLAB_TOKEN or not GITLAB_URL or not GITLAB_PROJECT_ID:
            print('All required parameters are not provided!')
            print('usage: -h (help) -k <api key> -u <tactical rmm URL> -t <gitlab token> -g <gitlab url> -p <gitlab project id>')
            sys.exit(2)
    else:
        api['auth']['url'] = base_url[:-1] if base_url.endswith('/') else base_url
        api['auth']['key'] = api_key
        TRMM_URL = api['auth']['url']
        TRMM_TOKEN = api_key
        GITLAB_TOKEN = gitlab_token
        GITLAB_URL = gitlab_url
        GITLAB_PROJECT_ID = gitlab_project_id

    compare_scripts()

if __name__ == "__main__":
    main(sys.argv[1:])
