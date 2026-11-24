"""The news server must find webdemo files in both the git layout and a flattened copy."""
import inspect

from newsreg import chain
from newsreg.chain import load_env, webdemo_dir


def test_webdemo_dir_has_retriever():
    assert (webdemo_dir() / "retrieval_v1.py").is_file()


def test_load_env_searches_root_env():
    env = load_env()
    assert isinstance(env, dict)
    src = inspect.getsource(chain.load_env)
    assert 'REPO / ".env"' in src
    assert 'REPO / "webdemo" / ".env"' in src
