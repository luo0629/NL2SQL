from dataclasses import dataclass
import re


_FILLER_PATTERNS = [
    "帮我",
    "请帮我",
    "请问",
    "麻烦",
    "麻烦你",
    "一下",
    "下",
    "哪些",
    "有什么",
    "有哪些",
]
_PUNCTUATION_PATTERN = re.compile(r"[？?！!。；;，,]+")
_SPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class QueryNormalization:
    original_question: str
    normalized_question: str
    removed_fillers: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            "original_question": self.original_question,
            "normalized_question": self.normalized_question,
            "removed_fillers": self.removed_fillers,
        }


class QueryNormalizer:
    def normalize(self, question: str) -> QueryNormalization:
        original_question = question.strip()
        normalized_question = _PUNCTUATION_PATTERN.sub(" ", original_question)
        removed_fillers: list[str] = []

        for filler in sorted(_FILLER_PATTERNS, key=len, reverse=True):
            if filler in normalized_question:
                normalized_question = normalized_question.replace(filler, " ")
                removed_fillers.append(filler)

        normalized_question = _SPACE_PATTERN.sub(" ", normalized_question).strip()
        if not normalized_question:
            normalized_question = original_question

        return QueryNormalization(
            original_question=original_question,
            normalized_question=normalized_question,
            removed_fillers=removed_fillers,
        )
