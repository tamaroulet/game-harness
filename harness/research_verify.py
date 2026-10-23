"""調べものの照合役（ADR-004 の K-15、W-123）：調べ役が挙げた主張の出典を取りに行き、抜粋が本文にあるかを確かめる。

**なぜ要るか**: 調べ役（LLM）は、存在しない出典や、出典に無い文を挙げうる。会話に貼られた調べものは、それを見分けられないまま
判断の根拠になった（棚卸しの付記）。ここは LLM を使わず、出典の URL への GET と文字列の照合だけで印を付ける。

印は 4 つ。verified（抜粋が本文にある）・quote_not_found（ページはあるが抜粋が無い）・unreachable（取りに行けない。偽ではなく未照合）・
no_source（URL か抜粋が無い）。fetch は差し替えられる（試験は偽のページで行う）。
"""
import datetime
import hashlib
import html
import re
import unicodedata
import urllib.error
import urllib.request
from html.parser import HTMLParser

STATUSES = ("verified", "quote_not_found", "unreachable", "no_source")
USER_AGENT = "game-harness-research-verify/1 (+https://github.com/tamaroulet/game-harness)"
MAX_BYTES = 5_000_000
_SKIP = {"script", "style", "noscript", "template"}
_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "「": '"', "」": '"',
                         "『": '"', "』": '"', "–": "-", "—": "-", "−": "-", " ": " "})


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in _SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_text(raw):
    """HTML の本文のテキスト（script・style を除く）。HTML でなければそのまま。"""
    if "<" not in raw:
        return html.unescape(raw)
    parser = _Text()
    parser.feed(raw)
    parser.close()
    return " ".join(parser.parts)


def normalize(text):
    """照合の前の正規化：NFKC、引用符とダッシュの統一、空白の畳み込み、大文字小文字の無視。"""
    text = unicodedata.normalize("NFKC", text or "").translate(_QUOTES)
    return re.sub(r"\s+", " ", text).strip().casefold()


def fetch(url, ttl=20):
    """(HTTP の状態, 本文のテキスト, 誤りの文)。取れなければ本文は None。"""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=ttl) as r:   # noqa: S310 - 出典の URL を読むだけ
            body = r.read(MAX_BYTES)
            charset = r.headers.get_content_charset() or "utf-8"
            return r.status, body.decode(charset, "replace"), None
    except urllib.error.HTTPError as e:
        return e.code, None, f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return None, None, " ".join(str(e).split())[:200]


def verify(claim, fetcher=fetch, now=None):
    """主張 1 件の照合の結果。{status, http_status, fetched_at, sha256, reason}。"""
    url, quote = str(claim.get("source_url") or "").strip(), str(claim.get("quote") or "").strip()
    at = (now or datetime.datetime.now().astimezone()).isoformat(timespec="seconds")
    if not url.startswith(("http://", "https://")) or not quote:
        return {"status": "no_source", "http_status": None, "fetched_at": None, "sha256": None,
                "reason": "URL が http(s) でないか、抜粋が空"}
    code, body, err = fetcher(url)
    if body is None:
        return {"status": "unreachable", "http_status": code, "fetched_at": at, "sha256": None, "reason": err}
    text = html_text(body)
    found = normalize(quote) in normalize(text)
    return {"status": "verified" if found else "quote_not_found", "http_status": code, "fetched_at": at,
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            "reason": None if found else "抜粋が本文に無い（作り話・ページの変更・言い換えのどれか）"}


def verify_all(claims, fetcher=fetch, now=None):
    """主張の列と同じ順の照合の結果の列。同じ URL は 1 回だけ取りに行く。"""
    cache = {}

    def once(url):
        if url not in cache:
            cache[url] = fetcher(url)
        return cache[url]

    return [verify(c, once, now) for c in claims]
