"""
外部APIへのリクエストの簡易リトライ処理。

2026-09-10追加: FREDが一時的に502 Bad Gatewayを返した際、main.pyの日次実行が
そこで例外終了し、記事下書きが一切生成されない不具合が発生した（他の指標・記事本体は
FREDの障害と無関係のはずなのに、たった1系列の一時的な障害で1日分の記事が丸ごと
失われた）。5xx・タイムアウト・接続エラーは再試行すれば復旧することが多いため、
日次実行の中核（FRED・BLS・FRB発表・FX/CFTC）のリクエストに簡易リトライを挟む。
4xx（リクエスト自体の誤り）は再試行しても直らないためリトライしない。
"""

import time
import requests

DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_SEC = 3


def request_with_retry(method, url, max_retries=DEFAULT_MAX_RETRIES,
                        backoff_sec=DEFAULT_BACKOFF_SEC, **kwargs):
    """
    requests.request()のラッパー。5xxエラー・タイムアウト・接続エラーの場合のみ、
    backoff_sec * (試行回数)秒待ってから最大max_retries回まで再試行する。
    4xxはそのまま返す（呼び出し側のresp.raise_for_status()に判断を委ねる）。
    """
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = requests.request(method, url, **kwargs)
            if resp.status_code >= 500:
                resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < max_retries - 1:
                time.sleep(backoff_sec * (attempt + 1))
    raise last_exc


def get_with_retry(url, **kwargs):
    return request_with_retry("GET", url, **kwargs)


def post_with_retry(url, **kwargs):
    return request_with_retry("POST", url, **kwargs)
