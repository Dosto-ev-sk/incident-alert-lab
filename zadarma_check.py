"""Read-only Zadarma account check. Never originates calls or changes the account."""
import base64
import hashlib
import hmac
import os
import re
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv


def authorization(key, secret, method, parameters):
    query = urlencode(sorted(parameters.items()))
    message = method + query + hashlib.md5(query.encode()).hexdigest()
    digest = hmac.new(secret.encode(), message.encode(), hashlib.sha1).hexdigest()
    return key + ':' + base64.b64encode(digest.encode()).decode()


def read_account(method, parameters=None):
    if method not in ('/v1/info/balance/', '/v1/info/price/', '/v1/sip/', '/v1/pbx/internal/',
                      '/v1/pbx/ivr/', '/v1/pbx/ivr/scenario/'):
        raise ValueError('Only read-only account checks are permitted.')
    parameters = parameters or {}
    key, secret = os.getenv('ZADARMA_KEY', ''), os.getenv('ZADARMA_SECRET', '')
    if not key or not secret:
        print('Missing local Zadarma credentials.')
        return None
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.get('https://api.zadarma.com' + method,
                                   params=sorted(parameters.items()),
                                   headers={'Authorization': authorization(key, secret, method, parameters)},
                                   timeout=(5, 15), allow_redirects=False)
        print(f'{method}: HTTP {response.status_code}')
        if response.status_code != 200:
            print('Account check rejected. No call attempted; no raw response logged.')
            return None
        body = response.json()
        if body.get('status') != 'success':
            print('Provider returned an account error. No call attempted; no raw response logged.')
            return None
        return body
    except Exception:
        print('Account check could not complete (network or response error). No call attempted.')
        return None


def main():
    load_dotenv(Path(__file__).resolve().parent / '.env')
    body = read_account('/v1/info/balance/')
    if body is None:
        return
    balance = body.get('balance')
    currency = body.get('currency')
    if isinstance(balance, (int, float)) and isinstance(currency, str) and currency.isalpha() and len(currency) <= 5:
        print(f'Authenticated. Balance: {balance} {currency}')
    else:
        print('Authenticated. Balance fields omitted.')
    for variable, label in [('ALERT_TO_NUMBER', 'Initial destination'), ('ALERT_BACKUP_NUMBER', 'Backup destination')]:
        number = os.getenv(variable, '').lstrip('+')
        if not number.isdigit():
            continue
        price = read_account('/v1/info/price/', {'number': number})
        if price:
            price = price.get('info', price)
            for field in ('price', 'currency', 'billing'):
                value = price.get(field)
                if isinstance(value, (int, float)):
                    print(f'{label} {field}: {value}')
                elif field == 'currency' and isinstance(value, str) and value.isalpha() and len(value) <= 5:
                    print(f'{label} {field}: {value}')
                elif field == 'price' and isinstance(value, str) and re.fullmatch(r'\d{1,5}(\.\d{1,8})?', value):
                    print(f'{label} {field}: {value}')
            print(f'{label}: pricing lookup accepted; calling eligibility not proven.')
    sip = read_account('/v1/sip/')
    if sip:
        items = sip.get('sips', [])
        print(f'SIP account count: {len(items) if isinstance(items, list) else "unavailable"}')
    pbx = read_account('/v1/pbx/internal/')
    if pbx:
        print('PBX extension lookup accepted.')


if __name__ == '__main__':
    main()
