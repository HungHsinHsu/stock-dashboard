"""每個 core/ 與 jobs/ 模組都要 import 得起來。

存在的理由：改共用常數/函式名稱時，很容易漏掉某個只在排程或診斷用到的引用——那種
模組平常沒有測試會碰到，壞了也不會紅燈，要等排程半夜跑掛掉、或網頁整頁 ImportError
才發現。實例：core.data 的 TAIFEX_SOURCES 改名成 TAIFEX_URL/TAIFEX_SESSIONS 後，
jobs/diag.py 的 import 沒跟著改，231 個測試全綠但那支 job 一跑就死。

只驗 import（模組層級的名稱都解析得到），不執行任何 run()，所以不碰網路與 DB。
app.py 不在這裡——它要 streamlit，留給部署環境。
"""
import importlib
import pkgutil

import pytest

PACKAGES = ("core", "jobs")


def _module_names():
    names = []
    for pkg in PACKAGES:
        names += [f"{pkg}.{m.name}" for m in pkgutil.iter_modules([pkg])]
    return sorted(names)


@pytest.mark.parametrize("name", _module_names())
def test_module_imports(name):
    importlib.import_module(name)


def test_found_modules_in_both_packages():
    """防呆：路徑抓錯導致一個模組都沒掃到時，上面的參數化會變成空集合、假性全綠。"""
    names = _module_names()
    for pkg in PACKAGES:
        assert any(n.startswith(pkg + ".") for n in names), f"{pkg} 沒掃到任何模組"
