"""插件内通用的常量（HTTP 状态码、超时等）。

各模块统一从这里取，别自己定义一份。
"""

HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_SERVER_ERROR = 500
HTTP_TOO_MANY_REQUESTS = 429
HTTP_BAD_GATEWAY = 502
HTTP_SERVICE_UNAVAILABLE = 503
HTTP_GATEWAY_TIMEOUT = 504

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/151.0.0.0 Safari/537.36"
)

HTTP_TIMEOUT = 10
HTTP_RETRY_ATTEMPTS = 3
HTTP_RETRY_BACKOFF = 0.5
HTTP_RETRY_STATUS_CODES = frozenset(
    {
        HTTP_TOO_MANY_REQUESTS,
        HTTP_SERVER_ERROR,
        HTTP_BAD_GATEWAY,
        HTTP_SERVICE_UNAVAILABLE,
        HTTP_GATEWAY_TIMEOUT,
    }
)

# 查询源和渲染源都必须在有限时间内失败，不能拖住整个 matcher。
SOURCE_LOOKUP_TIMEOUT = 5
# 内存索引源和网络源必须用**两个**线程池。共用一个池时"一个源一个 worker"的
# 承诺只在没有任何并发时才成立：几次慢搜索就能把所有 worker 占满，之后连纯内存
# 的 NLW / plat 都要排队，于是所有源一起错过 deadline、调用方判定"全部不可用"，
# 表现为无论搜什么都报 GDDL 无响应。分池之后网络源再慢也挤不到索引源。
SEARCH_INDEX_WORKERS = 4
SEARCH_NETWORK_WORKERS = 4
