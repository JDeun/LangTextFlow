from .models import GlossaryEntry

CHURCH_GLOSSARY: list[GlossaryEntry] = [
    GlossaryEntry(
        term="요한복음",
        aliases=["요한 보금", "요한 복음"],
        translations={"en": "Gospel of John"},
        category="bible-book",
        boost=2.0,
    ),
    GlossaryEntry(
        term="로마서",
        aliases=["로마 써"],
        translations={"en": "Romans"},
        category="bible-book",
        boost=2.0,
    ),
    GlossaryEntry(
        term="성령",
        aliases=["성녕", "성영"],
        translations={"en": "Holy Spirit"},
        category="theology",
        boost=1.5,
    ),
    GlossaryEntry(
        term="칭의",
        aliases=["칭이", "칭위"],
        translations={"en": "justification"},
        category="theology",
        boost=1.5,
    ),
    GlossaryEntry(
        term="성화",
        translations={"en": "sanctification"},
        category="theology",
        boost=1.5,
    ),
    GlossaryEntry(
        term="속죄",
        aliases=["속재", "속제"],
        translations={"en": "atonement"},
        category="theology",
        boost=1.5,
    ),
    GlossaryEntry(
        term="십자가",
        aliases=["십자 가"],
        translations={"en": "cross"},
        category="theology",
        boost=1.5,
    ),
    GlossaryEntry(
        term="복음",
        translations={"en": "gospel"},
        category="theology",
        boost=1.5,
    ),
]
