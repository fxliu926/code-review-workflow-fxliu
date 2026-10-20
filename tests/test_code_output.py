"""测试编码 / 修复两个 Agent 的输出清洗。

踩过的坑：让 LLM 自由发挥时，它会把 Markdown 讲解和 ``` 围栏一起吐出来，
这些解说文字被当成代码写进 state["code"]，后续审查与修复全在审一段"说明书"。
"""
import pytest

import agents.coder as coder_mod
import agents.fixer as fixer_mod
from agents.coder import coder_agent
from agents.fixer import fixer_agent
from agents.prompt import strip_code_block


@pytest.fixture
def mock_llm(monkeypatch):
    def _set(response, target_module=coder_mod):
        monkeypatch.setattr(target_module.client, "chat", lambda messages: response)
    return _set


@pytest.mark.parametrize("raw,expected", [
    ("```python\ndef f(): pass\n```", "def f(): pass"),
    ("```\ndef f(): pass\n```", "def f(): pass"),
    ("def f(): pass", "def f(): pass"),
    ("```python\ndef a(): pass\n```\n```python\ndef b(): pass\n```", "def a(): pass"),
    ("", ""),
])
def test_strip_code_block(raw, expected):
    assert strip_code_block(raw) == expected


def test_coder_strips_fence_and_records_version(mock_llm):
    mock_llm("```python\n# -*- coding: utf-8 -*-\ndef f(): pass\n```")
    state = coder_agent({"requirement": "写个函数"})

    assert state["code"] == "# -*- coding: utf-8 -*-\ndef f(): pass"
    assert "```" not in state["code"]
    assert state["versions"][0]["source"] == "coder"


def test_fixer_strips_fence(monkeypatch):
    monkeypatch.setattr(fixer_mod.client, "chat",
                        lambda messages: "```python\ndef fixed(): pass\n```")
    state = fixer_agent({"code": "def f(): pass", "review": "缺异常处理", "round": 1})

    assert state["code"] == "def fixed(): pass"
    assert "```" not in state["code"]
