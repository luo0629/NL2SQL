from app.agent.nodes import finalize_response, query_understanding
from app.rag.query_normalizer import QueryNormalizer


class StubLLMService:
    def build_chat_model(self):
        return None


def test_query_normalizer_removes_generic_fillers_and_preserves_original() -> None:
    result = QueryNormalizer().normalize("帮我查一下 最近一个月的订单有哪些？")

    assert result.original_question == "帮我查一下 最近一个月的订单有哪些？"
    assert result.normalized_question == "查 最近一个月的订单"
    assert "帮我" in result.removed_fillers
    assert "一下" in result.removed_fillers
    assert "有哪些" in result.removed_fillers


def test_query_understanding_includes_query_normalization() -> None:
    state = query_understanding(
        {"question": "请问 销售额最高的前10个商品有哪些？"},
        StubLLMService(),
    )

    assert state["query_normalization"]["original_question"] == "请问 销售额最高的前10个商品有哪些？"
    assert state["normalized_question"] == "销售额最高的前10个商品"
    assert state["query_understanding"]["normalized_question"] == "销售额最高的前10个商品"
    assert state["query_understanding"]["original_question"] == "请问 销售额最高的前10个商品有哪些？"


def test_finalize_response_exposes_normalization_debug_trace() -> None:
    state = finalize_response(
        {
            "question": "帮我查一下订单？",
            "normalized_question": "查订单",
            "query_normalization": {
                "original_question": "帮我查一下订单？",
                "normalized_question": "查订单",
                "removed_fillers": ["帮我", "一下"],
            },
            "query_understanding": {"intent": "select"},
            "status": "ready",
        }
    )

    debug = state["debug_trace"]
    assert debug["normalized_question"] == "查订单"
    assert debug["query_normalization"]["removed_fillers"] == ["帮我", "一下"]
